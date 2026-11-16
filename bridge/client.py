"""桥接服务的 HTTP 客户端（不需要管理员权限，只依赖标准库）。"""
import json
import os
import urllib.error
import urllib.request

BRIDGE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BRIDGE, ".token")
PORT = int(os.environ.get("CW_BRIDGE_PORT", "47821"))


class BridgeError(RuntimeError):
    pass


def call(endpoint: str, body: dict | None = None, timeout: float = 600) -> dict:
    try:
        token = open(TOKEN_PATH, encoding="utf-8").read().strip()
    except FileNotFoundError:
        raise BridgeError("找不到 bridge/.token：桥接服务还没启动，请运行 bridge\\start_server.bat") from None
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/{endpoint}",
        data=json.dumps(body or {}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-CW-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read().decode("utf-8"))
        except Exception:
            raise BridgeError(f"HTTP {error.code}") from None
    except urllib.error.URLError as error:
        raise BridgeError(f"连不上桥接服务（{error.reason}）：请运行 bridge\\start_server.bat") from None
