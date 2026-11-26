"""MCP server `k6` (stdio): run load tests from chat, bounded by policy.
Only hosts in K6_ALLOWED_HOSTS, only modes smoke|load|isolation, rate and duration capped. Soak is not exposed."""
import json, os, shutil, subprocess, tempfile
from pathlib import Path
from urllib.parse import urlparse
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_HOSTS = {h for h in os.getenv("K6_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h}
MAX_RATE = int(os.getenv("K6_MAX_RATE", "1000"))
MAX_SECONDS = 600
mcp = FastMCP("k6")


@mcp.tool()
def run_load_test(base_url: str, mode: str = "smoke", rate: int = 50, duration_s: int = 30) -> dict:
    """Run the arrival-rate k6 scenario against an allowlisted host and return its summary."""
    if mode not in ("smoke", "load", "isolation"):
        raise ValueError("mode must be smoke, load or isolation")
    host = urlparse(base_url).hostname
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"host {host!r} is not in K6_ALLOWED_HOSTS")
    if not (1 <= rate <= MAX_RATE) or not (1 <= duration_s <= MAX_SECONDS):
        raise ValueError("rate or duration out of bounds")
    if shutil.which("k6") is None:
        raise RuntimeError("k6 is not installed on this host")
    out_dir = Path(tempfile.mkdtemp(prefix="k6-"))
    out = out_dir / "summary.json"
    r = subprocess.run(["k6", "run", "-q", "-e", f"MODE={mode}", "-e", f"RATE={rate}", "-e", f"DURATION={duration_s}s",
                        "-e", "MAX_VUS=400", "-e", f"BASE={base_url}", "-e", f"SUMMARY={out}",
                        str(ROOT / "load_tests" / "k6_arrival_rate.js")],
                       capture_output=True, text=True, timeout=MAX_SECONDS + 120)
    summary = json.loads(out.read_text()) if out.exists() else None
    return {"exit_code": r.returncode, "summary": summary, "threshold_failed": r.returncode == 99}


if __name__ == "__main__":
    mcp.run(transport="stdio")
