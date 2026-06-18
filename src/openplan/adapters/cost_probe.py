import asyncio
import subprocess
import json

from openplan.core.ports import CostProbeResult


class OpenCodeCostProbeAdapter:
    def __init__(self, command: str = "opencode stats --json"):
        self.command = command

    async def get_delta(self, from_timestamp: float, to_timestamp: float) -> CostProbeResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                self.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            data = json.loads(stdout)
            # Parse token count from opencode stats output
            tokens = data.get("total_tokens", 0) or data.get("tokens", {}).get("total", 0)
            return CostProbeResult(tokens=int(tokens))
        except Exception:
            return CostProbeResult(tokens=0)
