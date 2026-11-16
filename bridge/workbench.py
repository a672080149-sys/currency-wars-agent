"""向小克工作台（127.0.0.1:8765）的 AI 面板汇报：操作日志、思考、计划。

由工作台启动 bridge 时会传入 XK_URL / XK_TOKEN；否则从 ../小克工作台/token.txt 读令牌。
工作台没开时静默跳过，绝不阻塞游戏操作。
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get("XK_URL", "http://127.0.0.1:8765").rstrip("/")


def _token():
    tok = os.environ.get("XK_TOKEN")
    if tok:
        return tok
    try:
        with open(os.path.join(ROOT, "小克工作台", "token.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _post(body):
    tok = _token()
    if not tok:
        return
    req = urllib.request.Request(URL + "/api/status", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=1.5).read()
    except Exception:
        pass


def send(body, wait=False):
    if wait:
        _post(body)
    else:
        threading.Thread(target=_post, args=(body,), daemon=True).start()


def note(text, reason=None, mood="running"):
    send({"note": text, "reason": reason, "mood": mood})


# 只读/高频接口不刷日志
QUIET = {"status", "observe", "logs", "board_quick", "summary", "abort", "say", "config"}
NAMES = {"collect": "捡球", "buy": "买卡", "roll": "搜卡", "exp": "升级", "sell": "卖角色", "move": "换位", "equip": "上装备",
         "sell_items": "卖装备", "combine": "合装备", "craft": "推荐合成", "encounter": "选遭遇", "halo": "主角光环",
         "fight": "出战", "tap": "点击", "drag": "拖动", "key": "按键", "board": "读棋盘", "advise": "建议", "step": "单步",
         "run": "自动推进", "home": "回主页", "launch": "进入货币战争", "reward": "领奖励", "reset": "重置"}


def _brief(body):
    body = {k: v for k, v in (body or {}).items() if k not in ("reason", "settle", "observe", "texts", "image")}
    s = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body else ""
    return s if len(s) <= 120 else s[:117] + "..."


def report(name, body, result=None, error=None):
    if name in QUIET:
        return
    text = f"{NAMES.get(name, name)} {_brief(body)}".strip()
    reason = (body or {}).get("reason")
    if error:
        note(f"{text} 失败: {error}"[:300], reason, "oops")
    else:
        note(text, reason, "writing" if name in ("key",) else "running")


def record(action="status", fps=60):
    """控制工作台录像：start / stop / status。返回工作台的结果；工作台没开时返回 error。"""
    tok = _token()
    if not tok:
        return {"error": "找不到小克工作台令牌（工作台没开过？）"}
    req = urllib.request.Request(URL + "/api/record", data=json.dumps({"action": action, "fps": fps}).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"error": error.read().decode("utf-8", "replace")}
    except Exception as error:
        return {"error": f"连不上小克工作台: {error}"}
