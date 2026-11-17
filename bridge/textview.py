"""文字化工具层：在 actions.Actions 之上加
- 装备图标识别（物品栏 / 顶部道具 / 角色身上），认不出的自动点开看一次名字并学习；
- look：任何页面都输出一段紧凑文字，不用看截图；
- 动作执行后附带 after（棋盘文字），省掉核对截图；
- 宏：wrench / furnace / rail / tea / halo(后台角色)。
由 server.py 用 TextActions 替换 Actions；改完代码调 /reload 热加载。
"""
from __future__ import annotations

import json
import os
import re
import time

import numpy as np

import actions as A
from actions import Actions, Page, drag, shot, tap, tap_wait, ui, wait_change, equipment_name
from icons import IconLib

TOP_CELLS = ((1772, 160), (1852, 160))           # 物品栏顶部消耗品
CARD_DX = (-29, 0, 29)                           # 卡片下三个装备小图标
RAIL_BTN = (545, 286)
RAIL_CLOSE = (12, 12)
HALO_BACK = tuple((712 + 105 * i, 760) for i in range(6))
TEA_BTN = {"left": (386, 842), "right": (1540, 842)}
ZONE_CN = {"front": "前", "back": "后", "bench": "备"}

LIB = IconLib()
_KNOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge", "characters.json")
POS = {"前台": "前", "后台": "后", "前后台": "前后"}
ALIAS = {"CT": "开拓者•欢愉"}


def _chars():
    try:
        with open(_KNOW, encoding="utf-8") as fh:
            return {A.norm(c["name"]): c for c in json.load(fh)}
    except Exception:
        return {}


CHARS = _chars()


def char_info(name):
    n = A.norm(ALIAS.get(name, name))
    if n in CHARS:
        return CHARS[n]
    hits = [c for k, c in CHARS.items() if n and (n in k or k in n)]
    return hits[0] if len(hits) == 1 else None


def _card_boxes(f, point):
    x, y = point
    return [f.crop((x + dx - 9, y + 68, x + dx + 10, y + 84)) for dx in CARD_DX]


CARD_LAYOUT = {1: (-1,), 2: (-21, 19), 3: (-41, -1, 39)}


def _card_icons(f, point, n):
    """卡片下方装备图标的图案区（26x26，和物品栏 48x48 截块同比例，可直接用 inv 库比对）。"""
    x, y = point
    return [f.crop((x + dx - 13, y + 68, x + dx + 13, y + 94)) for dx in CARD_LAYOUT.get(n, ())]


def _filled(mark):
    return bool(mark.size > 0 and mark.std(axis=(0, 1)).max() >= 38
                and ((mark.max(axis=2) > 155) & (np.ptp(mark, axis=2) > 28)).mean() >= 0.19)


