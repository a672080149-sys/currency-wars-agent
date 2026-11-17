"""DS 自动打完整局：读局面 → DS 提议 → 直接执行，直到对局结束；结束时停止录像。

用法（后台运行即可）：python -X utf8 duo_auto.py [--max-steps 300]
日志：bridge/logs/duo_auto.log
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duo  # noqa: E402
import workbench  # noqa: E402
from client import BridgeError, call  # noqa: E402

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "duo_auto.log")
NOTE = ("现在由你（DS）独自打完这一局，小克去休息了，你提的操作会直接执行。"
        "每次只处理当前页面：备战页按 捡球→商店→上阵→装备→卖闲卡→出战 一步到位（最后一个操作放 fight）；"
        "选择/投资/补给/遭遇/茶歇/光环/开拓商店等页面按 look 给的坐标点选项再点确认。"
        "经济按当前阵容：银狼先用超过50的金币升7级，停级搜银狼三星，刷新底线55；"
        "三星后用超过50的金币升到9级封顶。真珠顺手追三星，不专门刷新。阿哈每张都买。各阵容最终首领前清矿并解除存钱底线。"
        "人口必须上满；备战席上和场上同名的卡是升星素材（凑满3张自动升星），不要把它们移上场替换同名角色。阿哈装备（带阿哈标记的散件）要买并拖到阿哈头像 (420,310) 再点合成确认 (496,380)。")


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def page_head():
    try:
        return (call("look", {}).get("text") or "").split("\n", 1)[0]
    except BridgeError as e:
        return f"ERR {e}"


def finished(head):
    return head.startswith("[对局结算]") or head.startswith("[入画奇航首页]") or head.startswith("[模式选择]")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-steps", type=int, default=300)
    args = p.parse_args()
    log("=== DS 自动模式开始 ===")
    workbench.send({"agent": "ds", "kind": "say", "thought": "小克去休息啦，剩下的交给我！我会一步步打完这局～"}, wait=True)
    same, last = 0, None
    for step in range(args.max_steps):
        head = page_head()
        log(f"[{step}] {head}")
        if finished(head):
            # 对局结算页可能要点几次「下一步」
            if head.startswith("[对局结算]"):
                for _ in range(4):
                    call("tap", {"x": 965, "y": 904})
                    time.sleep(2.5)
            break
        same = same + 1 if head == last else 0
        last = head
        if same >= 4:  # 同一页面卡住：试着关弹窗/点空白
            log("卡住了，按 esc 并点空白")
            call("key", {"key": "esc"})
            time.sleep(1)
            call("tap", {"x": 965, "y": 710})
            time.sleep(1.5)
            same = 0
            continue
        try:
            st_before = duo.load()
            rc = duo.cmd_propose(argparse.Namespace(note=NOTE))
            if rc != 0:
                log("DS 提议失败，点空白继续")
                call("tap", {"x": 965, "y": 710})
                time.sleep(2)
                continue
            st = duo.load()
            st["status"] = "agreed"
            duo.save(st)
            log("执行：" + json.dumps(st.get("plan"), ensure_ascii=False)[:500])
            duo.cmd_exec(argparse.Namespace())
        except Exception:
            log("异常：" + traceback.format_exc()[-800:])
            time.sleep(3)
        time.sleep(1.5)
    workbench.send({"agent": "ds", "kind": "say", "thought": "这局打完啦！谢谢大家观看～"}, wait=True)
    try:
        r = call("record", {"action": "stop"})
        log("停止录像：" + json.dumps(r, ensure_ascii=False)[:300])
    except Exception as e:
        log(f"停止录像失败：{e}")
    log("=== 结束 ===")


if __name__ == "__main__":
    sys.exit(main())
