"""货币战争桥接服务（需以管理员身份运行，与游戏同权限）。

复用 March7thAssistant 的截图、OCR、页面识别、棋盘读取和单步自动逻辑，
在 127.0.0.1 上提供 JSON 接口，让 Claude 一步步观察和操作游戏。

启动：bridge\\start_server.bat（会弹 UAC）。
所有请求需带头 X-CW-Token，值在 bridge\\.token（启动时生成）。
"""
from __future__ import annotations

import io
import json
import logging
import os
import secrets
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BRIDGE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.normpath(os.path.join(BRIDGE, "..", "vendor", "March7thAssistant"))
SHOTS = os.path.join(BRIDGE, "shots")
TOKEN_PATH = os.path.join(BRIDGE, ".token")
PORT = int(os.environ.get("CW_BRIDGE_PORT", "47821"))

os.makedirs(SHOTS, exist_ok=True)
os.chdir(VENDOR)
sys.path.insert(0, VENDOR)

BACKEND = os.environ.get("CW_BACKEND", "local")
if BACKEND == "webview":  # 小克工作台内嵌的云·星穹铁道
    import webview_game  # noqa: E402
    webview_game.install()

from module.automation import auto  # noqa: E402
from module.config import cfg  # noqa: E402
from tasks.weekly import currency_wars as cw  # noqa: E402
from tasks.weekly import currency_wars_strategy as strategy  # noqa: E402
from tasks.weekly import currency_wars_ui as ui  # noqa: E402
from tasks.weekly.currency_wars_ui import Page  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, BRIDGE)
from portraits import PortraitLibrary, face_of  # noqa: E402
from textview import TextActions as Actions  # noqa: E402
import workbench  # noqa: E402

DEFAULT_STRATEGY = strategy.HIGH_FIREFLY


