"""玩家相关的本机配置：开拓者在游戏里显示的是玩家自己的昵称，需要用户自己填。

读取顺序：
  1. 环境变量 CW_TRAILBLAZER_NAME（可用逗号分隔多个）
  2. bridge/player.json：{"trailblazer_name": "你的昵称"}（也可以写成列表）
都没有时不做映射，开拓者只能按「开拓者·欢愉」这类正式名识别。
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "player.json")
TRAILBLAZER = "开拓者·欢愉"  # S2 货币战争里开拓者的正式名


def _clean(name):
    return (name or "").replace("•", "·").strip()


def trailblazer_names() -> set[str]:
    names = []
    env = os.environ.get("CW_TRAILBLAZER_NAME", "")
    names += [n for n in env.split(",") if n.strip()]
    try:
        with open(CONFIG, encoding="utf-8") as f:
            v = json.load(f).get("trailblazer_name")
        names += v if isinstance(v, list) else [v]
    except (OSError, ValueError, AttributeError):
        pass
    return {_clean(n).upper() for n in names if n and _clean(n)}


def to_trailblazer(name: str) -> str:
    """把玩家昵称换成开拓者正式名；不是昵称就原样返回。"""
    n = _clean(name)
    return TRAILBLAZER if n and n.upper() in trailblazer_names() else n
