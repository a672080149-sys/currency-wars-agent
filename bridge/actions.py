"""组合操作：一次调用完成一个完整动作并自带核对，等待改为「画面变化即继续」。

所有坐标为 1920x1080 游戏画面坐标。由 server.py 导入并注册路由（需要在 M7A 环境内运行）。
"""
from __future__ import annotations

import re
import time

import cv2
import numpy as np

from module.automation import auto
from tasks.weekly import currency_wars_ui as ui
from tasks.weekly.currency_wars_ui import Page, equipment_name

SHOP_BTN = (1624, 982)
SHOP_ROW_AREA = (367, 60, 1700, 340)
REFRESH_BTN = (1618, 488)
XP_BTN = (298, 864)
DETAIL_AREA = (1340, 150, 1920, 1000)
INVENTORY_AREA = (1700, 110, 1920, 820)
REWARD_AREA = (1275, 110, 1920, 470)
INVENTORY_COLUMNS = (1765, 1844)  # 物品栏左列、右列中心 x
COMBINE_OK_DY = 76  # 物品拖到物品上合成时，确认 ✓ 在 (1820, 目标y+76)
BOARD_PAGES = (Page.BOARD,)
NOTE = {"寒鸦", "白露", "千冶·刃", "星期日", "缇宝", "知更鸟", "灵砂", "罗刹", "符玄", "砂金", "花火", "雪衣",
        "风堇", "波提欧", "乱破", "大丽花", "流萤", "开拓者", "CT"}


# ---------------------------------------------------------------- 低层
def shot() -> np.ndarray:
    if not auto.take_screenshot():
        raise RuntimeError("截图失败")
    img = auto.screenshot
    if img.size != (1920, 1080):
        img = img.resize((1920, 1080))
    return np.asarray(img.convert("RGB"))


def _diff(a, b, area=None):
    if area:
        x1, y1, x2, y2 = area
        a, b = a[y1:y2, x1:x2], b[y1:y2, x1:x2]
    return float(cv2.absdiff(a, b).mean())


def wait_change(before, area=None, timeout=3.0, threshold=5.0, quiet=1.5):
    """轮询截图：区域变化超过阈值后，再等画面稳定（相邻两帧差 < quiet）即返回。超时返回最后一帧。"""
    start = time.monotonic()
    prev = before
    changed = False
    while time.monotonic() - start < timeout:
        time.sleep(0.12)
        cur = shot()
        if not changed and _diff(cur, before, area) > threshold:
            changed = True
        elif changed and _diff(cur, prev, area) < quiet:
            return cur, True
        prev = cur
    return prev, changed


def tap(x, y, hold=0.075):
    if auto.screenshot is None:
        shot()
    pos = auto.find_element((x / 1920, y / 1080, 0, 0), "crop", take_screenshot=False, need_ocr=False)
    if pos is None:
        raise RuntimeError("无法映射游戏窗口坐标")
    auto.click_element_with_pos(pos, press_duration=hold)


def drag(x1, y1, x2, y2, duration=0.8):
    if auto.screenshot is None:
        shot()
    auto.drag_mouse((x1 / 1920, y1 / 1080), (x2 / 1920, y2 / 1080), duration=duration, end_pause=0.10)


def tap_wait(x, y, area=None, timeout=3.0, hold=0.075):
    before = shot()
    tap(x, y, hold)
    return wait_change(before, area, timeout)


def norm(name):
    name = (name or "").replace("干冶", "千冶").replace("•", "·").strip()
    if name == "星神☆阿哈":
        return "星神★阿哈"
    return "开拓者·欢愉" if name.upper() == "CT" else name


