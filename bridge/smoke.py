"""冒烟测试：截取游戏窗口，跑 M7A 的 OCR 和页面识别。"""
import os
import sys
import time

VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor", "March7thAssistant")
os.chdir(VENDOR)
sys.path.insert(0, VENDOR)

from module.automation import auto  # noqa: E402
from tasks.weekly import currency_wars_ui as ui  # noqa: E402

t = time.time()
ok = auto.take_screenshot()
print("screenshot ok:", bool(ok), "size:", auto.screenshot.size if ok else None, f"{time.time() - t:.2f}s")
t = time.time()
auto.perform_ocr()
print("ocr items:", len(auto.ocr_result), f"{time.time() - t:.2f}s")
frame = ui.Frame.from_ocr(auto.screenshot, auto.ocr_result)
print("page:", frame.page, "stage:", frame.stage, "gold:", frame.gold, "season:", frame.season)
print("texts:", [lb.text for lb in frame.labels][:60])
auto.screenshot.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke.png"))
