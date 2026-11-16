"""命令行入口：python bridge/cw.py <endpoint> [key=value ...]

例：
  python bridge/cw.py status
  python bridge/cw.py observe
  python bridge/cw.py tap x=960 y=540
  python bridge/cw.py run stop_pages=CHOICE,HALO max_seconds=200
  python bridge/cw.py launch mode=normal difficulty=current
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import BridgeError, call  # noqa: E402

LIST_KEYS = {"stop_pages", "names", "must", "soft", "three_star", "skip", "items", "keep", "columns"}
JSON_KEYS = {"targets", "moves", "target", "want", "pairs"}


def parse(value, key):
    if key in JSON_KEYS or value.startswith("["):
        return json.loads(value)
    if key in LIST_KEYS:
        return [int(v) if v.isdigit() else v for v in value.split(",") if v]
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    body = {}
    for arg in sys.argv[2:]:
        key, _, value = arg.partition("=")
        body[key] = parse(value, key)
    try:
        result = call(sys.argv[1], body)
    except BridgeError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(result, dict) and ("text" in result or "after" in result):
        rest = {k: v for k, v in result.items() if k not in ("text", "after")}
        if rest:
            print(json.dumps(rest, ensure_ascii=False))
        for k in ("text", "after"):
            if result.get(k):
                print(result[k])
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
