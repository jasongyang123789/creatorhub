"""融合层的路径与外部工具探测；只读环境，不写系统目录。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FUSION_DIR = ROOT / "storage" / "fusion"

# 外部资产位置：可用环境变量覆盖，默认指向本机已验证的 G 盘目录。
RADAR_DIR = Path(os.environ.get("CREATORHUB_RADAR_DIR", r"G:/ai-verify/radar"))
SHOT_LIBRARY = Path(os.environ.get(
    "CREATORHUB_SHOT_LIBRARY",
    r"G:/VerifiedRepos/agent-skills/video-shotcraft/gallery/api/library.json"))
HYPERFRAMES_DIR = Path(os.environ.get(
    "CREATORHUB_HYPERFRAMES_DIR", r"G:/VerifiedRepos/agent-skills/hyperframes"))
MPT_DIR = Path(os.environ.get(
    "CREATORHUB_MPT_DIR", r"G:/VerifiedRepos/agent-skills/MoneyPrinterTurbo"))
CHROME_CANDIDATES = (
    os.environ.get("CREATORHUB_CHROME", ""),
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
)
FONT_CANDIDATES = (
    os.environ.get("CREATORHUB_FONT", ""),
    r"C:/Windows/Fonts/msyh.ttc",
    r"C:/Windows/Fonts/msyhbd.ttc",
    r"C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def chrome_exe() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def font_path() -> str | None:
    for candidate in FONT_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def ffmpeg_exe() -> str:
    """优先 PATH 中的 ffmpeg，其次 CreatorHub 自带的 imageio-ffmpeg。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("找不到 ffmpeg：请安装 ffmpeg 或保留 imageio-ffmpeg 依赖") from exc


def run(cmd: list[str], *, timeout: int = 300, cwd: str | None = None) -> subprocess.CompletedProcess:
    """统一的子进程入口；捕获输出便于诊断，不继承交互式 stdin。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=cwd, env=env,
                          stdin=subprocess.DEVNULL)


def slugify(text: str, limit: int = 40) -> str:
    keep = [ch if ch.isalnum() or ch in "-_" else "-" for ch in text.strip()]
    value = "".join(keep).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return (value or "untitled")[:limit]


def python_exe() -> str:
    return sys.executable
