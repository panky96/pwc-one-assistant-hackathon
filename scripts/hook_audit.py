"""PostToolUse hook: append an audit line (path + tool only, no content) to second-brain/session-audit.log."""
import json, sys, time
d = json.load(sys.stdin)
with open("second-brain/session-audit.log", "a") as f:
    f.write(f"{time.strftime('%FT%T')} {d.get('tool_name')} {d.get('tool_input', {}).get('file_path', '')}\n")
