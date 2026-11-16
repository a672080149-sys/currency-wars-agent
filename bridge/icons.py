"""自学习装备图标库：把截图里的装备小图标认成名字，认不出的由调用方查一次名字后 learn() 存起来。

文件：bridge/icons/<kind>/<名字>__<序号>.png
kind：
  inv  物品栏格子（48x48 截块）
  top  物品栏顶部消耗品（冶金炉/扳手/投影仪…）
  card 棋盘卡片下方的小装备图标（19x16 截块）
"""
from __future__ import annotations

import os
import re

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
SIZE = {"inv": (32, 32), "top": (32, 32), "card": (32, 32)}
SAFE = re.compile(r'[\\/:*?"<>|]')


def _prep(img: np.ndarray, kind: str) -> np.ndarray:
    w, h = SIZE[kind]
    a = cv2.resize(np.ascontiguousarray(img[:, :, :3]), (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
    return a


def _score(a: np.ndarray, b: np.ndarray) -> float:
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED).item())


class IconLib:
    def __init__(self, root: str = ROOT):
        self.root = root
        self.entries: dict[str, list[tuple[str, np.ndarray]]] = {k: [] for k in SIZE}
        self.reload()

    def reload(self):
        for kind in SIZE:
            self.entries[kind] = []
            d = os.path.join(self.root, kind)
            os.makedirs(d, exist_ok=True)
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".png"):
                    continue
                name = fn[:-4].split("__")[0]
                img = np.asarray(Image.open(os.path.join(d, fn)).convert("RGB"))
                self.entries[kind].append((name, _prep(img, kind)))
        return {k: len(v) for k, v in self.entries.items()}

    def match(self, crop: np.ndarray, kind: str = "inv"):
        """返回 (名字或 None, 最高分, 与第二名的差距)。"""
        if crop is None or crop.size == 0 or not self.entries[kind]:
            return None, 0.0, 0.0
        s = _prep(crop, kind)
        best: dict[str, float] = {}
        for name, ref in self.entries[kind]:
            sc = _score(s, ref)
            if sc > best.get(name, -1):
                best[name] = sc
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        top_name, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        need = 0.86 if kind == "card" else 0.88
        ok = top >= need and top - second >= 0.04
        return (top_name if ok else None), top, top - second

    def learn(self, name: str, crop: np.ndarray, kind: str = "inv", limit: int = 4):
        name = SAFE.sub("_", (name or "").strip())
        if not name or crop is None or crop.size == 0:
            return False
        d = os.path.join(self.root, kind)
        os.makedirs(d, exist_ok=True)
        have = [fn for fn in os.listdir(d) if fn.split("__")[0] == name]
        if len(have) >= limit:
            return False
        # 已经能以高分认出来就不重复存
        hit, sc, _ = self.match(crop, kind)
        if hit == name and sc >= 0.95:
            return False
        path = os.path.join(d, f"{name}__{len(have)}.png")
        Image.fromarray(np.ascontiguousarray(crop[:, :, :3])).save(path)
        self.entries[kind].append((name, _prep(crop, kind)))
        return True
