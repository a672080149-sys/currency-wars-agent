"""DS（DeepSeek）做一次纯文本问答。支持两种后端：

- workbuddy（默认）：WorkBuddy 自带的 CodeBuddy CLI（默认 DeepSeek V4.1 Flash）。
  不给任何工具、不加载 MCP，用 --system-prompt-file 设人设。
- dsh：DeepSeek Harness 的 `dsh headless`。需要先在 `dsh web` 的 Models 页填好 DeepSeek API Key
  （或设环境变量 DEEPSEEK_API_KEY）。dsh headless 没有关闭工具/设系统提示词的参数，
  所以把规则拼进任务正文、在空临时目录里运行，并在提示词里要求不要使用工具。

选择后端：环境变量 DS_BACKEND=workbuddy|dsh，或 bridge/player.json 里写 "ds_backend": "dsh"。
dsh 路径：环境变量 DSH_CMD，或 PATH 里的 dsh，或默认安装路径。
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("DS_MODEL", "custom-local:workbuddy/deepseek-v4.1-flash")
CLI = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "WorkBuddy", "resources", "app.asar.unpacked",
                   "cli", "dist", "codebuddy-lite-wb.mjs")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def backend() -> str:
    b = os.environ.get("DS_BACKEND", "").strip().lower()
    if not b:
        try:
            with open(os.path.join(HERE, "player.json"), encoding="utf-8") as f:
                b = str(json.load(f).get("ds_backend", "")).strip().lower()
        except (OSError, ValueError, AttributeError):
            b = ""
    return "dsh" if b in ("dsh", "deepseek-harness", "harness") else "workbuddy"


def _node():
    vers = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions", "*", "node.exe")))
    if not vers:
        raise RuntimeError("找不到 WorkBuddy 自带的 node")
    return vers[-1]


def _dsh_cmd():
    cands = [os.environ.get("DSH_CMD"), shutil.which("dsh"), shutil.which("dsh.cmd"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "DeepSeek Harness", "resources",
                          "runtime", "cli", "bin", "dsh.cmd")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise RuntimeError("找不到 dsh：请安装 DeepSeek Harness，或设环境变量 DSH_CMD 指向 dsh / dsh.cmd")


def _ask_workbuddy(prompt, timeout, system_file):
    if not os.path.exists(CLI):
        raise RuntimeError(f"找不到 WorkBuddy CLI: {CLI}（WorkBuddy 更新后入口文件可能改名）")
    args = [_node(), CLI, "-p", "--model", MODEL, "--output-format", "json", "--max-turns", "1",
            "--no-session-persistence", "--permission-mode", "dontAsk", "--tools=Read",
            "--strict-mcp-config", '--mcp-config={"mcpServers":{}}']
    if system_file:
        args += ["--system-prompt-file", os.path.abspath(system_file)]
    args.append(prompt)
    cwd = tempfile.mkdtemp(prefix="ds_")  # 空目录：读不到项目文件和 .mcp.json
    out = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    raw = out.stdout.decode("utf-8", "replace")
    s, e = raw.find("["), raw.rfind("]")
    try:
        res = [x for x in json.loads(raw[s:e + 1]) if x.get("type") == "result"][-1]
        return (res.get("result") or ""), bool(res.get("is_error"))
    except Exception:
        return raw, True


def _ask_dsh(prompt, timeout, system_file):
    system = ""
    if system_file:
        with open(system_file, encoding="utf-8") as f:
            system = f.read()
    task = ((system + "\n\n---\n\n") if system else "") + prompt + \
        "\n\n（不要使用任何工具，不要读写文件或运行命令，直接回答。）"
    cwd = tempfile.mkdtemp(prefix="ds_")
    out = subprocess.run([_dsh_cmd(), "headless", "-"], input=task.encode("utf-8"), cwd=cwd,
                         capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    text = out.stdout.decode("utf-8", "replace")
    if out.returncode != 0:
        return (text.strip() or out.stderr.decode("utf-8", "replace")), True
    return text, False


def ask(prompt: str, timeout: float = 180, system_file: str | None = None) -> dict:
    """返回 {"text": 回答, "seconds": 用时, "error": bool, "backend": 后端}。"""
    b = backend()
    t0 = time.time()
    text, err = (_ask_dsh if b == "dsh" else _ask_workbuddy)(prompt, timeout, system_file)
    return {"text": (text or "").strip(), "seconds": round(time.time() - t0, 1), "error": err, "backend": b}


if __name__ == "__main__":
    import sys
    print(json.dumps(ask(sys.argv[1] if len(sys.argv) > 1 else "用一句话介绍你自己"), ensure_ascii=False))
