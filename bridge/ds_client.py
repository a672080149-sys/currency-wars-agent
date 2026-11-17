"""调用 WorkBuddy 自带的 CodeBuddy CLI（默认 DeepSeek V4.1 Flash）做一次纯文本问答。

DS 不拿任何工具、不加载任何 MCP（防止绕过讨论流程直接操作游戏），只读我们给的文字、回一段文字。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import tempfile
import time

MODEL = os.environ.get("DS_MODEL", "custom-local:workbuddy/deepseek-v4.1-flash")
CLI = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "WorkBuddy", "resources", "app.asar.unpacked",
                   "cli", "dist", "codebuddy-lite-wb.mjs")


def _node():
    vers = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions", "*", "node.exe")))
    if not vers:
        raise RuntimeError("找不到 WorkBuddy 自带的 node")
    return vers[-1]


def ask(prompt: str, timeout: float = 180, system_file: str | None = None) -> dict:
    """返回 {"text": 回答, "seconds": 用时, "error": bool}。"""
    if not os.path.exists(CLI):
        raise RuntimeError(f"找不到 WorkBuddy CLI: {CLI}（WorkBuddy 更新后入口文件可能改名）")
    args = [_node(), CLI, "-p", "--model", MODEL, "--output-format", "json", "--max-turns", "1",
            "--no-session-persistence", "--permission-mode", "dontAsk", "--tools=Read",
            "--strict-mcp-config", '--mcp-config={"mcpServers":{}}']
    if system_file:
        args += ["--system-prompt-file", os.path.abspath(system_file)]
    args.append(prompt)
    cwd = tempfile.mkdtemp(prefix="ds_")  # 空目录：读不到项目文件和 .mcp.json
    t0 = time.time()
    out = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    raw = out.stdout.decode("utf-8", "replace")
    s, e = raw.find("["), raw.rfind("]")
    text, err = raw, True
    try:
        res = [x for x in json.loads(raw[s:e + 1]) if x.get("type") == "result"][-1]
        text, err = res.get("result") or "", bool(res.get("is_error"))
    except Exception:
        pass
    return {"text": text.strip(), "seconds": round(time.time() - t0, 1), "error": err}


if __name__ == "__main__":
    import sys
    print(json.dumps(ask(sys.argv[1] if len(sys.argv) > 1 else "用一句话介绍你自己"), ensure_ascii=False))
