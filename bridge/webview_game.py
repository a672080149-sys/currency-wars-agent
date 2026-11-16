"""小克工作台内嵌云·星穹铁道(WebView2)的控制器。

工作台用 --remote-debugging-port 打开 WebView2,这里直接走 CDP websocket:
截图 = Page.captureScreenshot(固定输出 1920x1080),输入 = M7A 自带的 CdpInput(Input.dispatch*)。
不用 selenium,不开新浏览器,不碰真实鼠标键盘。

用法(server.py 在导入 M7A 之前):  webview_game.install()
"""
from __future__ import annotations

import base64
import io
import itertools
import json
import os
import threading
import time
import urllib.request

import websocket
from PIL import Image

PORT = int(os.environ.get("CW_CDP_PORT", "9223"))
W, H = 1920, 1080


class WebViewGame:
    def __init__(self, port=PORT, logger=None):
        self.port = port
        self.logger = logger
        self.ws = None
        self.ids = itertools.count(1)
        self.lock = threading.RLock()
        self.driver = True  # 兼容 M7A 里 "if not self.driver" 的判断

    # ---------- CDP ----------
    def _target(self):
        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=3).read())
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise ConnectionError("工作台里没有打开云·星穹铁道页面(先点「启动星铁 · 货币战争」)")
        pages.sort(key=lambda t: ("mihoyo" not in t.get("url", ""), ))
        return pages[0]

    def _connect(self):
        target = self._target()
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
        # 让页面一直以为自己有焦点,键盘事件不丢
        self._send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        self._send("Page.setWebLifecycleState", {"state": "active"}, ignore_error=True)

    def _send(self, method, params=None, ignore_error=False):
        msg_id = next(self.ids)
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") != msg_id:
                continue  # 事件或旧回复
            if "error" in msg and not ignore_error:
                raise RuntimeError(f"CDP {method}: {msg['error'].get('message')}")
            return msg.get("result", {})

    def execute_cdp_cmd(self, cmd, cmd_args=None):
        with self.lock:
            for attempt in range(2):
                try:
                    if self.ws is None:
                        self._connect()
                    return self._send(cmd, cmd_args)
                except (websocket.WebSocketException, ConnectionError, OSError):
                    try:
                        self.ws and self.ws.close()
                    except Exception:
                        pass
                    self.ws = None
                    if attempt:
                        raise
                    time.sleep(0.3)

    def evaluate(self, expression):
        r = self.execute_cdp_cmd("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return (r.get("result") or {}).get("value")

    # ---------- 截图 ----------
    def screenshot(self, quality=90) -> Image.Image:
        """截整个视口再缩放到 1920x1080。
        (clip 的坐标不含浏览器缩放,用 clip 会截错范围;输入事件用的是 CSS 像素,本来就是 1920x1080。)"""
        self._dismiss_guides()
        r = self.execute_cdp_cmd("Page.captureScreenshot", {"format": "jpeg", "quality": quality})
        img = Image.open(io.BytesIO(base64.b64decode(r["data"])))
        img.load()
        if img.size != (W, H):
            img = img.resize((W, H), Image.BILINEAR)
        return img.convert("RGB")

    _guide_at = 0.0

    def _dismiss_guides(self):
        """云游戏页面自己的新手引导/提示浮层,每 5 秒顺手关一次。"""
        if time.time() - self._guide_at < 5:
            return
        self._guide_at = time.time()
        try:
            self.execute_cdp_cmd("Runtime.evaluate", {"expression": """(() => {
                let n = 0;
                document.querySelectorAll('div.guide-close-btn__x').forEach(e => { e.click(); n++; });
                document.querySelectorAll('button, div, span').forEach(e => {
                    if (/^我知道了/.test((e.innerText || '').trim()) && e.children.length === 0 && e.offsetParent) { e.click(); n++; }
                });
                return n;
            })()""", "returnByValue": True})
        except Exception:
            pass

    def take_screenshot(self, crop=(0, 0, 1, 1), prefer_frame=True):
        buf = io.BytesIO()
        self.screenshot().save(buf, "PNG")
        return buf.getvalue()

    def capture_frame(self, crop=(0, 0, 1, 1), *, prefer_frame=True):
        try:
            img = self.screenshot()
        except Exception as error:
            if self.logger:
                self.logger.debug(f"内嵌云游戏截图失败: {error}")
            return False
        left, top = int(W * crop[0]), int(H * crop[1])
        cw, ch = int(W * crop[2]), int(H * crop[3])
        if (left, top, cw, ch) != (0, 0, W, H):
            img = img.crop((left, top, left + cw, top + ch))
        return img, (left, top, cw, ch), 1

    # ---------- M7A 控制器接口 ----------
    def get_input_handler(self):
        from module.automation.cdp_input import CdpInput
        return CdpInput(cloud_game=self, logger=self.logger)

    def get_resolution(self):
        return W, H

    def get_window_handle(self):
        return 0

    def is_headless(self):
        return False

    def switch_to_game(self):
        return True

    def is_game_running(self):
        try:
            return bool(self.evaluate("!!document.querySelector('.game-player')"))
        except Exception:
            return False

    def is_in_game(self):
        return self.is_game_running()

    def check_cloud_game_interruptions(self):
        pass

    def start_game_process(self):
        return True

    def stop_game(self):
        return True

    def copy(self, text):
        self.evaluate("navigator.clipboard && navigator.clipboard.writeText(%s)" % json.dumps(text))

    def change_auto_battle(self, status):
        pass

    def page_state(self):
        """登录/排队/进游戏状态,给 status 用。"""
        try:
            return self.evaluate("""JSON.stringify({
                url: location.href,
                in_game: !!document.querySelector('.game-player'),
                start_button: !!document.querySelector('div.wel-card__content--start'),
                text: (document.body.innerText || '').slice(0, 200)
            })""")
        except Exception as error:
            return json.dumps({"error": str(error)})


    def enter(self, timeout=600):
        """网页首页点「进入游戏」并等到游戏画面出现(排队也在这里等)。返回是否进入。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = json.loads(self.page_state() or "{}")
            if state.get("in_game"):
                return True
            if state.get("start_button"):
                self.evaluate("document.querySelector('div.wel-card__content--start').click()")
            time.sleep(2)
        return False


def _auto_enter():
    """bridge 启动后 10 分钟内,网页停在首页就自动点「进入游戏」(断线重连后同理)。"""
    for _ in range(300):
        try:
            state = json.loads(GAME.page_state() or "{}")
            if state.get("start_button") and not state.get("in_game"):
                GAME.evaluate("document.querySelector('div.wel-card__content--start').click()")
        except Exception:
            pass
        time.sleep(2)


GAME: WebViewGame | None = None


def install():
    """在导入 M7A 之前调用:切到 cloud 后端,并把 cloud 控制器换成内嵌 WebView。"""
    global GAME
    os.environ["MARCH7TH_CLOUD_GAME_ENABLE"] = "true"
    os.environ["MARCH7TH_CLOUD_GAME_USE_PAID_TIME"] = "false"
    import module.game as game
    from module.logger import log
    GAME = WebViewGame(logger=log)
    game.cloud_game = GAME
    game._CONTROLLER_FACTORIES["cloud"] = lambda: GAME
    threading.Thread(target=_auto_enter, daemon=True).start()
    return GAME
