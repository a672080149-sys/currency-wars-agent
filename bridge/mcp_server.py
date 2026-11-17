"""货币战争 MCP 服务（Claude Code 以普通权限启动，转发到管理员权限的桥接服务）。

运行方式见项目根目录 .mcp.json：uv run --no-project --with "mcp<2" --with pillow python bridge/mcp_server.py
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

from client import BridgeError, call  # noqa: E402

mcp = FastMCP("currency-wars")


def _call(endpoint, body=None):
    try:
        return call(endpoint, body or {})
    except BridgeError as error:
        return {"error": str(error)}


def _with_image(result, scale=0.5):
    """把截图缩小后作为图片一起返回。"""
    text = json.dumps(result, ensure_ascii=False)
    path = result.get("screenshot") if isinstance(result, dict) else None
    if not path or not os.path.exists(path):
        return text
    try:
        from PIL import Image as PILImage
        img = PILImage.open(path).convert("RGB")
        if scale != 1:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return [text, Image(data=buf.getvalue(), format="jpeg")]
    except Exception:
        return text


@mcp.tool()
def cw_record(action: str = "status", fps: int = 60) -> str:
    """录制整个小克工作台窗口（游戏预览+思考+日志）：action=start 开始、stop 停止、status 查看。
    文件存到 小克工作台\录像\<时间>.mp4。用户让录一局时：开局前 start，打完（或中止）后 stop 并告诉用户文件路径。"""
    return _j("record", {"action": action, "fps": fps})


@mcp.tool()
def cw_say(thought: str | None = None, goal: list[str] | None = None, plan: list[str] | None = None,
           step: int | None = None, clear: bool = False, note: str | None = None,
           agent: str | None = None, kind: str | None = None) -> str:
    """在小克工作台上展示你的思考（用户在看，之后会做成带配音的视频）。
    thought=显示在小克头顶气泡里并进入思考记录：要口语化，像主播边打边聊，说清楚看到了什么、为什么这么做，每个操作前后都可以说；
    goal=当前游戏目标（如 ["升6搜流萤", "攒到50金币吃利息"]），显示在面板顶部，目标变了再更新；
    plan/step=非游戏的步骤清单；note=记一条日志。工作台没开时无影响。
    agent=xiaoke(默认)|ds：双人模式下谁在说话（各自的桌宠头顶气泡）；
    kind=say|propose(提议)|agree(同意)|object(反对)|decide(决定)|exec(执行)|ask(提问)，显示在对话记录里。"""
    body = {k: v for k, v in {"thought": thought, "goal": goal, "plan": plan, "step": step, "note": note,
                               "agent": agent, "kind": kind}.items() if v is not None}
    if clear:
        body["clear"] = True
    return _j("say", body)


@mcp.tool()
def cw_status() -> str:
    """桥接服务状态：是否管理员权限、游戏窗口位置、当前模式/策略/职级难度。"""
    return json.dumps(_call("status"), ensure_ascii=False)


@mcp.tool()
def cw_config(mode: str = "", strategy: str = "", difficulty: str = "") -> str:
    """设置模式(normal/overclock)、策略(默认 s2_firefly_hanya)、职级难度(lowest/current/highest)。留空表示不改。"""
    body = {k: v for k, v in {"mode": mode, "strategy": strategy, "difficulty": difficulty}.items() if v}
    return json.dumps(_call("config", body), ensure_ascii=False)


@mcp.tool()
def cw_observe(image: bool = False, texts: str = "auto", full_res: bool = False):
    """本地截图+OCR+页面识别（不耗 token），默认只返回结构化字段：page、节点、金币、血量、等级、经验、人口
    和页面专属字段（商店卡、选项等）。
    texts："auto"（默认，仅页面为 UNKNOWN/CONFLICT 时附带全屏文字）、"yes"、"no"；全屏文字为 [文字, x, y]（1920x1080）。
    image=True 时才附带截图（半分辨率约 700 token，full_res 更贵），只在识别不了或需要看画面时用。"""
    result = _call("observe", {"texts": True})
    if isinstance(result, dict) and "texts" in result:
        keep = texts == "yes" or (texts == "auto" and result.get("page") in ("UNKNOWN", "CONFLICT"))
        if not keep:
            result.pop("texts")
    return _with_image(result, 1 if full_res else 0.5) if image else json.dumps(result, ensure_ascii=False)


@mcp.tool()
def cw_tap(x: int, y: int, image: bool = False, settle: float = 0.6):
    """点击游戏画面坐标 (x, y)（1920x1080）。之后等待 settle 秒并返回新画面的识别结果。"""
    result = _call("tap", {"x": x, "y": y, "settle": settle})
    return _with_image(result) if image else json.dumps(result, ensure_ascii=False)


@mcp.tool()
def cw_drag(x1: int, y1: int, x2: int, y2: int, image: bool = False, duration: float = 0.5):
    """从 (x1,y1) 拖到 (x2,y2)，用于在棋盘上移动角色（前台/后台/备战席）。"""
    result = _call("drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration})
    return _with_image(result) if image else json.dumps(result, ensure_ascii=False)


@mcp.tool()
def cw_key(key: str, image: bool = False):
    """按键，如 esc。"""
    result = _call("key", {"key": key})
    return _with_image(result) if image else json.dumps(result, ensure_ascii=False)


@mcp.tool()
def cw_board() -> str:
    """在备战页读取完整棋盘（会逐个点开未识别角色的详情），返回每格角色名/星级/前后台/装备数，
    并附上 M7A 策略的建议：deployment(建议的移动)、buy_exp、quality_pressure、needs(还缺的升星目标)。"""
    return json.dumps(_call("board"), ensure_ascii=False)


@mcp.tool()
def cw_board_quick() -> str:
    """快速读棋盘：只用本地头像库识别每格角色（不点开详情，约 1 秒），返回名字/星级/前后台/装备数；
    不认识的格子 name 为 null（guess 为最接近的候选），需要时再用 cw_board 精确读取并学习。"""
    return json.dumps(_call("board_quick"), ensure_ascii=False)


@mcp.tool()
def cw_advise() -> str:
    """基于最近一次 cw_board 的棋盘给出策略建议；若当前画面是商店，还给出 buy(该买哪张) 和 refresh(是否刷新)。"""
    return json.dumps(_call("advise"), ensure_ascii=False)


@mcp.tool()
def cw_step(max_seconds: float = 180):
    """让 M7A 自动处理当前页面一步（备战整套运营/选择/光环/战斗等待/结算等），返回新页面和 M7A 日志。"""
    return json.dumps(_call("step", {"max_seconds": max_seconds}), ensure_ascii=False)


@mcp.tool()
def cw_run(stop_pages: list[str] | None = None, stop_on_stage_change: bool = False,
           max_steps: int = 60, max_seconds: float = 240):
    """连续让 M7A 自动推进，直到到达 stop_pages 中的页面（如 ["CHOICE","HALO","SHOP","BOARD"]）、
    节点变化、回到首页或超时。页面名见 cw_observe 的 page 字段。"""
    body = {"stop_pages": stop_pages or [], "stop_on_stage_change": stop_on_stage_change,
            "max_steps": max_steps, "max_seconds": max_seconds}
    return json.dumps(_call("run", body), ensure_ascii=False)


@mcp.tool()
def cw_abort() -> str:
    """中止正在进行的 cw_run / cw_step（在下一个检查点停下）。"""
    return json.dumps(_call("abort"), ensure_ascii=False)


@mcp.tool()
def cw_home() -> str:
    """从游戏主界面或货币战争菜单回到「入画奇航」首页。"""
    return json.dumps(_call("home"), ensure_ascii=False)


@mcp.tool()
def cw_launch(mode: str = "", difficulty: str = "", resume: bool = False) -> str:
    """开一局：进入模式→选职级→开始对局。resume=True 继续未完成存档。会重置对局内缓存。"""
    body = {k: v for k, v in {"mode": mode, "difficulty": difficulty}.items() if v}
    body["resume"] = resume
    return json.dumps(_call("launch", body), ensure_ascii=False)


@mcp.tool()
def cw_reset() -> str:
    """清空对局内缓存（接管一局进行中的对局时先调用）。"""
    return json.dumps(_call("reset"), ensure_ascii=False)


@mcp.tool()
def cw_reward() -> str:
    """在入画奇航首页查看周积分并领取可领奖励。"""
    return json.dumps(_call("reward"), ensure_ascii=False)


# ---------------------------------------------------------------- 组合操作（推荐优先使用）
def _j(endpoint, body=None):
    return json.dumps(_call(endpoint, body or {}), ensure_ascii=False)


@mcp.tool()
def cw_summary() -> str:
    """节点总览（一次调用，不点详情）：页面/节点/金币/血量/等级/经验/人口；备战页附棋盘(头像库识别名字+星级+前后台+装备数)、
    物品栏(装备名与坐标)、待捡奖励球数、可开启箱子、推进按钮文字；选项页附各栏文字；光环页附两侧光环；未知页附全屏文字。"""
    return _j("summary")


@mcp.tool()
def cw_collect() -> str:
    """开晶矿/捡完所有奖励球（自动关闭商店、确认在备战页；备战席满会停下报告）。"""
    return _j("collect")


@mcp.tool()
def cw_buy(names: list[str] | None = None, columns: list[int] | None = None, close: bool = False) -> str:
    """在商店按名字或列号(1-5)购买，自动打开商店并核对扣费。"""
    return _j("buy", {"names": names or [], "columns": columns or [], "close": close})


@mcp.tool()
def cw_roll(floor: int = 50, must: list[str] | None = None, soft: list[str] | None = None,
            want: dict[str, int] | None = None, three_star: list[str] | None = None, skip: list[str] | None = None,
            max_rolls: int = 20, stop_when_done: bool = False, auto: bool = True, close: bool = True) -> str:
    """按规则搜牌。auto=True 时自动把『场上 1 星角色』升 2 星缺口、three_star(默认流萤) 的 3 星缺口加入购买目标；
    must 不受 floor 限制（见到就买），soft 允许比 floor 少 3 金，其它目标严格守 floor；刷新到 金币-刷新费<floor 为止。
    skip 为不追星的名字。返回每轮商店、买到的卡、仍缺的卡、看到的值得注意的卡。"""
    return _j("roll", {"floor": floor, "must": must or [], "soft": soft or [], "want": want or {},
                       "three_star": three_star if three_star is not None else ["流萤"], "skip": skip or [],
                       "max_rolls": max_rolls, "stop_when_done": stop_when_done, "auto": auto, "close": close})


@mcp.tool()
def cw_exp(times: int = 0, until_level: int = 0, floor: int = 0) -> str:
    """购买经验：买 times 次，或买到 until_level 级；金币不低于 floor。"""
    body = {"floor": floor}
    if times:
        body["times"] = times
    if until_level:
        body["until_level"] = until_level
    return _j("exp", body)


@mcp.tool()
def cw_sell(targets: list[dict], allow_field: bool = False) -> str:
    """出售角色。targets 元素为 {"name":"乱破"[, "stars":1]} 或 {"zone":"bench","index":3}；
    默认只卖备战席（allow_field=True 才卖场上）。每次都会打开详情核对名字再卖。"""
    return _j("sell", {"targets": targets, "allow_field": allow_field})


@mcp.tool()
def cw_move(moves: list[dict]) -> str:
    """移动角色：[{"from":{"name":"波提欧"}或{"zone":"bench","index":1}, "to":{"zone":"front","index":3}}]。
    zone 取 front/back/bench。上场前先确认角色可站该区（前台/后台）。"""
    return _j("move", {"moves": moves})


@mcp.tool()
def cw_equip(items: list, target: dict) -> str:
    """把物品栏装备拖给角色：items=["星航履","行星钻地弹"] / ["扳手"] / ["冶金炉"]，target={"name":"雪衣"} 或 {"zone","index"}。
    注意：两件散件分别拖给角色不会合成，合成用 cw_craft。"""
    return _j("equip", {"items": items, "target": target})


@mcp.tool()
def cw_combine(a: str | list | None = None, b: str | list | None = None, pairs: list | None = None,
               confirm: bool = True) -> str:
    """物品栏内合成：把装备 a 拖到装备 b 上并点确认（如 蓝钻+以太钻头=击破星徽）。
    a/b 为装备名或 [x,y]；pairs=[[a,b],...] 连做多组；confirm=False 只拖不确认。返回新出现的装备和物品栏。"""
    body = {"confirm": confirm}
    if pairs:
        body["pairs"] = pairs
    else:
        body.update(a=a, b=b)
    return _j("combine", body)


@mcp.tool()
def cw_sell_items(names: list[str] | None = None, keep: list[str] | None = None) -> str:
    """出售物品栏装备：names 指定要卖的；或 keep 指定要留的（其余已识别装备全卖）。"""
    body = {}
    if names is not None:
        body["names"] = names
    if keep is not None:
        body["keep"] = keep
    return _j("sell_items", body)


@mcp.tool()
def cw_craft(target: dict, pick: str = "") -> str:
    """打开角色装备推荐面板：pick 为空只列出可『穿戴/合成』的选项(含成品名)；pick="1" 执行第 1 项，pick="all" 全部执行。"""
    body = {"target": target}
    if pick:
        body["pick"] = pick if pick == "all" else int(pick)
    return _j("craft", body)


@mcp.tool()
def cw_encounter(refresh: bool = True) -> str:
    """遭遇节点：优先难度 1，没有难度 1 时先刷新一次，然后选最低难度并确认。"""
    return _j("encounter", {"refresh": refresh})


@mcp.tool()
def cw_halo(action: str = "read", side: str = "", slot_index: int = 0, confirm: bool = True) -> str:
    """主角光环（从备战页会自动进入）：action=read 读两侧；refresh+side(left/right) 刷新一侧；
    pick+side+slot_index(前台第几格,0起) 选光环给该角色并确认。"""
    return _j("halo", {"action": action, "side": side, "slot_index": slot_index, "confirm": confirm})


@mcp.tool()
def cw_fight(max_seconds: float = 480) -> str:
    """点击出战（确认离开备战页后）并等战斗/结算结束，停在下一个需要决策的页面。"""
    return _j("fight", {"max_seconds": max_seconds})


@mcp.tool()
def cw_logs(since: int = 0, limit: int = 120) -> str:
    """读取 M7A 的最近日志。"""
    return json.dumps(_call("logs", {"since": since, "limit": limit}), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
