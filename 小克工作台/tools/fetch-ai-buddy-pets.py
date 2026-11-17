"""Downloads AI-Buddy character motion packs (lXiaoSel1/AI-Buddy) and converts them into
8-column PNG sprite sheets under pets/<id>/ for 小克工作台.

Usage:
  python tools/fetch-ai-buddy-pets.py                  # download & convert all 11 characters
  python tools/fetch-ai-buddy-pets.py deepseek claude  # specific characters only
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

import imageio_ffmpeg
import numpy as np
from PIL import Image

RELEASE_TAG = "v2.0.17"
ASSET_VERSION = "2.0.5"

CHARACTERS = [
    ("deepseek", "DeepSeek"),
    ("claude", "Claude (AI-Buddy)"),
    ("chatgpt", "GPT"),
    ("gemini", "Gemini"),
    ("qwen", "Qwen"),
    ("kimi", "Kimi"),
    ("grok", "Grok"),
    ("seed", "Seed"),
    ("minimax", "MiniMax"),
    ("glm", "GLM"),
    ("hunyuan", "混元"),
]

MOOD_TO_ACTION = {
    "idle": "idle",
    "thinking": "reviewing",
    "writing": "working",
    "running": "working",
    "happy": "emotion-cheerful",
    "oops": "fail",
}

CELL = 240
COLS = 8
TW, TH = 177, 192
X_OFF = (CELL - TW) // 2
Y_OFF = 18


def convert_character(char_id: str, display_name: str, zip_path: str, pets_dir: str, ffmpeg: str) -> None:
    out_dir = os.path.join(pets_dir, char_id)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "name.txt"), "w", encoding="utf-8") as f:
        f.write(display_name)

    with zipfile.ZipFile(zip_path) as z:
        cache = {}
        for mood, action in MOOD_TO_ACTION.items():
            out_path = os.path.join(out_dir, f"{mood}.png")
            if action in cache:
                shutil.copyfile(cache[action], out_path)
                continue

            entry = f"assets/pet-characters/{char_id}/{action}/motion.webm"
            if entry not in z.namelist():
                print(f"  [warn] missing {entry}, skipping {mood}")
                continue

            data = z.read(entry)
            p = subprocess.run(
                [
                    ffmpeg, "-v", "error", "-c:v", "libvpx-vp9", "-i", "pipe:0",
                    "-vf", f"fps=20,scale={TW}:{TH}:flags=lanczos",
                    "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1",
                ],
                input=data,
                capture_output=True,
            )
            if p.returncode != 0:
                raise RuntimeError(f"{char_id}/{action}: {p.stderr.decode('utf-8', 'replace')}")

            arr = np.frombuffer(p.stdout, dtype=np.uint8)
            n = len(arr) // (TW * TH * 4)
            if n == 0:
                raise RuntimeError(f"{char_id}/{action}: decoded 0 frames")
            arr = arr.reshape((n, TH, TW, 4))
            rows = math.ceil(n / COLS)
            sheet = Image.new("RGBA", (COLS * CELL, rows * CELL), (0, 0, 0, 0))
            for i in range(n):
                fr = Image.fromarray(arr[i], "RGBA")
                r, c = divmod(i, COLS)
                sheet.paste(fr, (c * CELL + X_OFF, r * CELL + Y_OFF))
            sheet.save(out_path, optimize=False)
            cache[action] = out_path
            print(f"  {mood} ({action}): {n} frames -> {os.path.getsize(out_path) // 1024} KB")


def download_zip(char_id: str, dest: str) -> None:
    url = (
        f"https://github.com/lXiaoSel1/AI-Buddy/releases/download/"
        f"{RELEASE_TAG}/AI-Buddy-{ASSET_VERSION}-motion-{char_id}-30fps.zip"
    )
    print(f"Downloading {char_id} from {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pets_dir = os.path.join(root, "pets")
    os.makedirs(pets_dir, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    wanted = set(sys.argv[1:]) if len(sys.argv) > 1 else {c[0] for c in CHARACTERS}
    targets = [(cid, name) for cid, name in CHARACTERS if cid in wanted]
    if not targets:
        print("No matching characters.")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        for cid, name in targets:
            out_dir = os.path.join(pets_dir, cid)
            if all(os.path.exists(os.path.join(out_dir, f"{m}.png")) for m in MOOD_TO_ACTION):
                if not os.path.exists(os.path.join(out_dir, "name.txt")):
                    with open(os.path.join(out_dir, "name.txt"), "w", encoding="utf-8") as f:
                        f.write(name)
                print(f"[{cid}] already converted, skipping.")
                continue
            zip_path = os.path.join(tmp, f"{cid}.zip")
            download_zip(cid, zip_path)
            print(f"[{cid}] converting ({name})...")
            convert_character(cid, name, zip_path, pets_dir, ffmpeg)
            try:
                os.remove(zip_path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
