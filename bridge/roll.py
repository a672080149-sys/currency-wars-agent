"""按规则搜牌：只花超过 floor 的金币，买想要的卡，记录看到的值得注意的卡。

python bridge/roll.py [floor=50] [max_rolls=20]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import call  # noqa: E402

# 名字: 本次最多买几张（流萤要攒 3 星，尽量都买）
WANT = {"雪衣": 2, "波提欧": 2}
MUST = {"雪衣", "波提欧"}  # 不受 floor 限制，见到就买
SOFT = {"雪衣", "波提欧", "风堇"}  # 关键卡，允许比 floor 少 3 金
NOTE = {"寒鸦", "白露", "千冶·刃", "千冶•刃", "星期日", "缇宝", "知更鸟", "灵砂", "罗刹", "符玄", "砂金", "花火"}


def shop():
    o = call("observe", {"texts": False})
    if o["page"] == "BOARD":
        o = call("tap", {"x": 1624, "y": 982, "settle": 1.3})
    return o


def main():
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    floor = int(args.get("floor", 50))
    max_rolls = int(args.get("max_rolls", 20))
    stop_name, _, stop_n = args.get("stop_after", ":0").partition(":")  # 例 stop_after=流萤:2
    o = shop()
    if o["page"] != "SHOP":
        print(json.dumps({"error": f"not shop: {o['page']}"}, ensure_ascii=False))
        return
    bought, seen, rolls = {}, set(), 0
    while True:
        o = call("observe", {"texts": False})
        offers = o.get("offers") or []
        gold = o.get("gold")
        line = [f"{x['name']}{x['cost']}" for x in offers]
        print(f"[{rolls}] gold={gold} {line}", flush=True)
        seen |= {x["name"] for x in offers if x["name"] in NOTE}
        for x in sorted([x for x in offers if x["name"] in WANT], key=lambda x: -x["column"]):
            must = x["name"] in MUST
            if bought.get(x["name"], 0) >= WANT[x["name"]] or gold is None:
                continue
            if gold - x["cost"] < (0 if must else floor - 3 if x["name"] in SOFT else floor):
                continue
            r = call("tap", {"x": x["point"][0], "y": x["point"][1], "settle": 1.2})
            bought[x["name"]] = bought.get(x["name"], 0) + 1
            gold = r.get("gold", gold)
            print(f"   bought {x['name']} -> gold {gold}", flush=True)
        if stop_name and bought.get(stop_name, 0) >= int(stop_n or 0) > 0:
            break
        price = call("observe", {"texts": False}).get("shop_refresh_price") or 2
        if gold is None or gold - price < floor or rolls >= max_rolls:
            break
        texts = call("observe")["texts"]
        btn = [t for t in texts if t[0] == "刷新"]
        x, y = (btn[0][1], btn[0][2]) if btn else (1618, 488)
        call("tap", {"x": x, "y": y, "settle": 1.3, "observe": False})
        rolls += 1
    o = call("observe", {"texts": False})
    print(json.dumps({"gold": o.get("gold"), "rolls": rolls, "bought": bought, "noteworthy": sorted(seen),
                      "final_offers": [x["name"] for x in o.get("offers") or []]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
