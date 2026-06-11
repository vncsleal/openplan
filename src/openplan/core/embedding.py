from __future__ import annotations

import concurrent.futures
import logging
import sqlite3
import threading
from typing import Any

_log = logging.getLogger(__name__)

import numpy as np

_EMBEDDING_DIMENSIONS: int = 384


class EmbeddingProvider:
    """Wraps fastembed.TextEmbedding, runs encode calls in a thread pool executor."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2", batch_size: int = 32) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._model: Any = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._loaded = False

    def warmup(self) -> None:
        """Load the model and create executor. Idempotent."""
        if self._loaded:
            return
        try:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                model_name=self._model_name,
                max_length=256,
                batch_size=self._batch_size,
                cache_dir=None,
            )
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            self._loaded = True
        except ImportError:
            self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def encode(self, texts: list[str]) -> np.ndarray | None:
        """Encode a list of texts. Returns (N, dims) array or None if not loaded."""
        if not self._loaded:
            _log.warning("encode called but provider not loaded")
            return None
        if not texts:
            return None
        if self._executor is None:
            _log.warning("encode called but executor not initialized")
            return None

        future = self._executor.submit(self._sync_encode, texts)
        try:
            return future.result()
        except Exception as exc:
            _log.warning("encode failed: %s", exc)
            return None

    def _sync_encode(self, texts: list[str]) -> np.ndarray:
        results: list[np.ndarray] = list(self._model.embed(texts))
        return np.array(results, dtype=np.float32)

    def shutdown(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=False)


class EmbeddingCache:
    """In-memory NumPy embedding cache with lazy load and incremental refresh.

    When sqlite-vec is available on the connection and the cache exceeds
    *_ann_threshold*, queries use the vec0 ANN index instead of brute-force
    cosine similarity.
    """

    def __init__(self, provider: EmbeddingProvider, ann_threshold: int = 1000) -> None:
        self._provider = provider
        self._matrix: np.ndarray | None = None
        self._index: dict[str, int] = {}
        self._reverse_index: list[str] = []
        self._version: int = 0
        self._ann_threshold = ann_threshold

    def _vec_to_blob(self, v: np.ndarray) -> bytes:
        return v.astype(np.float32).tobytes()

    @staticmethod
    def _vec0_available(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute("SELECT 1 FROM vec_embeddings LIMIT 0")
            return True
        except Exception:
            return False

    def _sync_vec0(self, conn: sqlite3.Connection) -> None:
        if self._matrix is None or not self._vec0_available(conn):
            return
        try:
            conn.execute("BEGIN")
        except sqlite3.OperationalError:
            conn.execute("DELETE FROM vec_embeddings")
            for i in range(self._matrix.shape[0]):
                blob = self._vec_to_blob(self._matrix[i])
                conn.execute(
                    "INSERT INTO vec_embeddings(rowid, embedding) VALUES (?, ?)",
                    (i, blob),
                )
            return
        try:
            conn.execute("DELETE FROM vec_embeddings")
            for i in range(self._matrix.shape[0]):
                blob = self._vec_to_blob(self._matrix[i])
                conn.execute(
                    "INSERT INTO vec_embeddings(rowid, embedding) VALUES (?, ?)",
                    (i, blob),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _query_vec0(
        self, query_blob: bytes, conn: sqlite3.Connection, top_k: int
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT rowid, distance FROM vec_embeddings "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (query_blob, top_k),
        ).fetchall()

        id_to_label: dict[str, str] = {}
        id_to_activation: dict[str, float] = {}
        id_to_frontier: dict[str, int] = {}
        for r in conn.execute("SELECT id, label, activation, frontier FROM nodes").fetchall():
            id_to_label[r["id"]] = r["label"]
            id_to_activation[r["id"]] = r["activation"]
            id_to_frontier[r["id"]] = r["frontier"]

        results: list[dict[str, Any]] = []
        for row in rows:
            idx = row["rowid"]
            if idx < 0 or idx >= len(self._reverse_index):
                continue
            sid = self._reverse_index[idx]
            results.append({
                "id": sid,
                "label": id_to_label.get(sid, ""),
                "activation": id_to_activation.get(sid, 0.0),
                "frontier": id_to_frontier.get(sid, 0),
                "similarity": 1.0 - row["distance"],
            })
        return results

    def refresh(self, conn: sqlite3.Connection) -> None:
        """Lazy-load or incrementally refresh the cache."""
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM nodes"
        ).fetchone()["cnt"]

        if total == 0:
            self._matrix = None
            self._index = {}
            self._reverse_index = []
            self._version = 0
            return

        if self._matrix is None or self._version == 0:
            rows = conn.execute(
                "SELECT id, label, COALESCE(props, '{}') AS props FROM nodes ORDER BY rowid"
            ).fetchall()
            self._full_load(rows, conn)
        elif total > self._version:
            rows = conn.execute(
                "SELECT id, label, COALESCE(props, '{}') AS props FROM nodes ORDER BY rowid LIMIT ? OFFSET ?",
                (total - self._version, self._version),
            ).fetchall()
            self._incremental_load(rows, conn)

    def _full_load(self, rows: list[sqlite3.Row], conn: sqlite3.Connection | None = None) -> None:
        self._index = {}
        texts: list[str] = []
        ids: list[str] = []

        for r in rows:
            tid = r["id"]
            label = r["label"] or ""
            texts.append(label)
            ids.append(tid)

        encoded = self._provider.encode(texts)
        if encoded is not None and encoded.shape == (len(ids), _EMBEDDING_DIMENSIONS):
            self._matrix = encoded
            self._reverse_index = ids
            for i, tid in enumerate(ids):
                self._index[tid] = i
            self._version = len(ids)
            if conn is not None:
                self._sync_vec0(conn)
        else:
            if encoded is not None:
                _log.warning("_full_load: shape %s != (%d, %d)", encoded.shape, len(ids), _EMBEDDING_DIMENSIONS)
            self._matrix = None
            self._index = {}
            self._reverse_index = []
            self._version = 0

    def _incremental_load(self, rows: list[sqlite3.Row], conn: sqlite3.Connection | None = None) -> None:
        if not rows:
            return
        texts = [(r["label"] or "") for r in rows]
        ids = [r["id"] for r in rows]

        encoded = self._provider.encode(texts)
        if encoded is None:
            return
        if encoded.shape != (len(ids), _EMBEDDING_DIMENSIONS):
            _log.warning("_incremental_load: shape %s != (%d, %d)", encoded.shape, len(ids), _EMBEDDING_DIMENSIONS)
            return

        old_n = self._matrix.shape[0] if self._matrix is not None else 0
        new_n = old_n + encoded.shape[0]
        new_matrix = np.zeros((new_n, _EMBEDDING_DIMENSIONS), dtype=np.float32)
        if old_n > 0:
            new_matrix[:old_n] = self._matrix
        new_matrix[old_n:] = encoded
        self._matrix = new_matrix
        self._reverse_index.extend(ids)
        for i, tid in enumerate(ids):
            self._index[tid] = old_n + i
        self._version += len(ids)
        if conn is not None:
            self._sync_vec0(conn)

    def query(self, text: str, conn: sqlite3.Connection, top_k: int = 5) -> list[dict[str, Any]]:
        """Find top_k nearest states by embedding similarity."""
        self.refresh(conn)

        if self._matrix is None or self._matrix.shape[0] == 0:
            return []

        query_emb = self._provider.encode([text])
        if query_emb is None:
            return []

        qv = query_emb[0]
        query_blob = self._vec_to_blob(qv)

        if (
            self._version >= self._ann_threshold
            and self._vec0_available(conn)
            and query_blob is not None
        ):
            return self._query_vec0(query_blob, conn, top_k)

        norms = np.linalg.norm(self._matrix, axis=1)
        dot = np.dot(self._matrix, qv)
        denom = norms * np.linalg.norm(qv)
        similarities = np.divide(dot, denom, out=np.zeros_like(dot), where=denom != 0)

        if len(similarities) <= top_k:
            top_indices = np.argsort(similarities)[::-1]
        else:
            top_indices = np.argpartition(similarities, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        result_ids = [
            self._reverse_index[idx]
            for idx in top_indices
            if idx < len(self._reverse_index)
        ]
        id_info: dict[str, dict[str, Any]] = {}
        if result_ids:
            placeholders = ",".join("?" * len(result_ids))
            for r in conn.execute(
                f"SELECT id, label, activation, frontier FROM nodes WHERE id IN ({placeholders})",
                result_ids,
            ).fetchall():
                id_info[r["id"]] = dict(r)

        results: list[dict[str, Any]] = []
        for idx in top_indices:
            if idx >= len(similarities) or idx >= len(self._reverse_index):
                continue
            sid = self._reverse_index[idx]
            info = id_info.get(sid, {})
            results.append({
                "id": sid,
                "label": info.get("label", ""),
                "activation": info.get("activation", 0.0),
                "frontier": info.get("frontier", 0),
                "similarity": float(similarities[idx]),
            })
        return results


_provider: EmbeddingProvider | None = None
_cache: EmbeddingCache | None = None
_embedding_lock = threading.Lock()


def get_provider() -> EmbeddingProvider:
    global _provider
    with _embedding_lock:
        if _provider is None:
            _provider = EmbeddingProvider()
        return _provider


def get_cache() -> EmbeddingCache:
    global _cache, _provider
    with _embedding_lock:
        if _cache is None:
            if _provider is None:
                _provider = EmbeddingProvider()
            _cache = EmbeddingCache(_provider)
        return _cache


def warmup_embeddings() -> None:
    """Warm up the embedding model. Safe to call multiple times."""
    provider = get_provider()
    provider.warmup()


def shutdown_embeddings() -> None:
    global _provider, _cache
    with _embedding_lock:
        if _provider is not None:
            _provider.shutdown()
            _provider = None
        _cache = None


def set_provider(provider: EmbeddingProvider | None) -> None:
    """Inject a test provider. Clears cache."""
    global _provider, _cache
    with _embedding_lock:
        if _provider is not None and _provider is not provider:
            _provider.shutdown()
        _provider = provider
        _cache = None


def reset_embeddings() -> None:
    """Reset all embedding singletons (for test isolation)."""
    global _provider, _cache
    with _embedding_lock:
        if _provider is not None:
            _provider.shutdown()
        _provider = None
        _cache = None
