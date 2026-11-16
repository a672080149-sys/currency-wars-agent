"""棋盘角色卡头像库：用已确认名字的卡面头像识别棋盘，跳过点开详情。

- 样本按卡片尺寸分组：field（前台/后台卡）与 bench（备战席卡，尺寸更小）。
- 头像区域沿用 M7A 的 slot_mark(point)[1:47, 6:54]，避开卡边和星徽。
- 只收录图鉴（knowledge/characters.json）里存在的名字，防止 OCR 错字污染。
"""
from __future__ import annotations

import json
import os
import threading

import cv2
import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portraits")
META = os.path.join(ROOT, "meta.json")
CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "knowledge", "characters.json")
MAX_SAMPLES = 6
ACCEPT = 0.82      # 最佳相似度下限
MARGIN = 0.06      # 与第二名（不同名字）的差距下限
DUPLICATE = 0.97   # 与已有样本过于相似则不再存


def face_of(mark: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(mark[1:47, 6:54])


def _score(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])


class PortraitLibrary:
    def __init__(self):
        self.lock = threading.Lock()
        self.samples: dict[str, list[tuple[str, np.ndarray]]] = {"field": [], "bench": []}
        self.meta: dict[str, dict] = {}
        try:
            self.catalog = {c["name"] for c in json.load(open(CATALOG, encoding="utf-8"))}
        except Exception:
            self.catalog = set()
        self._load()

    def _load(self):
        os.makedirs(ROOT, exist_ok=True)
        try:
            self.meta = json.load(open(META, encoding="utf-8"))
        except Exception:
            self.meta = {}
        for group in self.samples:
            base = os.path.join(ROOT, group)
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                folder = os.path.join(base, name)
                for file in sorted(os.listdir(folder)):
                    data = np.fromfile(os.path.join(folder, file), dtype=np.uint8)
                    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                    if img is not None:
                        self.samples[group].append((name, cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))

    def stats(self):
        return {g: {"samples": len(s), "names": len({n for n, _ in s})} for g, s in self.samples.items()}

    def match(self, face: np.ndarray, group: str):
        """返回 (name, best, second) ；没有样本时 name 为 None。先比同组，再比另一组（自动缩放）。"""
        if face.std() < 12:
            return None, 0.0, 0.0
        best: dict[str, float] = {}
        with self.lock:
            pools = [self.samples[group], self.samples["bench" if group == "field" else "field"]]
            for i, pool in enumerate(pools):
                for name, sample in pool:
                    s = _score(face, sample) - (0.02 if i else 0.0)  # 跨尺寸比对略降权
                    if s > best.get(name, -1):
                        best[name] = s
        if not best:
            return None, 0.0, 0.0
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        name, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        return name, top, second

    def confident(self, face: np.ndarray, group: str):
        name, top, second = self.match(face, group)
        if name and top >= ACCEPT and top - second >= MARGIN:
            return name, top
        return None, top

    def canonical(self, name: str | None) -> str | None:
        """OCR 错字纠正：不在图鉴里时，取同长度、最多差 1 个字的图鉴名（如 藿蕾→藿藿）。"""
        if not name or not self.catalog or name in self.catalog:
            return name
        close = [c for c in self.catalog if len(c) == len(name) and sum(a != b for a, b in zip(c, name)) <= 1]
        return close[0] if len(close) == 1 else None

    def reload(self):
        with self.lock:
            self.samples = {"field": [], "bench": []}
        self._load()
        return self.stats()

    def add(self, face: np.ndarray, group: str, name: str, output: bool) -> bool:
        name = self.canonical(name)
        if not name or face.std() < 12:
            return False
        with self.lock:
            own = [s for n, s in self.samples[group] if n == name]
            if len(own) >= MAX_SAMPLES or any(_score(face, s) >= DUPLICATE for s in own):
                self._remember(name, output)
                return False
            folder = os.path.join(ROOT, group, name)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{len(own)}.png")
            ok, buf = cv2.imencode(".png", cv2.cvtColor(face, cv2.COLOR_RGB2BGR))
            if ok:
                buf.tofile(path)  # 支持中文路径
                self.samples[group].append((name, face.copy()))
            self._remember(name, output)
            return ok

    def _remember(self, name, output):
        entry = self.meta.setdefault(name, {})
        if output or "output" not in entry:
            entry["output"] = bool(output) or entry.get("output", False)
            with open(META, "w", encoding="utf-8") as f:
                json.dump(self.meta, f, ensure_ascii=False, indent=1)

    def output_of(self, name):
        return bool(self.meta.get(name, {}).get("output", False))