# ---------------------------------------------------------------- 组合操作
class Actions:
    def __init__(self, bridge):
        self.b = bridge

    # -- 基础
    def frame(self, seconds=30):
        self.b._arm(seconds)
        return self.b.war._observe()

    def same(self, a, b):
        a, b = norm(a), norm(b)
        if not a or not b:
            return False
        if a == b or a in b or b in a:
            return True
        ca = self.b.portraits.canonical(a)
        return ca is not None and ca == self.b.portraits.canonical(b)

    def ensure_board(self, f=None):
        f = f or self.frame()
        for _ in range(4):
            if f.page == Page.BOARD:
                return f
            if f.page == Page.SHOP:
                tap_wait(*SHOP_BTN, area=(0, 0, 1920, 600))
            elif f.page in (Page.DETAIL, Page.PARTNER_DETAIL, Page.EQUIPMENT, Page.HALO_DETAIL):
                before = shot()
                auto.press_key("esc")
                wait_change(before)
            else:
                return f
            f = self.frame()
        return f

    def open_shop(self, f=None):
        f = f or self.frame()
        if f.page == Page.SHOP:
            return f
        f = self.ensure_board(f)
        if f.page != Page.BOARD:
            return f
        tap_wait(*SHOP_BTN, area=(0, 0, 1920, 600))
        return self.frame()

    def slots(self, f):
        """头像库快速识别所有格子（不点开详情）。"""
        out = []
        for slot in f.slots():
            item = {"zone": slot.zone, "index": slot.index, "point": list(slot.point),
                    "occupied": slot.occupied, "blocked": slot.blocked, "name": None, "stars": None}
            if slot.occupied and not slot.blocked:
                from portraits import face_of
                name, top, second = self.b.portraits.match(face_of(f.slot_mark(slot.point)), self.b._group(slot))
                if name and top >= 0.82 and top - second >= 0.06:
                    item["name"] = name
                else:
                    item["guess"] = name
                try:
                    item["stars"] = self.b.war._card_stars(f, slot)
                    item["front"], item["back"] = f.card_role(slot)
                    item["equipment"] = f.card_equipment(slot)
                except Exception:
                    pass
            out.append(item)
        return out

    @staticmethod
    def inventory_cells(f):
        """扫描物品栏左右两列（M7A 原版只扫右列 x=1844）。左列要求同时有上下边缘和图标纹理，避免背景误判。"""
        out = []
        try:
            start = 271 if f.has("装备追踪中", area=(1695, 99, 1900, 148)) else 248
        except Exception:
            start = 248
        for y in range(start, start + 77 * 6, 77):
            for x in INVENTORY_COLUMNS:
                edges = []
                for top, bottom in ((y - 43, y - 25), (y + 28, y + 45)):
                    crop = f.crop((x - 29, top, x + 30, bottom)).mean(axis=2)
                    edges.append((np.abs(np.diff(crop, axis=0)) > 16).mean(axis=1).max())
                if min(edges) < 0.65:
                    continue
                mark = f.crop((x - 24, y - 24, x + 24, y + 24))
                name = equipment_name(mark)
                if x != INVENTORY_COLUMNS[-1] and name is None and mark.std(axis=(0, 1)).max() < 28:
                    continue
                out.append(((x, y), name))
        return out

    def inventory(self, f):
        items = []
        try:
            cells = self.inventory_cells(f)
        except Exception:
            cells = []
        if not any(x == INVENTORY_COLUMNS[-1] for (x, _), _ in cells):
            try:  # 兜底：自扫没扫到右列时退回 M7A 原版
                cells += list(f.inventory_equipment())
            except Exception:
                pass
        for (x, y), name in sorted(cells, key=lambda c: (c[0][1], c[0][0])):
            items.append({"point": [x, y], "name": name})
        tool = None
        try:
            tool = f.disassembly_tool()
        except Exception:
            pass
        return {"items": items, "wrench": list(tool) if tool else None}

    def item_name_by_tooltip(self, point):
        tap_wait(*point, area=(1250, 100, 1920, 760))
        f = self.frame()
        name = next((lb.text for lb in f.texts((1380, 140, 1800, 185)) if not lb.text.isdigit()), None)
        sell = f.find("出售", area=(1300, 650, 1920, 800))
        return f, name, sell

    def dismiss_tooltip(self):
        tap_wait(1100, 230, timeout=1.0)

    # -- 总览
    def summary(self, body):
        f = self.frame()
        info = self.b.summarize(f, texts=False)
        info.pop("screenshot", None)
        if f.page == Page.BOARD:
            info["slots"] = [s for s in self.slots(f) if s["occupied"]]
            info["inventory"] = self.inventory(f)
            info["orbs"] = len(f.reward_orbs())
            info["boxes"] = [list(lb.point) for lb in f.texts(ui.BENCH_BOX) if lb.text == "开启"]
            adv = f.find(*ui.BOARD_ADVANCE, area=ui.BOARD_FIGHT)
            info["advance"] = adv.text if adv else None
        elif f.page == Page.CHOICE:
            info["options"] = self.choice_columns(f)
        elif f.page == Page.HALO:
            info["halo"] = self.halo_state(f)
        elif f.page in (Page.UNKNOWN, Page.CONFLICT, Page.REVEAL):
            info["texts"] = [[lb.text, *lb.point] for lb in f.labels]
        return info

    @staticmethod
    def choice_columns(f):
        labels = f.texts((150, 150, 1780, 900))
        if not labels:
            return []
        xs = sorted({lb.point[0] for lb in labels})
        groups, cur = [], [xs[0]]
        for x in xs[1:]:
            if x - cur[-1] > 120:
                groups.append(cur)
                cur = [x]
            else:
                cur.append(x)
        groups.append(cur)
        cols = []
        for g in groups:
            lo, hi = g[0] - 1, g[-1] + 1
            col = sorted([lb for lb in labels if lo <= lb.point[0] <= hi], key=lambda lb: (lb.point[1], lb.point[0]))
            cols.append({"x": (lo + hi) // 2, "text": " ".join(lb.text for lb in col),
                         "top": [col[0].text, *col[0].point] if col else None})
        return cols

    # -- 晶矿/奖励球
    def collect(self, body):
        f = self.ensure_board()
        if f.page != Page.BOARD:
            return {"error": f"不在棋盘（{f.page.value}）", "page": f.page.name}
        swept = 0
        blocked = False
        stalled = 0
        for _ in range(18):
            orbs = f.reward_orbs()
            if not orbs:
                break
            idx = min(stalled, len(orbs) - 1)
            x, y = orbs[idx]
            before = shot()
            drag(max(1275, x - 86), y, min(1638, x + 88), y, duration=0.35)
            wait_change(before, REWARD_AREA, timeout=1.5)
            f = self.frame()
            if f.has("备战席已满", area=(440, 275, 1480, 327), contains=True):
                blocked = True
                break
            if f.page != Page.BOARD:
                break
            new_orbs = f.reward_orbs()
            still_there = any(abs(nx - x) < 35 and abs(ny - y) < 35 for nx, ny in new_orbs)
            if still_there and len(new_orbs) >= len(orbs):
                stalled += 1
                if stalled >= 2 or stalled >= len(new_orbs):
                    blocked = True
                    break
            else:
                swept += 1
                stalled = 0
        return {"swept": swept, "blocked_bench_full": blocked, "page": f.page.name, "gold": f.gold,
                "orbs_left": len(f.reward_orbs()) if f.page == Page.BOARD else None,
                "inventory": self.inventory(f) if f.page == Page.BOARD else None,
                "boxes": [list(lb.point) for lb in f.texts(ui.BENCH_BOX) if lb.text == "开启"]
                if f.page == Page.BOARD else None}

    # -- 商店
    def buy(self, body):
        names = body.get("names") or []
        columns = body.get("columns") or []
        f = self.open_shop()
        if f.page != Page.SHOP:
            return {"error": f"无法打开商店（{f.page.value}）"}
        bought = []
        for col in columns:
            offer = next((o for o in f.offers if o.column == int(col) - 1), None)
            if offer:
                g0 = f.gold
                tap_wait(*offer.point, area=SHOP_ROW_AREA)
                f = self.frame()
                bought.append({"name": offer.name, "ok": f.gold is not None and g0 is not None and f.gold < g0,
                               "gold": f.gold})
        for name in names:
            offer = next((o for o in f.offers if norm(o.name) == norm(name)), None)  # 精确匹配，避免 砂金→砂金·戏浪
            if offer is None:
                cn = self.b.portraits.canonical(norm(name))
                offer = next((o for o in f.offers if cn and self.b.portraits.canonical(norm(o.name)) == cn), None)
            if offer is None:
                bought.append({"name": name, "ok": False, "reason": "商店没有"})
                continue
            g0 = f.gold
            tap_wait(*offer.point, area=SHOP_ROW_AREA)
            f = self.frame()
            bought.append({"name": offer.name, "ok": f.gold is not None and g0 is not None and f.gold < g0,
                           "gold": f.gold})
        if body.get("close", False):
            f = self.ensure_board(f)
        return {"bought": bought, "gold": f.gold, "page": f.page.name,
                "offers": [[o.name, o.cost, o.stars] for o in (f.offers if f.page == Page.SHOP else ())]}

    def refresh_shop(self, f):
        btn = f.find(*ui.SHOP_REFRESH_LABELS, area=ui.SHOP_REFRESH)
        tap_wait(*(btn.point if btn else REFRESH_BTN), area=SHOP_ROW_AREA)
        return self.frame()

    def roll(self, body):
        """按规则搜牌。自动把场上 1 星角色的升星缺口、three_star 名单的 3 星缺口加入购买目标。"""
        floor = int(body.get("floor", 50))
        must = {norm(n) for n in body.get("must", [])}
        soft = {norm(n) for n in body.get("soft", [])}
        three = {norm(n) for n in body.get("three_star", ["流萤"])}
        skip = {norm(n) for n in body.get("skip", [])}
        max_rolls = int(body.get("max_rolls", 20))
        always = {norm(n) for n in body.get("always_buy", [])}
        must.update(always)
        stop_done = bool(body.get("stop_when_done", False))
        stop_targets = {norm(n) for n in body.get("stop_targets", [])}
        want: dict[str, int] = {norm(k): int(v) for k, v in (body.get("want") or {}).items()}

        f = self.ensure_board()
        if f.page == Page.BOARD and body.get("auto", True):
            copies: dict[str, list[int]] = {}
            fielded = set()
            for s in self.slots(f):
                n = norm(s.get("name"))
                if not n or s.get("stars") is None:
                    continue
                copies.setdefault(n, []).append(s["stars"])
                if s["zone"] != "bench":
                    fielded.add(n)
            for n, stars in copies.items():
                if n in skip:
                    continue
                if n in three:
                    if max(stars) < 3:
                        eq = sum(1 if st == 1 else 3 if st == 2 else 9 for st in stars)
                        want[n] = max(want.get(n, 0), 9 - eq)
                elif max(stars) == 1 and (n in fielded or n in want or n in must or n in soft):
                    want[n] = max(want.get(n, 0), 3 - stars.count(1))
            for n in must | soft:
                want.setdefault(n, 1)
        f = self.open_shop(f)
        if f.page != Page.SHOP:
            return {"error": f"无法打开商店（{f.page.value}）", "want": want}
        log, bought, seen, rolls = [], {}, set(), 0
        while True:
            if self.b.abort_flag:
                return {"error": "搜牌已中止", "gold": f.gold, "rolls": rolls, "bought": bought, "log": log}
            offers = f.offers
            gold = f.gold
            log.append(f"[{rolls}] {gold} " + " ".join(f"{o.name}{o.cost}" + ("★2" if o.stars == 2 else "")
                                                     for o in offers))
            seen |= {norm(o.name) for o in offers if norm(o.name) in NOTE}
            for o in sorted(offers, key=lambda o: -o.column):
                # 买卡必须精确匹配名字：包含匹配会把「砂金」当成「砂金·戏浪」、「刃」当成「千冶·刃」
                n = norm(o.name)
                if gold is None or (n not in always and want.get(n, 0) <= 0):
                    continue
                limit = 0 if (n in must or n in three) else floor - 3 if n in soft else floor
                if gold - o.cost < limit:
                    continue
                tap_wait(*o.point, area=SHOP_ROW_AREA)
                f2 = self.frame()
                if f2.gold is not None and f2.gold < gold:
                    if n not in always:
                        want[n] -= 3 ** max(0, (o.stars or 1) - 1)
                    bought[n] = bought.get(n, 0) + 1
                    gold = f2.gold
                    log.append(f"   买 {o.name} -> {gold}")
                else:
                    log.append(f"   买 {o.name} 失败（备战席满？）")
                    if stop_targets or n in always:
                        return {"error": f"购买目标 {o.name} 失败，先检查备战席再搜牌",
                                "gold": f2.gold, "rolls": rolls, "bought": bought, "log": log}
                    want[n] = 0
                f = f2
                if f.page != Page.SHOP:
                    return {"needs_review": True, "reason": "买卡后离开商店，先处理当前页面",
                            "gold": f.gold, "rolls": rolls, "bought": bought, "log": log}
            if stop_targets and all(want.get(n, 0) <= 0 for n in stop_targets):
                break
            if stop_done and all(v <= 0 for v in want.values()):
                break
            price = f.shop_refresh_price
            price = 2 if price is None else price
            if gold is None or gold - price < floor or rolls >= max_rolls or f.page != Page.SHOP:
                break
            f = self.refresh_shop(f)
            rolls += 1
        if body.get("close", True):
            f = self.ensure_board(f)
        return {"gold": f.gold, "rolls": rolls, "bought": bought,
                "still_want": {k: v for k, v in want.items() if v > 0}, "seen": sorted(seen), "log": log}

    # -- 经验
    def exp(self, body):
        times = int(body.get("times", 0))
        until = body.get("until_level")
        floor = int(body.get("floor", 0))
        f = self.ensure_board()
        n = 0
        while f.page == Page.BOARD and (times == 0 or n < times):
            if self.b.abort_flag:
                return {"error": "买经验已中止", "bought": n, "gold": f.gold, "level": f.level}
            if until and f.level is None:
                return {"error": "读不到等级，停止买经验", "bought": n, "gold": f.gold}
            if f.gold is None:
                return {"error": "读不到金币，停止买经验", "bought": n, "level": f.level}
            if until and f.level is not None and f.level >= int(until):
                break
            cost = f.experience_cost or 4
            if f.gold is None or f.gold - cost < floor:
                break
            if times == 0 and not until:
                break
            before = (f.gold, f.level, f.experience)
            tap_wait(*XP_BTN, area=(100, 820, 480, 1010), timeout=2.0)
            f = self.frame()
            if before == (f.gold, f.level, f.experience):
                return {"error": "买经验后局面未变化，停止重试", "bought": n, "gold": f.gold, "level": f.level}
            n += 1
        return {"bought": n, "gold": f.gold, "level": f.level, "exp": f.experience, "capacity": f.capacity}

    # -- 角色
    def _find_slots(self, f, name=None, zone=None, index=None, bench_first=True):
        slots = self.slots(f)
        if zone is not None and index is not None:
            return [s for s in slots if s["zone"] == zone and s["index"] == int(index)]
        hits = [s for s in slots if s["occupied"] and self.same(s.get("name") or s.get("guess"), name)]
        hits.sort(key=lambda s: (s["zone"] != "bench") if bench_first else (s["zone"] == "bench"))
        return hits

    def sell(self, body):
        """出售角色：targets = [{"name":..}|{"zone":..,"index":..}]，可带 "stars"；默认只卖备战席。"""
        results = []
        allow_field = bool(body.get("allow_field", False))
        f = self.ensure_board()
        for t in body.get("targets", []):
            if f.page != Page.BOARD:
                break
            cands = self._find_slots(f, t.get("name"), t.get("zone"), t.get("index"))
            if not allow_field and "zone" not in t:
                cands = [s for s in cands if s["zone"] == "bench"]
            if "stars" in t:
                cands = [s for s in cands if s.get("stars") == int(t["stars"])]
            if not cands:
                results.append({**t, "ok": False, "reason": "没找到"})
                continue
            s = cands[0]
            tap_wait(*s["point"], area=DETAIL_AREA)
            d = self.frame()
            name = d.employee_name(local=True) if d.page == Page.DETAIL else None
            expect = t.get("name") or s.get("name")
            btn = d.find("出售", area=ui.DETAIL_SELL) if d.page == Page.DETAIL else None
            if d.page != Page.DETAIL or btn is None or (expect and not self.same(name, expect)):
                results.append({**t, "ok": False, "reason": f"详情核对失败（{d.page.value} {name}）"})
                f = self.ensure_board(d)
                continue
            tap_wait(*btn.point, area=(0, 0, 1920, 1080))
            f = self.frame()
            if f.page != Page.BOARD:
                f = self.ensure_board(f)
            results.append({**t, "ok": True, "sold": name, "gold": f.gold})
        return {"results": results, "gold": f.gold, "page": f.page.name}

    def move(self, body):
        """移动角色：moves = [{"from":{"zone","index"}|{"name"}, "to":{"zone","index"}}]。"""
        f = self.ensure_board()
        out = []
        stale = False
        for m in body.get("moves", []):
            src = m["from"]
            if stale and src.get("name"):  # 只有按名字找人时才需要重读棋盘
                f, stale = self.frame(), False
            cands = self._find_slots(f, src.get("name"), src.get("zone"), src.get("index"),
                                     bench_first=src.get("bench_first", True))
            dst = self._find_slots(f, zone=m["to"]["zone"], index=m["to"]["index"])
            if not cands or not dst:
                out.append({**m, "ok": False, "reason": "格子没找到"})
                continue
            a, b = cands[0]["point"], dst[0]["point"]
            before = shot()
            drag(a[0], a[1], b[0], b[1], duration=float(m.get("duration", 0.6)))
            pad = 90
            area = (max(0, min(a[0], b[0]) - pad), max(0, min(a[1], b[1]) - pad),
                    min(1920, max(a[0], b[0]) + pad), min(1080, max(a[1], b[1]) + pad))
            wait_change(before, area=area, timeout=1.5, quiet=2.5)
            stale = True
            out.append({**m, "ok": True})
        f = self.frame()
        if f.page != Page.BOARD:
            f = self.ensure_board(f)
        return {"capacity": f.capacity, "moves": out, "slots": [s for s in self.slots(f) if s["occupied"]] if f.page == Page.BOARD else None}

    # -- 装备
    def _locate_item(self, f, name):
        inv = self.inventory(f)["items"]
        for it in inv:
            if it["name"] and self.same(it["name"], name):
                return it["point"]
        for it in inv:  # 图标没认出的（剧本装备/星徽等）逐个看说明
            if it["name"] is None:
                _, n, _ = self.item_name_by_tooltip(it["point"])
                self.dismiss_tooltip()
                if self.same(n, name):
                    return it["point"]
        return None

    def equip(self, body):
        """把物品栏里的装备拖给角色：items=[名字...]，target={"zone","index"}|{"name"}。冶金炉/扳手用 item="冶金炉"/"扳手"。"""
        f = self.ensure_board()
        t = body["target"]
        cands = self._find_slots(f, t.get("name"), t.get("zone"), t.get("index"), bench_first=False)
        if not cands:
            return {"error": "目标角色没找到"}
        target = cands[0]["point"]
        out = []
        for item in body.get("items", []):
            if item in ("扳手", "wrench"):
                src = self.inventory(f)["wrench"] or [1847, 143]
            elif item in ("冶金炉", "furnace"):
                src = [1765, 143]
            elif isinstance(item, (list, tuple)):
                src = list(item)
            else:
                src = self._locate_item(f, item)
            if not src:
                out.append({"item": item, "ok": False, "reason": "物品栏没找到"})
                continue
            before = shot()
            drag(src[0], src[1], target[0], target[1] - 5, duration=1.2)
            wait_change(before, timeout=2.5)
            f = self.frame()
            out.append({"item": item, "ok": True})
        return {"results": out, "inventory": self.inventory(f), "page": f.page.name}

    def _item_point(self, f, item):
        if isinstance(item, (list, tuple)):
            return list(item)
        if isinstance(item, dict) and "point" in item:
            return list(item["point"])
        return self._locate_item(f, item)

    def combine(self, body):
        """物品栏内合成：把 a 拖到 b 上，再点确认 ✓。a/b 为装备名或 [x,y]；pairs=[[a,b],...] 可连做。
        confirm=False 只拖不点（看弹窗）。"""
        f = self.ensure_board()
        pairs = body.get("pairs") or [[body.get("a"), body.get("b")]]
        out = []
        for a, b in pairs:
            pa = self._item_point(f, a)
            pb = self._item_point(f, b)
            if not pa or not pb or pa == pb:
                out.append({"a": a, "b": b, "ok": False, "reason": "物品栏没找到"})
                continue
            names_before = [it["name"] for it in self.inventory(f)["items"]]
            before = shot()
            drag(pa[0], pa[1], pb[0], pb[1], duration=1.0)
            wait_change(before, timeout=2.0, quiet=2.0)
            if body.get("confirm", True):
                before = shot()
                tap(1820, pb[1] + COMBINE_OK_DY)
                wait_change(before, area=INVENTORY_AREA, timeout=2.0, quiet=2.0)
            f = self.frame()
            if f.page != Page.BOARD:
                f = self.ensure_board(f)
            names_after = [it["name"] for it in self.inventory(f)["items"]]
            new = [n for n in names_after if n and names_after.count(n) > names_before.count(n)]
            out.append({"a": a, "b": b, "ok": len(names_after) < len(names_before), "new": sorted(set(new))})
        return {"results": out, "inventory": self.inventory(f), "page": f.page.name}

    def sell_items(self, body):
        """出售物品栏装备：names=[...]；keep=[...] 时卖掉除 keep 外所有已识别装备。"""
        f = self.ensure_board()
        names = body.get("names")
        keep = body.get("keep")
        sold = []
        for _ in range(12):
            inv = self.inventory(f)["items"]
            pick = None
            for it in inv:
                n = it["name"]
                if names is not None and n and any(self.same(n, x) for x in names):
                    pick = it
                    break
                if keep is not None and n and not any(self.same(n, x) for x in keep):
                    pick = it
                    break
            if pick is None:
                break
            tf, tip, btn = self.item_name_by_tooltip(pick["point"])
            if btn is None or not self.same(tip, pick["name"]):
                self.dismiss_tooltip()
                break
            tap_wait(*btn.point, area=INVENTORY_AREA)
            f = self.frame()
            sold.append({"name": tip, "gold": f.gold})
            if names is not None:
                names = [x for x in names if not self.same(x, tip)] + \
                        [x for x in names if self.same(x, tip)][1:]
        return {"sold": sold, "gold": f.gold, "inventory": self.inventory(f)}

    def craft(self, body):
        """打开角色的装备推荐面板：pick=None 只列出可穿戴/合成项；pick=序号(从1起) 或 "all" 依次执行。"""
        f = self.ensure_board()
        t = body["target"]
        cands = self._find_slots(f, t.get("name"), t.get("zone"), t.get("index"), bench_first=False)
        if not cands:
            return {"error": "目标角色没找到"}
        tap_wait(*cands[0]["point"], area=DETAIL_AREA)
        d = self.frame()
        rec = d.find(*ui.DETAIL_RECOMMEND, area=(1440, 780, 1600, 860)) or d.find("推荐", area=(1400, 760, 1640, 880))
        if d.page != Page.DETAIL or rec is None:
            self.ensure_board(d)
            return {"error": f"详情页没找到推荐按钮（{d.page.value}）"}
        tap_wait(*rec.point, area=(900, 350, 1450, 1000))
        e = self.frame()

        def options(fr):
            opts = []
            for i, btn in enumerate(sorted(fr.equipment_actions(), key=lambda lb: (lb.point[1], lb.point[0]))):
                x, y = btn.point
                name, parts = None, []
                try:
                    eq = fr.equipment_option(btn)
                    name, parts = eq.name, [p for p in eq.ingredients if p]
                except Exception:
                    pass
                if name is None:
                    name = equipment_name(fr.crop((x - 62, y - 97, x + 8, y - 27)))
                opts.append({"index": i + 1, "action": btn.text, "point": list(btn.point),
                             "result": name, "from": parts or None})
            return opts

        opts = options(e)
        done = []
        pick = body.get("pick")
        if pick is not None:
            todo = [o["index"] for o in opts] if pick == "all" else [int(pick)]
            for idx in todo:
                cur = options(self.frame())
                o = next((c for c in cur if c["index"] == idx), None) if pick != "all" else (cur[0] if cur else None)
                if o is None:
                    break
                tap_wait(*o["point"], area=(1340, 750, 1920, 860))
                done.append(o)
        f = self.ensure_board()
        return {"options": opts, "done": done, "page": f.page.name,
                "inventory": self.inventory(f) if f.page == Page.BOARD else None}

    # -- 节点选择
    def encounter(self, body):
        """遭遇节点：优先难度 1；没有难度 1 且还能刷新时先刷新一次；然后选最低难度。"""
        f = self.frame()
        if f.page != Page.CHOICE or f.choice != "遭遇节点":
            return {"error": f"不是遭遇节点（{f.page.value} {f.choice}）"}
        digits = "一二三四五六七八九"

        def levels(fr):
            out = []
            for lb in fr.texts((215, 250, 1720, 1005)):
                if m := re.fullmatch(r"遭遇其([一二三四五六七八九1-9])", lb.text):
                    v = m[1]
                    out.append((digits.index(v) + 1 if v in digits else int(v), lb))
            return sorted(out, key=lambda t: t[0])

        lv = levels(f)
        refreshed = False
        if lv and lv[0][0] > 1 and body.get("refresh", True):
            rest = next((lb for lb in f.texts((500, 860, 960, 1010)) if "剩余次数" in lb.text), None)
            m = re.search(r"(\d+)", rest.text) if rest else None
            if rest and m and int(m[1]) > 0:
                tap_wait(rest.point[0] - 100, rest.point[1], timeout=3.0)
                f = self.frame()
                lv = levels(f)
                refreshed = True
        if not lv:
            return {"error": "没读到遭遇选项", "refreshed": refreshed}
        level, lb = lv[0]
        tap_wait(lb.point[0], lb.point[1] + 130, timeout=1.5)
        f = self.frame()
        btn = f.find("选择", area=(800, 850, 1400, 950))
        if btn:
            tap_wait(*btn.point, timeout=4.0)
        f = self.frame()
        return {"picked": lb.text, "level": level, "refreshed": refreshed, "page": f.page.name, "gold": f.gold}

    # -- 主角光环
    @staticmethod
    def halo_state(f):
        def side(x1, x2):
            title = next((lb for lb in f.texts((x1, 400, x2, 450))), None)
            desc = "".join(lb.text for lb in f.texts((x1, 450, x2, 650)))
            return {"title": title.text if title else None, "desc": desc}
        cost = next((lb.text for lb in f.texts((600, 110, 1320, 160))), None)
        return {"left": side(100, 760), "right": side(1150, 1820), "refresh": cost,
                "gold": next((lb.text for lb in f.texts((1300, 930, 1450, 980)) if lb.text.isdigit()), None)}

    def halo(self, body):
        """光环页：action=read | refresh(side=left/right) | pick(side, slot_index 前台序号0起)。"""
        f = self.frame()
        if f.page != Page.HALO:
            adv = f.find(*ui.BOARD_ADVANCE, area=ui.BOARD_FIGHT) if f.page == Page.BOARD else None
            if adv and adv.text == "选择主角":
                tap_wait(*adv.point, timeout=6.0)
                for _ in range(8):
                    f = self.frame()
                    if f.page == Page.HALO:
                        break
                    time.sleep(0.6)
            if f.page != Page.HALO:
                return {"error": f"不在光环页（{f.page.value}）"}
        action = body.get("action", "read")
        if action == "refresh":
            x = 471 if body.get("side") == "left" else 1549
            tap_wait(x, 774, area=(100, 380, 1820, 660), timeout=3.0)
            f = self.frame()
        elif action == "pick":
            x = 420 if body.get("side") == "left" else 1500
            tap_wait(x, 435, timeout=1.5)
            i = int(body.get("slot_index", 0))
            px, py = ui.HALO_FRONT[i]
            before = shot()
            drag(px, py, *ui.HALO_TARGET, duration=1.0)
            wait_change(before, timeout=2.5)
            f = self.frame()
            if body.get("confirm", True):
                btn = f.find("确认", area=ui.HALO_CONFIRM)
                if btn:
                    tap_wait(*btn.point, timeout=5.0)
                    for _ in range(6):
                        f = self.frame()
                        if f.page != Page.HALO:
                            break
                        time.sleep(0.6)
                return {"page": f.page.name}
        return {"halo": self.halo_state(f) if f.page == Page.HALO else None, "page": f.page.name}

    # -- 出战
    def fight(self, body):
        f = self.ensure_board()
        if f.page != Page.BOARD:
            return {"error": f"不在棋盘（{f.page.value}）"}
        adv = f.find(*ui.BOARD_ADVANCE, area=ui.BOARD_FIGHT)
        if adv is None:
            return {"error": "没找到出战按钮"}
        if adv.text == "选择主角":
            return {"error": "需要先选主角光环（cw_halo）"}
        tap_wait(*adv.point, timeout=4.0)
        # 必须确认已离开备战页，否则 run 的第一步会对 BOARD 执行 M7A 的整套自动备战。
        for _ in range(12):
            f = self.frame()
            if f.page != Page.BOARD:
                break
            time.sleep(0.5)
        if f.page == Page.BOARD:
            return {"error": "点击出战后仍在备战页", "page": f.page.name}
        stops = body.get("stop_pages") or ["BOARD", "CHOICE", "SHOP", "HALO", "GARDEN", "HACK", "DANCE",
                                           "RANGER", "TEA", "RAIL_SHOP", "BLACK_MARKET", "BENCH_FULL", "HOME",
                                           "UNKNOWN", "EQUIPMENT", "DETAIL"]
        r = self.b.run({"stop_pages": stops, "max_seconds": float(body.get("max_seconds", 480)),
                        "max_steps": 40})
        r.pop("log", None)
        r.pop("screenshot", None)
        return r
