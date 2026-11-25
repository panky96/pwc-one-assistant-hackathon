"""Starts the API (multi-worker), runs k6 against it, writes evals/results/load.json.
Local capacity run only (AUTH_ENABLED=false infrastructure-capacity mode); NOT production certification.
Usage: python scripts/run_load.py [MODE] [RATE] [DURATION]"""
import json, os, socket, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
mode, rate, dur = (sys.argv + [None] * 4)[1:4]
mode, rate, dur = mode or "load", rate or "300", dur or "20s"
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
env = {**os.environ, "APP_ENV": "development", "AUTH_ENABLED": "false", "OPENROUTER_API_KEY": "", "DATABASE_URL": ""}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--port", str(port), "--workers", "4", "--log-level", "warning"],
                       cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(100):
        try: socket.create_connection(("127.0.0.1", port), .2).close(); break
        except OSError: time.sleep(.2)
    out = ROOT / "evals" / "results" / ("load.json" if mode in ("smoke", "load") else f"{mode}.json")
    out.parent.mkdir(exist_ok=True)
    r = subprocess.run(["k6", "run", "-q", "-e", f"MODE={mode}", "-e", f"RATE={rate}", "-e", f"DURATION={dur}", "-e", "MAX_VUS=400",
                        "-e", f"BASE=http://127.0.0.1:{port}", "-e", f"SUMMARY={out}", str(ROOT / "load_tests" / "k6_arrival_rate.js")], cwd=ROOT)
    print("k6 exit code", r.returncode, "(non-zero = a threshold failed)")
finally:
    srv.terminate()
