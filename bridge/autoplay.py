"""让 M7A 内置逻辑完整打一局，并把每段轨迹、日志、节点状态记录到 bridge/logs/。

python bridge/autoplay.py [mode=overclock] [difficulty=lowest] [resume=false]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import BridgeError, call  # noqa: E402

LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOGS, exist_ok=True)


def main():
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    path = os.path.join(LOGS, time.strftime("match_%Y%m%d_%H%M%S.jsonl"))
    out = open(path, "a", encoding="utf-8")

    def record(kind, data):
        out.write(json.dumps({"t": time.strftime("%H:%M:%S"), "kind": kind, **data}, ensure_ascii=False) + "\n")
        out.flush()

    print("log:", path, flush=True)
    cfg = {"mode": args.get("mode", "overclock"), "difficulty": args.get("difficulty", "lowest")}
    record("config", call("config", cfg))
    started = time.time()
    if args.get("skip_launch") != "true":
        r = call("launch", {**cfg, "resume": args.get("resume") == "true"})
        record("launch", r)
        print("launch ->", r.get("page"), r.get("error", ""), flush=True)
        if "error" in r:
            return 1
    stage = None
    for i in range(80):
        try:
            r = call("run", {"max_seconds": 240, "max_steps": 80, "stop_on_stage_change": True})
        except BridgeError as error:
            record("error", {"error": str(error)})
            print("bridge error:", error, flush=True)
            return 2
        record("run", r)
        if r.get("stage") != stage:
            stage = r.get("stage")
        print(f"[{time.time() - started:6.0f}s] {r.get('reason')} page={r.get('page')} stage={r.get('stage')} "
              f"gold={r.get('gold')} hp={r.get('squad_health')} lv={r.get('level')} result={r.get('result')}",
              flush=True)
        if "error" in r and "reason" not in r:
            print("server error:", r["error"], flush=True)
            return 3
        reason = r.get("reason", "")
        if reason == "home":
            break
        if reason.startswith("error") or reason == "aborted":
            return 4
    record("done", {"seconds": round(time.time() - started)})
    try:
        record("reward", call("reward"))
    except BridgeError:
        pass
    print("done in", round(time.time() - started), "s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
