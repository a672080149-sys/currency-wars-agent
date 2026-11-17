"""小克 × DS 双人讨论流程。

DS（WorkBuddy 的 DeepSeek）看局面、提操作意见；小克（Claude）判断后回复；
同意就由 DS 执行，不同意最多再讨论两轮，之后小克拍板。全程发言显示在工作台两个桌宠的气泡里。

用法（在 bridge 目录）：
  python -X utf8 duo.py propose [--note "补充说明/当前想法"]
      读当前局面 → DS 提议（气泡+对话记录）→ 打印局面和提议
  python -X utf8 duo.py reply agree "小克的话" [--actions '[...]']
      同意（可附带改过的操作，否则用 DS 的方案）→ 进入待执行
  python -X utf8 duo.py reply object "小克的意见" [--actions '[...]']
      反对/修改 → DS 回应；最多两轮，两轮后按小克的方案
  python -X utf8 duo.py exec
      DS 执行已达成一致的操作，打印每一步结果和执行后的局面
  python -X utf8 duo.py status
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ds_client  # noqa: E402
from player import to_trailblazer  # noqa: E402
import workbench  # noqa: E402
from client import BridgeError, call  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "duo_state.json")
SYSTEM_TEMPLATE = os.path.join(HERE, "duo_ds_system.md")
LINEUP_FILE = os.path.join(HERE, "duo_lineup.txt")  # 当前阵容名，对应 duo_lineups/<名>.md
SYSTEM = os.path.join(HERE, "duo_state_system.md")  # 合成后的系统提示词（自动生成）


def build_system():
    try:
        name = open(LINEUP_FILE, encoding="utf-8").read().strip() or "firefly"
    except OSError:
        name = "firefly"
    with open(os.path.join(HERE, "duo_lineups", name + ".md"), encoding="utf-8") as f:
        lineup = f.read()
    with open(os.path.join(HERE, "..", "knowledge", "通用对局规则.md"), encoding="utf-8") as f:
        common = f.read()
    with open(SYSTEM_TEMPLATE, encoding="utf-8") as f:
        text = f.read().replace("{LINEUP}", lineup).replace("{COMMON_RULES}", common)
    with open(SYSTEM, "w", encoding="utf-8") as f:
        f.write(text)
    return name
MAX_ROUNDS = 2
SHOP_BTN = (1630, 990)


# ---------------------------------------------------------------- 状态
def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def say(who, kind, text):
    if text:
        workbench.send({"agent": who, "kind": kind, "thought": text}, wait=True)


# ---------------------------------------------------------------- DS
def ask_ds(prompt):
    build_system()
    r = ds_client.ask(prompt, system_file=SYSTEM)
    data = parse_json(r["text"])
    if data is None:  # 再给一次机会：只要 JSON
        r2 = ds_client.ask(prompt + "\n\n（上一次输出不是合法 JSON。只输出一个 JSON 对象。）", system_file=SYSTEM)
        r = {**r2, "seconds": r["seconds"] + r2["seconds"]}
        data = parse_json(r["text"])
    return data, r


def parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except ValueError:
        return None


def look():
    try:
        return call("look", {}).get("text") or ""
    except BridgeError as error:
        return f"（读不到局面：{error}）"


def transcript(st):
    return "\n".join(f"{'DS' if m['who'] == 'ds' else '小克'}【{m['kind']}】{m['text']}"
                     + (f"\n  操作：{json.dumps(m['actions'], ensure_ascii=False)}" if m.get("actions") else "")
                     for m in st.get("history", []))


# ---------------------------------------------------------------- 命令
def cmd_propose(args):
    state_text = look()
    prompt = f"## 当前局面\n{state_text}\n"
    if args.note:
        prompt += f"\n## 小克的补充说明\n{args.note}\n"
    prompt += "\n请看局面，提出这一步的操作意见（提议格式）。"
    data, r = ask_ds(prompt)
    if not data:
        print(json.dumps({"error": "DS 没给出合法 JSON", "raw": r["text"][:800], "seconds": r["seconds"]}, ensure_ascii=False))
        return 1
    st = {"look": state_text, "round": 0, "status": "proposed", "plan": data.get("actions") or [],
          "history": [{"who": "ds", "kind": "提议", "text": data.get("say", ""), "actions": data.get("actions") or [],
                       "reason": data.get("reason", "")}]}
    save(st)
    say("ds", "propose", data.get("say", ""))
    print(state_text)
    print(f"\n[DS 提议 {r['seconds']}s] {data.get('say', '')}\n理由：{data.get('reason', '')}")
    print("操作：" + json.dumps(data.get("actions") or [], ensure_ascii=False))
    return 0


def cmd_reply(args):
    st = load()
    if st.get("status") not in ("proposed", "discussing"):
        print(json.dumps({"error": f"当前没有待讨论的提议（status={st.get('status')}）"}, ensure_ascii=False))
        return 1
    mine = json.loads(args.actions) if args.actions else None
    kind_cn = "同意" if args.kind == "agree" else "反对"
    st["history"].append({"who": "xiaoke", "kind": kind_cn, "text": args.text, "actions": mine or []})
    say("xiaoke", args.kind, args.text)
    if args.kind == "agree":
        st["plan"] = mine if mine is not None else st.get("plan", [])
        st["status"] = "agreed"
        save(st)
        print("已同意，待执行：" + json.dumps(st["plan"], ensure_ascii=False))
        return 0
    st["round"] = st.get("round", 0) + 1
    prompt = (f"## 当前局面\n{st.get('look', '')}\n\n## 到目前为止的讨论\n{transcript(st)}\n\n"
              f"这是第 {st['round']} 轮讨论（最多 {MAX_ROUNDS} 轮）。请回应小克（回复格式）：认同就 agree=true 并给出最终操作；"
              "不认同就说明理由并给出你的方案。")
    data, r = ask_ds(prompt)
    if not data:
        data = {"agree": True, "say": "（DS 没回上来）那就按小克说的来！", "actions": mine or st.get("plan", [])}
    agree = bool(data.get("agree"))
    if data.get("actions") is not None:
        ds_actions = data["actions"]
    else:
        ds_actions = mine if mine is not None else st.get("plan", [])
    if agree and mine is not None:  # DS 同意小克的方案：以小克给出的操作为准（DS 偶尔会把旧方案原样带回来）
        ds_actions = mine
    st["history"].append({"who": "ds", "kind": "同意" if agree else "反对", "text": data.get("say", ""), "actions": ds_actions})
    say("ds", "agree" if agree else "object", data.get("say", ""))
    if agree:
        st["plan"], st["status"] = ds_actions, "agreed"
        verdict = "DS 同意，待执行"
    elif st["round"] >= MAX_ROUNDS:
        st["plan"], st["status"] = (mine if mine is not None else st.get("plan", [])), "agreed"
        say("xiaoke", "decide", "讨论两轮啦，这次听我的，按我的方案来！")
        verdict = "两轮未达成一致，按小克方案，待执行"
    else:
        st["plan"], st["status"] = ds_actions, "discussing"
        verdict = f"DS 不同意（第 {st['round']} 轮），可再回复一轮"
    save(st)
    print(f"[DS 回应 {r['seconds']}s] {'同意' if agree else '反对'}：{data.get('say', '')}")
    print("DS 方案：" + json.dumps(ds_actions, ensure_ascii=False))
    print(verdict + "：" + json.dumps(st["plan"], ensure_ascii=False))
    return 0


SIMPLE_ITEMS = {"轮滑鞋", "折叠小刀", "和平手枪", "幸运星", "生命之花", "量产型装甲", "以太钻头", "光能电池"}


def _norm(n):
    name = (n or "").replace("干冶", "千冶").replace("•", "·").strip()
    if name == "星神☆阿哈":
        return "星神★阿哈"
    return to_trailblazer(name)  # 开拓者显示的是玩家昵称，见 player.py / player.json


def current_lineup():
    try:
        with open(LINEUP_FILE, encoding="utf-8") as f:
            return f.read().strip() or "firefly"
    except OSError:
        return "firefly"


def read_lineup_board():
    state = call("observe", {"texts": False})
    if state.get("page") == "SHOP":
        state = call("tap", {"x": SHOP_BTN[0], "y": SHOP_BTN[1]})
    if state.get("page") != "BOARD":
        raise BridgeError("请先回到备战棋盘，再检查阵容和经济")
    result = call("board", {})
    board = result.get("board") or {}
    if result.get("error") or not board.get("slots"):
        raise BridgeError(result.get("error") or "读不到完整棋盘，停止经济操作")
    for slot in board["slots"]:
        if slot.get("occupied") and not slot.get("blocked"):
            unit = slot.get("unit") or {}
            if _norm(unit.get("name")) == "星神★阿哈":
                continue
            if not unit.get("name") or not isinstance(unit.get("stars"), int) or unit["stars"] < 1:
                raise BridgeError("存在未识别角色或星级，重新识别后再计算购买缺口")
    return board


def lineup_config():
    with open(os.path.join(HERE, "duo_lineups", current_lineup() + ".want.json"), encoding="utf-8") as f:
        return json.load(f)


def lineup_targets(board):
    data = lineup_config()
    target = {_norm(k): int(v) for k, v in data.items() if not k.startswith("_")}
    replaced = any(s.get("zone") == "front" and _norm((s.get("unit") or {}).get("name")) == "砂金·戏浪"
                   for s in board.get("slots", []))
    if not replaced:
        target.update({_norm(k): int(v) for k, v in data.get("_transition_targets", {}).items()})
    return target


def lineup_needs(board=None):
    """按当前阵容的终局目标星级，减去场上+备战席已有张数，得到还要买的卡 {名字: 张数}。"""
    try:
        board = board if board is not None else read_lineup_board()
        target = lineup_targets(board)
    except (OSError, ValueError) as error:
        if current_lineup() == "silverwolf":
            raise BridgeError(f"银狼购卡配置读取失败：{error}") from error
        return {}
    have = {}
    for s in board.get("slots", []):
        u = s.get("unit")
        if u and u.get("name"):
            n = _norm(u["name"])
            have[n] = have.get(n, 0) + 3 ** max(0, (u.get("stars") or 1) - 1)
    need = {}
    for n, stars in target.items():
        left = 3 ** (stars - 1) - have.get(n, 0)
        if left > 0:
            need[n] = left
    return need


def silverwolf_economy(do, body=None):
    """Apply lineup budgets and pause the plan when economy changes the board."""
    board = read_lineup_board()
    level, gold = board.get("level"), board.get("gold")
    if not isinstance(level, int) or not isinstance(gold, int):
        raise BridgeError("读不到等级或金币，停止经济操作")
    need = lineup_needs(board)
    known = set(lineup_targets(board)) | {"花火"}
    # Keep explicit upgrade candidates and equipment without reviving completed targets.
    for name, count in ((body or {}).get("want") or {}).items():
        name = _norm(name)
        if name not in known and int(count) > 0:
            need[name] = min(int(count), 1) if name in SIMPLE_ITEMS else int(count)
    ready = any(_norm((s.get("unit") or {}).get("name")) == "银狼LV.999"
                and (s.get("unit") or {}).get("stars", 0) >= 3 for s in board["slots"])
    if not ready and "银狼LV.999" not in need:
        return {"error": "银狼张数已足但未读到三星，先确认合星，不继续搜牌或升级"}
    cap = 9 if ready else 7
    final = board.get("stage") == "3-7" or board.get("final_node") is True
    searching = (level >= 7 and not ready) or (final and ready and level >= cap)
    always = lineup_config().get("_always_buy", [])
    if do == "exp" and level >= cap:
        return {"skipped": "银狼三星前停买经验；三星后最多升9级", "gold": gold, "level": level}
    if do == "roll" and not searching and not always:
        return {"skipped": "当前阶段不付费搜牌，需要的卡在当前商店购买", "gold": gold, "level": level}

    # Always check the current shop for needed cards, even in leveling phases.
    if do in ("roll", "fight") and (need or always):
        result = call("roll", {"floor": 0 if final else 55, "always_buy": always, "max_rolls": max(20, gold + 1) if searching else 0,
                               "want": need, "must": list(need), "auto": False,
                               "three_star": ["银狼LV.999", "真珠"],
                               "stop_targets": [] if ready else ["银狼LV.999"], "close": True})
        if result.get("error") or result.get("needs_review"):
            return result
        if result.get("bought") or result.get("rolls"):
            return {**result, "needs_review": True, "reason": "经济已处理，重新检查买到的卡、站位与装备再出战"}
    if do in ("exp", "fight") and level < cap:
        result = call("exp", {"times": 0, "until_level": cap, "floor": 0 if final else 50})
        if result.get("error"):
            return result
        if result.get("bought"):
            return {**result, "needs_review": True, "reason": "经验已按当前阶段预算购买，重新检查人口和站位再出战"}
    return {"gold": gold, "level": level, "checked": True}


def run_action(a):
    do = a.get("do")
    body = {k: v for k, v in a.items() if k != "do"}
    if current_lineup() == "silverwolf" and do in ("exp", "roll", "fight"):
        result = silverwolf_economy(do, body)
        if do != "fight" or result.get("error") or result.get("needs_review"):
            return result
    if do == "open_shop" or do == "close_shop":
        return call("tap", {"x": SHOP_BTN[0], "y": SHOP_BTN[1]})
    if do == "buy":
        return call("buy", {"names": a.get("names", []), "close": a.get("close", False)})
    if do == "sell":
        return call("sell", {"targets": a.get("targets", []), "allow_field": True})
    if do == "move":
        try:  # 保护：目标格站着同名角色就不换（备战席同名卡是升星素材，换上去会把高星的换下场）
            slots = (call("board", {}).get("board") or {}).get("slots", [])
            def unit_at(z, i):
                s = next((x for x in slots if x["zone"] == z and x["index"] == int(i)), None)
                return _norm((s or {}).get("unit", {}).get("name") if s and s.get("unit") else "")
            src = a["from"]
            src_name = _norm(src.get("name")) or unit_at(src.get("zone"), src.get("index", -1))
            dst_name = unit_at(a["to"]["zone"], a["to"]["index"])
            if src_name and src_name == dst_name:
                return {"skipped": f"目标格已是{dst_name}，不换"}
        except (BridgeError, KeyError, ValueError):
            pass
        return call("move", {"moves": [{"from": a["from"], "to": a["to"]}]})
    if do == "equip":
        return call("equip", {"items": a.get("items", []), "target": a.get("target", {})})
    if do == "wrench":
        return call("wrench", {"name": a.get("name")})
    if do in ("combine", "exp", "roll", "collect", "encounter", "halo", "tea", "rail", "tap", "fight"):
        if do == "roll":
            need = lineup_needs()
            want = dict(need)
            for k, v in (body.get("want") or {}).items():  # 手写的数量和自动缺口取较大值，不会把缺口改小
                want[k] = max(int(v), want.get(k, 0))
            for k in list(want):  # 散件（阿哈装备）每次最多买 1 个，防止一口气买一堆
                if _norm(k) in SIMPLE_ITEMS:
                    want[k] = min(want[k], 1)
            body["want"] = want
            body["must"] = sorted(set(body.get("must") or []) | set(need))
            body.setdefault("floor", 55)
            body.setdefault("auto", False)  # 不自动把场上打工卡的同名卡加进购买目标
            body.setdefault("three_star", ["银狼LV.999"])
        return call(do, body)
    return {"error": f"不支持的操作 {do}"}


def brief(res):
    if not isinstance(res, dict):
        return str(res)[:200]
    if res.get("error"):
        return "出错：" + str(res["error"])[:200]
    keep = {k: res[k] for k in ("results", "bought", "picked", "gold", "level", "rolls", "stage", "squad_health", "page", "swept", "needs_review", "reason", "skipped") if k in res}
    return json.dumps(keep, ensure_ascii=False)[:300] if keep else "ok"


def cmd_exec(args):
    st = load()
    if st.get("status") != "agreed":
        print(json.dumps({"error": f"还没有达成一致的方案（status={st.get('status')}）"}, ensure_ascii=False))
        return 1
    plan = st.get("plan") or []
    say("ds", "exec", "收到，开始执行：" + "、".join(a.get("do", "?") for a in plan) if plan else "这步不用操作～")
    out = []
    status = "done"
    for a in plan:
        try:
            res = run_action(a)
        except BridgeError as error:
            res = {"error": str(error)}
        out.append({"action": a, "result": brief(res)})
        print(f"- {json.dumps(a, ensure_ascii=False)} → {brief(res)}")
        if isinstance(res, dict) and res.get("error"):
            status = "error"
            say("ds", "say", f"「{a.get('do')}」出问题了：{str(res['error'])[:40]}")
            break
        if isinstance(res, dict) and res.get("needs_review"):
            status = "review"
            say("ds", "say", res.get("reason", "局面变化了，重新检查再继续。"))
            break
    st["status"], st["results"] = status, out
    save(st)
    print("\n" + look())
    return 0


def cmd_status(args):
    st = load()
    print(json.dumps({k: st.get(k) for k in ("status", "round", "plan")}, ensure_ascii=False, indent=1))
    print(transcript(st))
    return 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("propose")
    a.add_argument("--note")
    a = sub.add_parser("reply")
    a.add_argument("kind", choices=["agree", "object"])
    a.add_argument("text")
    a.add_argument("--actions")
    sub.add_parser("exec")
    sub.add_parser("status")
    args = p.parse_args()
    return {"propose": cmd_propose, "reply": cmd_reply, "exec": cmd_exec, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
