import json
import os

from openplan.core.ports import MeshBaseline


class MeshAdapter:
    def __init__(self, api_url: str = "", api_key: str = ""):
        self.api_url = api_url
        self.api_key = api_key
        self.cached_baselines: list[MeshBaseline] = []

    async def push_checkpoints(self, checkpoints: list[dict]) -> bool:
        if not self.api_url or not self.api_key:
            return False
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.api_url}/v1/checkpoints",
                    json={"checkpoints": checkpoints},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=10,
                )
                return resp.status_code == 202
        except Exception:
            return False

    async def pull_baselines(self) -> list[MeshBaseline]:
        if not self.api_url or not self.api_key:
            return self.cached_baselines
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.api_url}/v1/baselines",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    baselines = [
                        MeshBaseline(
                            match_level=b.get("match_level", "action"),
                            action=b.get("action", ""),
                            phase_label_tokens=b.get("phase_label_tokens", ""),
                            avg_cost=b.get("avg_cost", 0),
                            ci_lo=b.get("ci_lo", 0),
                            ci_hi=b.get("ci_hi", 0),
                            sample_count=b.get("sample_count", 0),
                            success_rate=b.get("success_rate", 0),
                        )
                        for b in data.get("baselines", [])
                    ]
                    self.cached_baselines = baselines
                    return baselines
        except Exception:
            pass
        return self.cached_baselines

    def sync_pending(self, conn) -> int:
        """Sync unsynced calibration events. Returns count synced."""
        if not self.api_url or not self.api_key:
            return 0
        pending = conn.execute(
            "SELECT * FROM calibration_events WHERE synced = 0 LIMIT 100"
        ).fetchall()
        if not pending:
            return 0

        checkpoints = []
        for p in pending:
            checkpoints.append({
                "action": p["action"],
                "phase_label_tokens": p["phase_label_tokens"],
                "expected_cost": p["expected_cost"],
                "actual_cost": p["actual_cost"],
                "outcome": p["outcome"],
            })

        import httpx
        try:
            import httpx as _httpx
            resp = _httpx.post(
                f"{self.api_url}/v1/checkpoints",
                json={"checkpoints": checkpoints},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            if resp.status_code == 202:
                conn.executemany(
                    "UPDATE calibration_events SET synced = 1 WHERE id = ?",
                    [(p["id"],) for p in pending],
                )
                conn.commit()
                return len(pending)
        except Exception:
            pass
        return 0
