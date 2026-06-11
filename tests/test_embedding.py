from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import pytest

from openplan.core.embedding import (
    EmbeddingCache,
    EmbeddingProvider,
    get_cache,
    get_provider,
    reset_embeddings,
    set_provider,
    warmup_embeddings,
)
from openplan.db.schema import init_db

_DIM = 384


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic mock that doesn't require fastembed."""

    def __init__(self) -> None:
        self._loaded = False
        self._model = object()
        self._model_name = "mock"
        self._batch_size = 32
        self._executor = ThreadPoolExecutor(max_workers=1)

    def warmup(self) -> None:
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    def encode(self, texts: list[str]) -> np.ndarray | None:
        if not self._loaded or not texts:
            return None
        result = []
        for t in texts:
            seed = (hash(t) & 0x7FFFFFFF) or 1
            rng = np.random.default_rng(seed)
            vec = rng.random(_DIM, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            result.append(vec)
        return np.array(result)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_embeddings()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


# ── Phase 3: test_embedding_provider_load ──

def test_embedding_provider_load() -> None:
    """Model loads, warmup succeeds, returns 384-dim vector."""
    p = MockEmbeddingProvider()
    assert not p.loaded

    p.warmup()
    assert p.loaded

    encoded = p.encode(["hello world"])
    assert encoded is not None
    assert encoded.shape == (1, _DIM)
    assert encoded.dtype == np.float32


# ── Phase 3: test_embedding_provider_thread_pool ──

def test_embedding_provider_thread_pool() -> None:
    """Runs in executor without blocking."""
    p = MockEmbeddingProvider()
    p.warmup()

    future = p._executor.submit(p.encode, ["test"])
    result = future.result(timeout=5)
    assert result is not None
    assert result.shape == (1, _DIM)


# ── Phase 3: test_embedding_cache_lazy_load ──

def test_embedding_cache_lazy_load(conn: sqlite3.Connection) -> None:
    """Matrix loads on first query, not on construction."""
    p = MockEmbeddingProvider()
    p.warmup()
    cache = EmbeddingCache(p)

    assert cache._matrix is None
    assert cache._version == 0

    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000001', 'alpha', 'test')"
    )
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000002', 'beta', 'test')"
    )

    results = cache.query("alpha", conn, top_k=2)

    assert cache._matrix is not None
    assert cache._matrix.shape == (2, _DIM)
    assert cache._version == 2
    assert len(results) > 0
    labels = [r["label"] for r in results]
    assert "alpha" in labels or "beta" in labels


# ── Phase 3: test_embedding_cache_incremental_refresh ──

def test_embedding_cache_incremental_refresh(conn: sqlite3.Connection) -> None:
    """New states are queryable without full reload."""
    p = MockEmbeddingProvider()
    p.warmup()
    cache = EmbeddingCache(p)

    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000001', 'alpha', 'test')"
    )
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000002', 'beta', 'test')"
    )
    cache.query("alpha", conn)
    assert cache._version == 2

    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000003', 'gamma', 'test')"
    )

    results = cache.query("gamma", conn, top_k=3)
    assert cache._version == 3
    assert cache._matrix is not None
    assert cache._matrix.shape == (3, _DIM)
    labels = [r["label"] for r in results]
    assert "gamma" in labels


def test_embedding_cache_vec0_ann(conn: sqlite3.Connection) -> None:
    """cache.query uses ANN via vec0 when above threshold and available."""
    p = MockEmbeddingProvider()
    p.warmup()
    # Set low threshold to force ANN path
    cache = EmbeddingCache(p, ann_threshold=2)

    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000001', 'alpha', 'test')"
    )
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000002', 'beta', 'test')"
    )

    results = cache.query("alpha", conn, top_k=2)
    assert len(results) > 0, "ANN query returned no results"
    assert results[0]["similarity"] >= 0, "Similarity should be >= 0"


# ── Phase 3: test_observe_query_embedding ──

def test_observe_query_embedding(conn: sqlite3.Connection) -> None:
    """observe(query) uses embedding similarity when provider is available."""
    from openplan.core.graph import observe

    config = {
        "activation_threshold": 0.5,
        "activation_weights": {"in_degree": 0.4, "frontier": 0.3, "recency": 0.2, "boost": 0.1},
        "stale_days": 2,
    }

    mock = MockEmbeddingProvider()
    mock.warmup()
    set_provider(mock)

    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000001', 'Implement authentication', 'test')"
    )
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000002', 'Setup database', 'test')"
    )

    result = observe("test", query="login system", scope="frontier", conn=conn, config=config)

    assert result["mode"] == "similarity"
    assert result["method"] == "embedding"
    assert result["count"] >= 1
    assert "states" in result


# ── Phase 3: test_plan_natural_language_target ──

def test_plan_natural_language_target(conn: sqlite3.Connection) -> None:
    """plan resolves non-ID target via embedding similarity."""
    from openplan.core.graph import plan

    config = {
        "activation_threshold": 0.5,
        "activation_weights": {"in_degree": 0.4, "frontier": 0.3, "recency": 0.2, "boost": 0.1},
        "stale_days": 2,
    }

    mock = MockEmbeddingProvider()
    mock.warmup()
    set_provider(mock)

    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000001', 'Implement authentication', 'test')"
    )
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000002', 'Setup database', 'test')"
    )
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES ('S-000003', 'Deploy to production', 'test')"
    )

    conn.execute(
        "INSERT INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES ('S-000001', 'S-000002', 'implement', 10000, 0.1, 0.8)"
    )
    conn.execute(
        "INSERT INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES ('S-000002', 'S-000003', 'deploy', 5000, 0.05, 0.95)"
    )

    result = plan("S-000001", "go to production", conn, config)

    assert result["ok"] is True
    assert "resolved_target" in result
    assert result["resolved_target"]["id"] is not None
    assert result["path"] is not None
