"""PreToolUse hook: block writes to secrets/.env and destructive shell patterns (exit 2 = block)."""
import json, re, sys
d = json.load(sys.stdin); ti = d.get("tool_input", {})
target = ti.get("file_path", "") or ti.get("command", "")
if re.search(r"(^|/)\.env($|\s)", target) and d.get("tool_name") in ("Edit", "Write"):
    print("Blocked: .env holds secrets; edit manually.", file=sys.stderr); sys.exit(2)
if re.search(r"rm\s+-rf\s+/|git\s+push\s+--force|DROP\s+TABLE", target, re.I):
    print("Blocked: destructive command.", file=sys.stderr); sys.exit(2)