class TextActions(Actions):
    lib = LIB

    # ------------------------------------------------------------ 角色名：头像猜测不准，用 M7A 读棋盘（带缓存）
    def slots(self, f):
        base = super().slots(f)
        try:
            self.b._arm(60)
            _, board = self.b.war._read_board(f)
        except Exception:
            board = None
        if board is None:
            return base
        names = {(s.zone, s.index): s.unit for s in board.slots if s.unit is not None}
        unknown = []
        for s in base:
            u = names.get((s["zone"], s["index"]))
            if u is not None:
                s["name"] = A.norm(u.name)
                s.pop("guess", None)
                if u.stars:
                    s["stars"] = u.stars
            elif s["occupied"] and not s.get("blocked"):
                unknown.append(s)
        for s in unknown:  # M7A 没认出的格子：点开详情读名字，并收录头像
            try:
                name = self._name_by_detail(s)
            except Exception:
                name = None
            if name:
                s["name"] = name
                s.pop("guess", None)
        return base

    def _name_by_detail(self, s):
        from portraits import face_of
        f0 = self.frame()
        mark = f0.slot_mark(tuple(s["point"]))
        tap_wait(*s["point"], area=A.DETAIL_AREA)
        d = self.frame()
        name = d.employee_name(local=True) if d.page == Page.DETAIL else None
        A.auto.press_key("esc")
        time.sleep(0.4)
        name = A.norm(name)
        if name:
            group = "bench" if s["zone"] == "bench" else "field"
            try:
                self.b.portraits.add(face_of(mark), group, name, self.b.portraits.output_of(name))
            except Exception:
                pass
        return name or None

    # ------------------------------------------------------------ 装备识别
    def _cell_crop(self, f, x, y):
        return f.crop((x - 24, y - 24, x + 24, y + 24))

    def _learn_by_tooltip(self, point, crop, kind):
        _, name, _ = self.item_name_by_tooltip(point)
        A.auto.press_key("esc")
        time.sleep(0.4)
        name = A.norm(name)
        if name:
            self.lib.learn(name, crop, kind)
        return name or None

    def inventory(self, f, learn=False):
        try:
            cells = self.inventory_cells(f)
        except Exception:
            cells = []
        items, unknown = [], []
        for (x, y), m7a in sorted(cells, key=lambda c: (c[0][1], c[0][0])):
            crop = self._cell_crop(f, x, y)
            name, _, _ = self.lib.match(crop, "inv")
            if name is None and m7a:
                name = m7a
                self.lib.learn(m7a, crop, "inv")
            items.append({"point": [x, y], "name": name})
            if name is None:
                unknown.append((len(items) - 1, (x, y), crop))
        if learn and unknown:
            for i, pt, crop in unknown:
                items[i]["name"] = self._learn_by_tooltip(pt, crop, "inv")
            f = self.frame()
        tool = None
        try:
            tool = f.disassembly_tool()
        except Exception:
            pass
        return {"items": items, "wrench": list(tool) if tool else None, "top": self.top_items(f, learn)}

    def top_items(self, f, learn=False):
        out = []
        for x, y in TOP_CELLS:
            crop = self._cell_crop(f, x, y)
            if crop.std(axis=(0, 1)).max() < 25:
                continue
            name, _, _ = self.lib.match(crop, "top")
            if name is None and learn:
                name = self._learn_by_tooltip((x, y), crop, "top")
            cnt = None
            try:
                raw = f.read_local((x + 18, y + 14, x + 46, y + 46), recognition_only=True) or ""
                raw = raw.translate(str.maketrans("IlL|i", "11111"))
                cnt = int(raw) if raw.isdigit() else None
            except Exception:
                pass
            out.append({"point": [x, y], "name": name, "count": cnt})
        return out

    def card_items(self, f, slot):
        n = slot.get("equipment") or sum(_filled(m) for m in _card_boxes(f, slot["point"]))
        x, y = slot["point"]
        names = []
        for dx in CARD_LAYOUT.get(n, ()):
            best = None
            for oy in range(-3, 7, 1):
                for ox in range(-6, 7, 1):
                    cx, cy = x + dx + ox, y + 81 + oy
                    mark = f.crop((cx - 13, cy - 13, cx + 13, cy + 13))
                    r = self._best(mark, kinds=("card", "inv"))
                    if r and (best is None or r[1] > best[1]):
                        best = r
            ok = best and best[1] >= 0.72 and best[2] >= 0.05
            names.append(best[0] if ok else "?")
        return names

    def _learn_card(self, f, point, n, name):
        """新穿上的装备：在各图标位附近找与物品栏同名图标最像的截块，存进 card 库。"""
        from icons import _prep, _score
        refs = [r for nm, r in self.lib.entries["inv"] if nm == name]
        if not refs:
            return
        x, y = point
        best = None
        for dx in CARD_LAYOUT.get(n, ()):
            for oy in range(-3, 7):
                for ox in range(-6, 7):
                    cx, cy = x + dx + ox, y + 81 + oy
                    mark = f.crop((cx - 13, cy - 13, cx + 13, cy + 13))
                    if mark.size == 0:
                        continue
                    pm = _prep(mark, "inv")
                    sc = max(max(_score(pm, r), _score(pm[:18], r[:18])) for r in refs)
                    if best is None or sc > best[0]:
                        best = (sc, mark)
        if best and best[0] >= 0.45:
            self.lib.learn(name, best[1], "card")

    def _best(self, crop, kinds=("inv",)):
        from icons import _prep, _score
        if crop.size == 0:
            return None
        c = _prep(crop, "inv")
        best = {}
        for k in kinds:
            for n, ref in self.lib.entries[k]:
                # 左下角常压着层数数字，只看上半部再取较高分
                sc = max(_score(c, ref), _score(c[:18], ref[:18]))
                best[n] = max(best.get(n, -1), sc)
        if not best:
            return None
        r = sorted(best.items(), key=lambda kv: -kv[1])
        return r[0][0], r[0][1], r[0][1] - (r[1][1] if len(r) > 1 else 0)

    def _locate_item(self, f, name):
        for it in self.inventory(f)["items"]:
            if it["name"] and self.same(it["name"], name):
                return it["point"]
        for it in self.inventory(f, learn=True)["items"]:
            if it["name"] and self.same(it["name"], name):
                return it["point"]
        return None

    # ------------------------------------------------------------ 文字视图
    def board_text(self, f, learn=False):
        L = []
        L.append(f"{f.stage} 金{f.gold} 血{f.squad_health} Lv{f.level} 经验{_xp(f)} 人口{_cap(f)}")
        rail = next((re.sub(r"\D", "", lb.text) for lb in f.texts((380, 265, 640, 315)) if re.search(r"\d", lb.text)), None)
        orbs = len(f.reward_orbs())
        boxes = [tuple(lb.point) for lb in f.texts(ui.BENCH_BOX) if lb.text == "开启"]
        adv = f.find(*ui.BOARD_ADVANCE, area=ui.BOARD_FIGHT)
        L.append(f"开拓力{rail or '-'} 球{orbs} 宝箱{boxes or '无'} 按钮[{adv.text if adv else '-'}]")
        syn = self.synergies(f)
        if syn:
            L.append("羁绊 " + " ".join(syn))
        slots = [s for s in self.slots(f) if s["occupied"]]
        for zone in ("front", "back", "bench"):
            row = []
            for s in slots:
                if s["zone"] != zone:
                    continue
                nm = s.get("name") or ((s.get("guess") or "") + "?")
                info = char_info(nm.rstrip("?"))
                role = POS.get(info["position"], "") + str(info["cost"]) + "费" if info else ""
                eq = self.card_items(f, s) if s.get("equipment") else []
                eqs = f"[{','.join(eq)}]" if eq else ""
                row.append(f"{s['index']}:{nm}★{s.get('stars')}{('/' + role) if role else ''}{eqs}")
            L.append(f"{ZONE_CN[zone]}: " + ("  ".join(row) if row else "空"))
        inv = self.inventory(f, learn=learn)
        L.append("物品: " + ("  ".join(f"{it['name'] or '?'}@{it['point'][0]},{it['point'][1]}" for it in inv["items"]) or "空"))
        tops = "  ".join(f"{t['name'] or '?'}x{t['count']}" for t in inv["top"])
        if tops:
            L.append("道具: " + tops)
        return "\n".join(L)

    def synergies(self, f):
        labels = sorted(f.texts((40, 210, 330, 820)), key=lambda lb: lb.point[1])
        out, last = [], None
        for lb in labels:
            if re.fullmatch(r"[\d/]+", lb.text):
                if last is not None:
                    out[-1] = f"{out[-1]}({lb.text})"
                    last = None
                continue
            if len(lb.text) >= 2:
                out.append(lb.text)
                last = lb
        return out

    def page_text(self, f):
        p = f.page
        head = f"[{p.value}]"
        if p == Page.SHOP:
            try:
                offs = f.offers
                row = "  ".join(f"{o.column}:{o.name}{o.cost}费{_pos(o.name)}" for o in offs)
            except Exception:
                row = "?"
            return f"{head} {f.stage} 金{f.gold} 刷新{getattr(f, 'shop_refresh_price', None)}\n{row}"
        if p == Page.CHOICE:
            return f"{head} {f.choice}\n" + self.choice_text(f)
        if p == Page.HALO:
            h = self.halo_state(f)
            return (f"{head} 刷新{h['refresh']} 金{h['gold']}\n左 {h['left']['title']}：{h['left']['desc']}\n"
                    f"右 {h['right']['title']}：{h['right']['desc']}")
        if p == Page.RAIL_SHOP:
            return f"{head}\n" + self.rail_text(f)
        return head + "\n" + " / ".join(f"{lb.text}@{lb.point[0]},{lb.point[1]}" for lb in f.labels)

    def choice_text(self, f):
        """按卡片标题位置分栏；标题读不到时按 x 等距聚类。"""
        if "武装箱" in (f.choice or ""):
            items = [lb for lb in f.texts((420, 300, 1720, 372)) if len(lb.text) >= 2]
            return "\n".join(f"{i}@{lb.point[0]},{lb.point[1] - 90} {lb.text}" for i, lb in enumerate(sorted(items, key=lambda l: l.point[0])))
        try:
            sup = f.supply_cards
            sup = sup() if callable(sup) else sup
        except Exception:
            sup = None
        if sup:
            rows = []
            for i, c in enumerate(sup):
                head, item = c[0], c[1] if len(c) > 1 else None
                t = head.text if hasattr(head, "text") else head.get("text")
                pt = head.point if hasattr(head, "point") else head.get("point")
                info = char_info(A.norm(t))
                extra = f"({info['cost']}费/{POS.get(info['position'], '')}/{'/'.join(info.get('traits', []))})" if info else ""
                rows.append(f"{i}@{pt[0]},{pt[1] - 60} {t}{extra} + {item}")
            btn = f.find("确认", area=(600, 900, 1900, 1060))
            if btn:
                rows.append(f"确认@{btn.point[0]},{btn.point[1]}")
            return "\n".join(rows)
        titles = []
        for attr in ("recruitment_cards", "item_cards"):
            try:
                v = getattr(f, attr)
                if callable(v):
                    v = v()
            except Exception:
                v = None
            for c in v or ():
                t = c.text if hasattr(c, "text") else c.get("text")
                pt = c.point if hasattr(c, "point") else c.get("point")
                titles.append((t, tuple(pt)))
            if titles:
                break
        labels = [lb for lb in f.texts((60, 120, 1860, 1010))]
        if not titles:
            try:
                inv_titles = list(f.investment_titles or ())
            except Exception:
                inv_titles = []
            for t in inv_titles:
                lb = next((l for l in labels if l.text == t or l.text.startswith(t)), None)
                if lb:
                    titles.append((t, tuple(lb.point)))
        if not titles:
            cols = self.choice_columns(f)
            return "\n".join(f"x{c['x']}: {c['text']}" for c in cols)
        titles.sort(key=lambda t: t[1][0])
        cols = {i: [] for i in range(len(titles))}
        for lb in labels:
            i = min(range(len(titles)), key=lambda k: abs(titles[k][1][0] - lb.point[0]))
            if abs(titles[i][1][0] - lb.point[0]) <= 260:
                cols[i].append(lb)
        out = []
        for i, (t, pt) in enumerate(titles):
            body = " ".join(lb.text for lb in sorted(cols[i], key=lambda l: (l.point[1] // 12, l.point[0])) if lb.text != t)
            out.append(f"{i}@{pt[0]},{pt[1]} 【{t}】 {body}")
        btn = f.find("确认", area=(600, 900, 1900, 1060))
        if btn:
            out.append(f"确认@{btn.point[0]},{btn.point[1]}")
        return "\n".join(out)

    def look(self, body):
        f = self.frame()
        if f.page == Page.BOARD:
            text = self.board_text(f, learn=body.get("learn", True))
        else:
            text = self.page_text(f)
        return {"text": text}

    def _after(self, res):
        try:
            f = self.frame()
            res["after"] = self.board_text(f) if f.page == Page.BOARD else self.page_text(f)
        except Exception as e:
            res["after"] = f"读取失败 {e}"
        for k in ("inventory", "slots"):
            res.pop(k, None)
        return res

    # ------------------------------------------------------------ 动作 + 学习
    def equip(self, body):
        f = self.ensure_board()
        t = body["target"]
        cands = self._find_slots(f, t.get("name"), t.get("zone"), t.get("index"), bench_first=False)
        if not cands:
            return {"error": "目标角色没找到"}
        slot = cands[0]
        f = self.ensure_board()  # 读名字时可能点开过详情，先确保回到棋盘再拖
        out = []
        for item in body.get("items", []):
            if item in ("扳手", "wrench"):
                inv = self.inventory(f)
                src = inv["wrench"] or next((t2["point"] for t2 in inv["top"] if t2["name"] == "扳手"), [1852, 160])
            elif item in ("冶金炉", "furnace"):
                return {"error": "冶金炉不能拖到角色身上（会重洗全身装备），用 furnace item=散件名"}
            elif isinstance(item, (list, tuple)):
                src = list(item)
            else:
                src = self._locate_item(f, item)
            if not src:
                out.append({"item": item, "ok": False, "reason": "物品栏没找到"})
                continue
            before_boxes = [_filled(m) for m in _card_boxes(f, slot["point"])]
            named = isinstance(item, str) and item not in ("扳手", "wrench")
            count_before = self._count_item(f, item) if named else None
            ok = False
            for attempt in range(2):  # 拖完核对物品栏：那件还在就说明没装上，换个落点再试一次
                before = shot()
                ty = slot["point"][1] - 5 if attempt == 0 else slot["point"][1] - 35
                drag(src[0], src[1], slot["point"][0], ty, duration=1.4)
                wait_change(before, timeout=2.5)
                time.sleep(0.8)
                f = self.ensure_board(self.frame())
                if not named:
                    ok = True
                    break
                if self._count_item(f, item) < count_before:
                    ok = True
                    break
                src = self._locate_item(f, item) or src
            after_boxes = [_filled(m) for m in _card_boxes(f, slot["point"])]
            n = sum(after_boxes)
            if ok and named and n == sum(before_boxes) + 1:
                self._learn_card(f, slot["point"], n, A.norm(item))
            out.append({"item": item, "ok": ok, "slots_filled": sum(after_boxes),
                        **({} if ok else {"reason": "拖了两次物品栏里还在，没装上（可能装备栏满了或目标不对）"})})
        return self._after({"results": out})

    def _count_item(self, f, name):
        name = A.norm(name)
        return sum(1 for it in self.inventory(f)["items"] if it["name"] and self.same(it["name"], name))

    def wrench(self, body):
        return self.equip({"items": ["扳手"], "target": body.get("target") or {"name": body.get("name")}})

    def furnace(self, body):
        f = self.ensure_board()
        pt = self._locate_item(f, body["item"])
        if not pt:
            return {"error": f"物品栏没有 {body['item']}"}
        src = next((t["point"] for t in self.top_items(f) if t["name"] == "冶金炉"), [1772, 160])
        before = shot()
        drag(src[0], src[1], pt[0], pt[1], duration=1.0)
        wait_change(before, timeout=2.0, quiet=2.0)
        return self._after({"ok": True, "item": body["item"]})

    def combine(self, body):
        return self._after(super().combine(body))

    def move(self, body):
        return self._after(super().move(body))

    def sell(self, body):
        return self._after(super().sell(body))

    # ------------------------------------------------------------ 开拓商店
    def rail_text(self, f):
        bal = next((lb.text for lb in f.texts((440, 90, 680, 135)) if lb.text.isdigit()), None)
        items = self.rail_items(f)
        return f"开拓力{bal}  " + "  ".join(f"{n}({c})" for n, c, _ in items)

    @staticmethod
    def rail_items(f):
        out = []
        for lb in sorted(f.texts((430, 355, 1430, 405)), key=lambda l: l.point[0]):
            if lb.text.isdigit():
                if out:
                    n, c, p = out[-1]
                    if c is None:
                        out[-1] = (n, int(lb.text), p)
                continue
            out.append((lb.text, None, lb.point))
        return out

    def rail(self, body):
        """开拓商店：action=read | buy names=[...] （按顺序买，买不起就停）。"""
        f = self.frame()
        if f.page != Page.RAIL_SHOP:
            f = self.ensure_board(f)
            tap_wait(*RAIL_BTN, timeout=3.0)
            f = self.frame()
            if f.page != Page.RAIL_SHOP:
                return {"error": f"没打开开拓商店（{f.page.value}）"}
        bought = []
        if body.get("action") == "buy":
            for want in body.get("names", []):
                hit = next((it for it in self.rail_items(f) if want in it[0]), None)
                if not hit:
                    bought.append({"name": want, "ok": False, "reason": "没有"})
                    continue
                before = shot()
                tap(hit[2][0], 300)
                wait_change(before, timeout=2.0)
                f = self.frame()
                bought.append({"name": want, "ok": True})
        text = self.rail_text(f)
        if body.get("close", True):
            tap_wait(*RAIL_CLOSE, timeout=2.0)
        return {"text": text, "bought": bought}

    # ------------------------------------------------------------ 幕后茶歇
    def tea(self, body):
        """茶歇：action=read side=left|right 打开该选项读子选项；
        action=choose index=0 选子选项并确认、开奖励、回棋盘。"""
        f = self.frame()
        if f.page != Page.TEA:
            return {"error": f"不在茶歇（{f.page.value}）"}
        act = body.get("action", "read")
        if act == "read" and body.get("side"):
            tap_wait(*TEA_BTN[body["side"]], timeout=2.0)
            for _ in range(5):
                f = self.frame()
                if self._tea_options(f):
                    break
                tap_wait(1300, 800, timeout=1.5)
            opts = self._tea_options(f)
            return {"text": "\n".join(f"{i}: {t}" for i, (t, _) in enumerate(opts)) or self.page_text(f)}
        if act == "choose":
            opts = self._tea_options(f)
            i = int(body.get("index", 0))
            if i >= len(opts):
                return {"error": "没有该选项", "text": self.page_text(f)}
            tap_wait(*opts[i][1], timeout=2.0)
            trail = []
            for _ in range(14):
                f = self.frame()
                if f.page != Page.TEA:
                    break
                btn = f.find("确认", area=(1100, 380, 1900, 1000)) or f.find("开启", area=(700, 900, 1220, 1040))
                if btn:
                    trail.append(btn.text)
                    tap_wait(*btn.point, timeout=3.0)
                    continue
                trail.append("继续")
                tap_wait(1300, 800, timeout=1.5)
            return self._after({"trail": trail})
        return {"text": self.page_text(f)}

    @staticmethod
    def _tea_options(f):
        boxes = [lb for lb in f.texts((1130, 380, 1870, 800)) if len(lb.text) >= 2]
        boxes.sort(key=lambda lb: lb.point[1])
        out, last_y = [], -999
        for lb in boxes:
            if lb.point[1] - last_y > 60:
                out.append([lb.text, lb.point])
            else:
                out[-1][0] += " " + lb.text
            last_y = lb.point[1]
        return [(t, p) for t, p in out if t != "返回"]

    # ------------------------------------------------------------ 光环：支持后台
    def halo(self, body):
        if body.get("action") == "pick" and body.get("zone") == "back":
            f = self.frame()
            if f.page != Page.HALO:
                return {"error": f"不在光环页（{f.page.value}）"}
            x = 420 if body.get("side") == "left" else 1500
            tap_wait(x, 435, timeout=1.5)
            px, py = HALO_BACK[int(body.get("slot_index", 0))]
            before = shot()
            drag(px, py, *ui.HALO_TARGET, duration=1.0)
            wait_change(before, timeout=2.5)
            f = self.frame()
            btn = f.find("确认", area=ui.HALO_CONFIRM)
            if btn:
                tap_wait(*btn.point, timeout=5.0)
                for _ in range(6):
                    f = self.frame()
                    if f.page != Page.HALO:
                        break
                    time.sleep(0.6)
            return self._after({"page": f.page.name})
        return super().halo(body)

    def teach(self, body):
        """手动教身上装备图标：target={"zone","index"}, names=[该角色身上的装备名...]。"""
        f = self.ensure_board()
        t = body["target"]
        cands = self._find_slots(f, t.get("name"), t.get("zone"), t.get("index"), bench_first=False)
        if not cands:
            return {"error": "目标角色没找到"}
        s = cands[0]
        n = s.get("equipment") or len(body.get("names", []))
        for nm in body.get("names", []):
            self._learn_card(f, s["point"], n, A.norm(nm))
        return {"card": sorted({nm for nm, _ in self.lib.entries["card"]}), "now": self.card_items(f, s)}

    def icons(self, body):
        if body.get("action") == "teach":
            return self.teach(body)
        if body.get("action") == "reload":
            return {"loaded": self.lib.reload()}
        return {k: sorted({n for n, _ in v}) for k, v in self.lib.entries.items()}


def _pos(name):
    info = char_info(name)
    return "/" + POS.get(info["position"], "") if info else ""


def _xp(f):
    e = f.experience
    return f"{e[0]}/{e[1]}" if e else "-"


def _cap(f):
    c = f.capacity
    return f"{c[0]}/{c[1]}" if c else "-"
