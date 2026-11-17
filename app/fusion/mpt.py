"""批量混剪：用 MoneyPrinterTurbo 的本地素材管线出竖屏成片。

只走「给定脚本 + 本地素材」路径，不依赖 LLM 与在线素材库：
    python cli.py --video-subject ... --video-script ... --video-source local \
        --video-materials a.jpg,b.mp4 --stop-at video
产物在 MPT 的 storage/tasks/<id>/final-1.mp4，这里再复制到 storage/fusion/shorts/。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .paths import FUSION_DIR, MPT_DIR, ensure_dir, run, slugify


def render_short(subject: str, script: str, materials: list[str],
                 language: str = "zh-CN") -> dict:
    subject = (subject or "").strip()
    script = (script or "").strip()
    if not subject or not script:
        raise ValueError("subject 与 script 都不能为空")
    if not materials:
        raise ValueError("至少提供一条本地素材（图片或视频路径）")
    resolved = []
    for item in materials:
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = (MPT_DIR / path)
        if not path.is_file():
            raise ValueError(f"素材不存在：{item}")
        resolved.append(str(path))
    cli = MPT_DIR / "cli.py"
    if not cli.is_file():
        raise RuntimeError(f"MoneyPrinterTurbo 不在 {MPT_DIR}（可设 CREATORHUB_MPT_DIR）")
    deps = MPT_DIR / ".deps"
    if not deps.is_dir():
        raise RuntimeError("MoneyPrinterTurbo 依赖未安装（缺 .deps）")

    env_cmd = [
        str(MPT_DIR / ".venv" / "Scripts" / "python.exe")
        if (MPT_DIR / ".venv" / "Scripts" / "python.exe").is_file() else "python",
        "-X", "utf8", str(cli),
        "--video-subject", subject,
        "--video-script", script,
        "--video-language", language,
        "--paragraph-number", "1",
        "--video-source", "local",
        "--video-materials", ",".join(resolved),
        "--stop-at", "video",
    ]
    import os
    env = {**os.environ, "PYTHONPATH": str(deps), "PYTHONUTF8": "1",
           "PYTHONIOENCODING": "utf-8"}
    import subprocess
    result = subprocess.run(env_cmd, cwd=str(MPT_DIR), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=900, env=env,
                            stdin=subprocess.DEVNULL)
    payload = None
    for line in reversed((result.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and '"task_id"' in line:
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
    if result.returncode != 0 or not payload:
        tail = (result.stderr or result.stdout or "")[-400:]
        raise RuntimeError(f"MoneyPrinterTurbo 渲染失败：{tail}")

    videos = (payload.get("result") or {}).get("videos") or []
    if not videos:
        raise RuntimeError("MoneyPrinterTurbo 未返回成片路径")
    source = Path(videos[0])
    folder = ensure_dir(FUSION_DIR / "shorts")
    target = folder / f"{datetime.utcnow():%Y%m%d-%H%M%S}-{slugify(subject)}.mp4"
    shutil.copy2(source, target)
    return {"path": str(target), "source": str(source), "task_id": payload.get("task_id"),
            "subject": subject, "materials": len(resolved),
            "audio": bool((payload.get("result") or {}).get("audio_file")),
            "subtitles": bool((payload.get("result") or {}).get("subtitle_path")),
            "note": "已含 TTS 配音与字幕；发布前请过一遍去 AI 味质检。"}