# ---------------------------------------------------------------- 日志捕获
class _Ring(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = deque(maxlen=400)
        self.seq = 0

    def emit(self, record):
        self.seq += 1
        self.lines.append((self.seq, f"{time.strftime('%H:%M:%S')} {record.levelname[0]} {record.getMessage()}"))

    def since(self, seq):
        return [line for s, line in self.lines if s > seq]


RING = _Ring()
logging.getLogger("March7thAssistant").addHandler(RING)


# ---------------------------------------------------------------- 序列化
def jsonable(value):
    if isinstance(value, Page):
        return value.name
    if is_dataclass(value) and not isinstance(value, type):
        return {k: jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, ui.Label):
        return {"text": value.text, "point": list(value.point)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def safe(frame, name):
    try:
        attr = getattr(frame, name)
        return jsonable(attr() if callable(attr) else attr)
    except Exception as error:  # 不同页面上部分字段读不出来属正常
        return f"<error {type(error).__name__}>"


def describe_slot(slot):
    if slot is None:
        return None
    unit = slot.unit
    return {
        "zone": slot.zone, "index": slot.index, "point": list(slot.point),
        "occupied": slot.occupied, "blocked": slot.blocked,
        "unit": None if unit is None else {
            "name": unit.name, "stars": unit.stars, "front": unit.front, "back": unit.back,
            "output": unit.output, "equipment": unit.equipment, "halo": unit.has_halo,
        },
    }


def describe_board(board):
    if board is None:
        return None
    return {
        "stage": board.stage, "gold": board.gold, "hp": board.hp, "used": board.used,
        "capacity": board.capacity, "level": board.level, "exp": board.exp,
        "final_node": board.final_node, "core": board.core,
        "core_ready": board.core_ready, "core_upgraded": board.core_upgraded,
        "slots": [describe_slot(s) for s in board.slots],
    }


PAGE_FIELDS = {
    "common": ("stage", "gold", "squad_health", "level", "experience", "experience_cost", "capacity"),
    Page.SHOP: ("offers", "shop_refresh_price"),
    Page.CHOICE: ("choice", "recruitment_cards", "supply_cards", "item_cards", "investment_titles"),
    Page.HALO: ("halo_gold",),
    Page.GARDEN: ("board_effect_cards",),
    Page.HACK: ("board_effect_cards",),
    Page.DANCE: ("employee_event_candidates",),
    Page.RANGER: ("employee_event_candidates",),
    Page.TEA: ("tea_hp",),
    Page.NODE_RESULT: ("result_heading",),
    Page.MATCH_RESULT: ("result_heading",),
    Page.MODES: ("menu_mode", "unfinished_match"),
    Page.RANK: ("rank_mode", "enemy_rank"),
}


# ---------------------------------------------------------------- 核心
class Bridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.war = cw.CurrencyWars()
        self.abort_flag = False
        self.portraits = PortraitLibrary()
        self.last_read_stats = {}
        original = self.war._read_board

        def read_board(frame, **kwargs):
            # 先用头像库预填 M7A 的格位缓存，命中的格子不再点开详情；读完收录新确认的头像。
            matched = self._prefill(frame)
            frame2, board = original(frame, **kwargs)
            learned = self._harvest(frame2, board) if board is not None else 0
            self.last_read_stats = {"matched": matched, "learned": learned, "library": self.portraits.stats()}
            return frame2, board

        self.war._read_board = read_board
        self.configure({})

    @staticmethod
    def _group(slot):
        return "bench" if slot.zone == "bench" else "field"

    def _prefill(self, frame):
        matched = []
        try:
            slots = frame.slots()
        except Exception:
            return matched
        for slot in slots:
            if not slot.occupied or slot.blocked:
                continue
            key = slot.zone, slot.index
            mark = frame.slot_mark(slot.point)
            portrait = cv2.resize(mark, (12, 12)).astype(np.int16)
            cached = self.war.slot_cache.get(key)
            if cached and np.abs(cached[0] - portrait).mean() <= 6:
                continue
            name, score = self.portraits.confident(face_of(mark), self._group(slot))
            if name:
                self.war.slot_cache[key] = (portrait, name, self.portraits.output_of(name))
                matched.append(f"{slot.zone}{slot.index}:{name}({score:.2f})")
        return matched

    def _harvest(self, frame, board):
        learned = 0
        for slot in board.slots:
            if slot.unit is None or slot.blocked:
                continue
            try:
                if frame.slot_occupied(slot.point):
                    learned += self.portraits.add(face_of(frame.slot_mark(slot.point)), self._group(slot),
                                                  slot.unit.name, slot.unit.output)
            except Exception:
                pass
        return learned

    def board_quick(self, body):
        """只用头像库识别，不点开任何详情（速度快；不认识的格子 name 为 null）。"""
        self._arm(20)
        frame = self.war._observe()
        if frame.page != Page.BOARD:
            return {"error": f"当前不在备战棋盘（{frame.page.value}）", **self.summarize(frame, texts=False)}
        slots = []
        for slot in frame.slots():
            item = {"zone": slot.zone, "index": slot.index, "point": list(slot.point),
                    "occupied": slot.occupied, "blocked": slot.blocked}
            if slot.occupied and not slot.blocked:
                mark = frame.slot_mark(slot.point)
                name, top, second = self.portraits.match(face_of(mark), self._group(slot))
                ok = name and top >= 0.82 and top - second >= 0.06
                item.update({"name": name if ok else None, "guess": name, "score": round(top, 3),
                             "margin": round(top - second, 3)})
                try:
                    item["stars"] = self.war._card_stars(frame, slot)
                    item["front"], item["back"] = frame.card_role(slot)
                    item["equipment"] = frame.card_equipment(slot)
                except Exception:
                    pass
            slots.append(item)
        return {"stage": frame.stage, "gold": frame.gold, "hp": safe(frame, "squad_health"),
                "level": frame.level, "exp": frame.experience, "capacity": frame.capacity,
                "slots": slots, "library": self.portraits.stats()}

    # -- 配置
    def configure(self, body):
        mode = body.get("mode") or cfg.get_value("currencywars_type", "normal")
        if mode not in ("normal", "overclock"):
            raise ValueError("mode 只能是 normal 或 overclock")
        strat = body.get("strategy") or DEFAULT_STRATEGY
        strat = strategy.resolve_strategy(strat)
        difficulty = body.get("difficulty")
        if difficulty:
            if difficulty not in ("lowest", "current", "highest"):
                raise ValueError("difficulty 只能是 lowest / current / highest")
            cfg.set_value("currencywars_rank_difficulty", difficulty)
        if body.get("mode"):
            cfg.set_value("currencywars_type", mode)
        self.war.mode = mode
        self.war.lineup = strategy.LINEUPS.get(strat)
        self.strategy = strat
        return {"mode": mode, "strategy": strat, "strategy_name": strategy.STRATEGY_NAMES[strat],
                "difficulty": cfg.get_value("currencywars_rank_difficulty", "lowest")}

    def reset_match(self):
        """复刻 CurrencyWars._play 开头的状态清理。"""
        w = self.war
        w.slot_cache.clear()
        w.stage_refreshes.clear()
        w.deployment_stage = None
        w.deployment_moves = ()
        w.held_deployment = None
        w.deployment_replan_stage = None
        w.preparation_progress = 0
        w.experience_probes.clear()
        w.disassembly_attempted.clear()
        w.equipment_deferred_stage = None
        w.deferred_operations.clear()
        w.halo_excluded.clear()
        w.halo_candidate = None
        w.result = None
        w.last_board = None
        w.last_result_stage = None
        w.network_reconnects = 0

    def _arm(self, seconds):
        """给 M7A 的预算检查设一个截止时间，/abort 会把它提前到现在。"""
        self.abort_flag = False
        self.war.deadline = time.monotonic() + seconds

    def abort(self):
        self.abort_flag = True
        self.war.deadline = time.monotonic()
        return {"aborting": True}

    # -- 观察
    def observe(self, body):
        self._arm(30)
        frame = self.war._observe()
        return self.summarize(frame, texts=body.get("texts", True))

    def summarize(self, frame, *, texts=True):
        path = os.path.join(SHOTS, "last.png")
        frame.image.save(path)
        page = frame.page
        info = {"page": page.name, "page_cn": page.value, "screenshot": path}
        for name in PAGE_FIELDS["common"]:
            info[name] = safe(frame, name)
        for name in PAGE_FIELDS.get(page, ()):
            info[name] = safe(frame, name)
        if texts:
            info["texts"] = [[lb.text, *lb.point] for lb in frame.labels]
        return info

    # -- 原始输入（坐标均为 1920x1080 游戏画面坐标）
    @staticmethod
    def _before(body):
        from actions import shot
        return None if "settle" in body else shot()

    @staticmethod
    def _settle(before, body):
        """未指定 settle 时按画面变化等待（变化后稳定即返回，最多 3 秒）。"""
        if before is None:
            time.sleep(float(body["settle"]))
        else:
            from actions import wait_change
            wait_change(before, timeout=float(body.get("timeout", 3.0)))
    def _map(self, x, y):
        if not (0 <= x < 1920 and 0 <= y < 1080):
            raise ValueError("坐标超出 1920x1080")
        pos = auto.find_element((x / 1920, y / 1080, 0, 0), "crop", take_screenshot=False, need_ocr=False)
        if pos is None:
            raise RuntimeError("无法映射游戏窗口坐标（窗口是否在前台/最小化？）")
        return pos

    def tap(self, body):
        if auto.screenshot is None:
            auto.take_screenshot()
        pos = self._map(int(body["x"]), int(body["y"]))
        before = self._before(body)
        auto.click_element_with_pos(pos, press_duration=float(body.get("hold", cw.CLICK_HOLD)))
        self._settle(before, body)
        return self.observe({"texts": body.get("texts", False)}) if body.get("observe", True) else {"ok": True}

    def drag(self, body):
        if auto.screenshot is None:
            auto.take_screenshot()
        src = int(body["x1"]) / 1920, int(body["y1"]) / 1080
        dst = int(body["x2"]) / 1920, int(body["y2"]) / 1080
        before = self._before(body)
        auto.drag_mouse(src, dst, duration=float(body.get("duration", 0.5)), end_pause=0.10)
        self._settle(before, body)
        return self.observe({"texts": body.get("texts", False)}) if body.get("observe", True) else {"ok": True}

    def key(self, body):
        before = self._before(body)
        auto.press_key(body["key"])
        self._settle(before, body)
        return self.observe({"texts": body.get("texts", False)}) if body.get("observe", True) else {"ok": True}

    # -- 读棋盘与建议
    def board(self, body):
        self._arm(90)
        frame = self.war._observe()
        if frame.page != Page.BOARD:
            return {"error": f"当前不在备战棋盘（{frame.page.value}）", **self.summarize(frame, texts=False)}
        frame, board = self.war._read_board(frame)
        if board is None:
            return {"error": "棋盘读取中途页面变化", **self.summarize(frame, texts=False)}
        self.war.last_board = board
        return {"board": describe_board(board), "read_stats": self.last_read_stats,
                "advice": self._advice(board, frame)}

    def advise(self, body):
        board = self.war.last_board
        if board is None:
            return {"error": "还没有读过棋盘，先调用 /board"}
        frame = self.war.frame
        return {"board_stage": board.stage, "advice": self._advice(board, frame)}

    def _advice(self, board, frame):
        out = {}
        try:
            out["deployment"] = [[describe_slot(a), describe_slot(b)] for a, b in strategy.deployment(board)]
        except Exception as error:
            out["deployment"] = f"<error {error}>"
        price = frame.experience_cost if frame is not None else None
        try:
            out["buy_exp"] = strategy.should_buy_exp(board, price)
        except Exception as error:
            out["buy_exp"] = f"<error {error}>"
        if board.lineup:
            try:
                out["quality_pressure"] = strategy._quality_pressure(board)
                out["needs"] = [name for name, stars in board.lineup.stars
                                if strategy._needs_copy(board, name, stars, strategy._owned(board, name))]
            except Exception as error:
                out["quality_pressure"] = f"<error {error}>"
        if frame is not None and frame.page == Page.SHOP:
            offers = frame.offers
            out["offers"] = jsonable(offers)
            out["buy"] = jsonable(strategy.shop_purchase(board, offers, experience_price=price))
            refresh_price = frame.shop_refresh_price
            if refresh_price is not None:
                out["refresh"] = strategy.should_refresh(board, refresh_price, self.war.stage_refreshes.get(board.stage, 0),
                                                         experience_price=price)
        try:
            out["disposable_bench"] = [describe_slot(s) for s in strategy.disposable_bench(board)]
        except Exception:
            pass
        return out

    # -- M7A 自动驾驶
    def _one_step(self, frame):
        w = self.war
        try:
            return w._play_step(frame), None
        except cw.CurrencyWarsReconnect as error:
            return error.frame, "reconnect"
        except cw.CurrencyWarsEquipmentReplan as error:
            return w.frame, f"equipment_replan: {error}"
        except cw.CurrencyWarsReceiptTimeout as error:
            try:
                return w._recover_transit(frame, error), f"recovered: {error}"
            except cw.CurrencyWarsReconnect as reconnect:
                return reconnect.frame, "reconnect"
        except (cw.CurrencyWarsActionUnready, cw.CurrencyWarsReadTimeout) as error:
            w.slot_cache.clear()
            w.deployment_moves = ()
            return w._observe(), f"replan: {error}"

    def step(self, body):
        self._arm(float(body.get("max_seconds", 180)))
        seq = RING.seq
        frame = self.war._observe()
        before = frame.page.name
        frame, note = self._one_step(frame)
        return {"before": before, "note": note, "log": RING.since(seq), **self.summarize(frame, texts=False)}

    def run(self, body):
        """连续单步，直到命中 stop_pages / 节点变化 / 回到首页 / 超时。"""
        max_seconds = float(body.get("max_seconds", 240))
        max_steps = int(body.get("max_steps", 60))
        stop_pages = {p.upper() for p in body.get("stop_pages", [])}
        stop_on_stage = bool(body.get("stop_on_stage_change", False))
        self._arm(max_seconds + 120)
        started = time.monotonic()
        seq = RING.seq
        frame = self.war._observe()
        stage = frame.stage
        trail, reason, failures = [], "max_steps", 0
        for _ in range(max_steps):
            if self.abort_flag:
                reason = "aborted"
                break
            if frame.page == Page.HOME:
                reason = "home"
                break
            if trail and frame.page.name in stop_pages:
                reason = f"stop_page:{frame.page.name}"
                break
            if time.monotonic() - started > max_seconds:
                reason = "max_seconds"
                break
            try:
                frame, note = self._one_step(frame)
            except cw.CurrencyWarsError as error:
                failures += 1
                trail.append(f"ERROR {error}")
                if failures >= 2 or self.abort_flag:
                    reason = f"error: {error}"
                    break
                frame = self.war._observe()
                continue
            trail.append(f"{frame.page.name}{' ' + frame.stage if frame.stage else ''}{' (' + note + ')' if note else ''}")
            if stop_on_stage and frame.stage and stage and frame.stage != stage:
                reason = "stage_change"
                break
            stage = frame.stage or stage
        return {"reason": reason, "trail": trail, "result": self.war.result,
                "log": RING.since(seq)[-80:], **self.summarize(frame, texts=False)}

    # -- 菜单
    def home(self, body):
        self._arm(60)
        frame = self.war._homepage(self.war._observe())
        return self.summarize(frame, texts=False)

    def launch(self, body):
        """从首页/模式页开局（resume=True 继续存档）。会按 difficulty 设置职级。"""
        self._arm(180)
        self.configure(body)
        frame = self.war._observe()
        if frame.page not in (Page.MODES, Page.RANK, Page.END_MATCH_CONFIRM):
            frame = self.war._homepage(frame)
        self.reset_match()
        try:
            frame = self.war._launch(frame, bool(body.get("resume", False)))
        except cw.CurrencyWarsReconnect as error:
            frame = error.frame
        return self.summarize(frame, texts=False)

    def reward(self, body):
        self._arm(90)
        frame, score, claimed = self.war._check_weekly_reward(self.war._observe())
        return {"score": score, "claimed": claimed, **self.summarize(frame, texts=False)}

    def logs(self, body):
        return {"log": RING.since(int(body.get("since", 0)))[-int(body.get("limit", 120)):], "seq": RING.seq}

    def status(self, body):
        import ctypes
        window = None
        try:
            from module.automation.screenshot import Screenshot
            win = Screenshot.get_window(cfg.game_title_name)
            window = None if not win else {"left": win.left, "top": win.top, "width": win.width, "height": win.height}
        except Exception as error:
            window = f"<error {error}>"
        if BACKEND == "webview":
            window = {"backend": "webview", "page": webview_game.GAME.page_state()}
        return {"elevated": bool(ctypes.windll.shell32.IsUserAnAdmin()), "window": window,
                "control_mode": cfg.get_value("local_control_mode", "foreground"),
                **self.configure({})}


BRIDGE_CORE = Bridge()
ACT = Actions(BRIDGE_CORE)
ROUTES = {
    "summary": ACT.summary, "collect": ACT.collect, "buy": ACT.buy, "roll": ACT.roll, "exp": ACT.exp,
    "sell": ACT.sell, "move": ACT.move, "equip": ACT.equip, "sell_items": ACT.sell_items, "combine": ACT.combine, "craft": ACT.craft,
    "encounter": ACT.encounter, "halo": ACT.halo, "fight": ACT.fight,
    "status": BRIDGE_CORE.status, "config": BRIDGE_CORE.configure, "observe": BRIDGE_CORE.observe,
    "tap": BRIDGE_CORE.tap, "drag": BRIDGE_CORE.drag, "key": BRIDGE_CORE.key,
    "board": BRIDGE_CORE.board, "board_quick": BRIDGE_CORE.board_quick, "advise": BRIDGE_CORE.advise,
    "portraits_reload": lambda body: BRIDGE_CORE.portraits.reload(), "step": BRIDGE_CORE.step, "run": BRIDGE_CORE.run,
    "home": BRIDGE_CORE.home, "launch": BRIDGE_CORE.launch, "reward": BRIDGE_CORE.reward,
    "say": lambda body: (workbench.send({k: body[k] for k in ("thought", "goal", "plan", "step", "clear", "note", "reason", "mood") if k in body}), {"ok": True})[1],
    "record": lambda body: workbench.record(body.get("action", "status"), int(body.get("fps", 60))),
    "logs": BRIDGE_CORE.logs, "reset": lambda body: (BRIDGE_CORE.reset_match(), {"reset": True})[1],
}
ACT_ROUTES = ("summary", "collect", "buy", "roll", "exp", "sell", "move", "equip", "sell_items", "combine", "craft",
              "encounter", "halo", "fight", "look", "wrench", "furnace", "rail", "tea", "icons", "teach")


def bind_actions(act):
    for name in ACT_ROUTES:
        ROUTES[name] = getattr(act, name)


def reload_code(body):
    """热加载 icons / actions / textview，不用重启服务。"""
    global ACT
    import importlib
    import icons as _icons
    import actions as _actions
    import textview as _textview
    for mod in (_icons, _actions, _textview):
        importlib.reload(mod)
    ACT = _textview.TextActions(BRIDGE_CORE)
    bind_actions(ACT)
    for name in getattr(_textview, "EXTRA_ROUTES", ()):  # 新增路由不用重启
        ROUTES[name] = getattr(ACT, name)
    return {"reloaded": True, "icons": {k: len(v) for k, v in ACT.lib.entries.items()}}


bind_actions(ACT)
ROUTES["reload"] = reload_code
UNLOCKED = {"abort", "logs", "status", "say", "record"}  # 这些不等待长任务


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _reply(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.headers.get("X-CW-Token") != TOKEN:
            return self._reply(403, {"error": "bad token"})
        name = self.path.strip("/")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._reply(400, {"error": "bad json"})
        if name == "abort":
            return self._reply(200, BRIDGE_CORE.abort())
        handler = ROUTES.get(name)
        if handler is None:
            return self._reply(404, {"error": f"unknown endpoint {name}", "endpoints": sorted([*ROUTES, "abort"])})
        if name in UNLOCKED:
            return self._reply(200, jsonable(handler(body)))
        if not BRIDGE_CORE.lock.acquire(timeout=5):
            return self._reply(409, {"error": "busy: 另一个操作仍在进行，可调用 /abort"})
        try:
            result = jsonable(handler(body))
            err = result.get("error") if isinstance(result, dict) else None
            workbench.report(name, body, error=err)
            self._reply(200, result)
        except Exception as error:
            workbench.report(name, body, error=f"{type(error).__name__}: {error}")
            self._reply(500, {"error": f"{type(error).__name__}: {error}",
                              "trace": traceback.format_exc()[-1500:]})
        finally:
            BRIDGE_CORE.war.deadline = None
            BRIDGE_CORE.lock.release()


if __name__ == "__main__":
    TOKEN = secrets.token_hex(16)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(TOKEN)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[cw-bridge] listening on 127.0.0.1:{PORT}  strategy={BRIDGE_CORE.strategy}  mode={BRIDGE_CORE.war.mode}")
    print("[cw-bridge] 关闭此窗口即停止服务")
    workbench.note(f"cw-bridge 就绪 127.0.0.1:{PORT}", f"策略 {BRIDGE_CORE.strategy}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
