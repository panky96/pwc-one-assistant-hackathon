"""PostToolUse hook: append an audit line (path + tool only, no content) to logs/session-audit.log."""
import json, sys, time
d = json.load(sys.stdin)
import os
os.makedirs("logs", exist_ok=True)
with open("logs/session-audit.log", "a") as f:
    f.write(f"{time.strftime('%FT%T')} {d.get('tool_name')} {d.get('tool_input', {}).get('file_path', '')}\n")
