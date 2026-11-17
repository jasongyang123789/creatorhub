"""长文 → 讲解片（轻量版）：分镜 + 大字卡 + 字幕 + 可选配音。

对齐 anything2explainer 的产品逻辑（主题→分镜→字幕→成片），但不引入 Remotion：
- 分镜：按句意把正文切成 4–8 个镜头，每个镜头一屏；
- 画面：黑底 + 点阵 + 大标题 + 关键数字，逐帧淡入；
- 配音：检测到 edge-tts 时自动合成（需联网），否则输出无声版并烧字幕；
- 编码：ffmpeg 30fps H.264。
"""
from __future__ import annotations

import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .paths import FUSION_DIR, ensure_dir, ffmpeg_exe, font_path, run, slugify

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
BG = (8, 10, 14)
FG = (238, 243, 250)
DIM = (140, 152, 168)
ACCENT = (139, 108, 247)
MAX_SCENES = 8


def _font(size: int):
    path = font_path()
    if path:
        try:
            return ImageFont.truetype(path, size, index=0)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _wrap(draw, text, font, max_width):
    lines, current = [], ""
    for char in text:
        probe = current + char
        if draw.textlength(probe, font=font) <= max_width or not current:
            current = probe
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _split_scenes(text: str) -> list[dict]:
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；;])\s*|\n+", text) if s.strip()]
    if not sentences:
        raise ValueError("正文为空")
    # 按字数均匀分组，最多 8 组
    target = max(1, min(MAX_SCENES, math_ceil(len(sentences) / 2)))
    size = max(1, math_ceil(len(sentences) / target))
    scenes = []
    for start in range(0, len(sentences), size):
        chunk = sentences[start:start + size]
        body = "".join(chunk)
        number = re.search(r"\d+(?:\.\d+)?\s*(?:万|亿|%|元|天|年|小时|分钟)?", body)
        scenes.append({
            "headline": chunk[0][:28],
            "body": body[:160],
            "number": number.group(0) if number else "",
        })
        if len(scenes) >= MAX_SCENES:
            break
    return scenes


def math_ceil(value: float) -> int:
    return int(value) + (1 if value > int(value) else 0)


def _scene_image(scene: dict, index: int, total: int, progress: float,
                 width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    # 点阵
    for y in range(40, height, 44):
        for x in range(40, width, 44):
            draw.ellipse([x, y, x + 3, y + 3], fill=(26, 30, 38))
    margin = int(width * 0.09)
    ease = 1 - (1 - min(1.0, progress * 4)) ** 3
    # 顶部章节
    draw.text((margin, int(height * 0.09)), f"{index + 1:02d} / {total:02d}",
              font=_font(int(width * 0.03)), fill=DIM)
    # 大数字
    y = int(height * 0.24)
    if scene["number"]:
        draw.text((margin, y), scene["number"], font=_font(int(width * 0.17)), fill=ACCENT)
        y += int(width * 0.20)
    # 标题
    title_font = _font(int(width * 0.068))
    for line in _wrap(draw, scene["headline"], title_font, width - margin * 2)[:3]:
        draw.text((margin, y + int((1 - ease) * 30)), line, font=title_font, fill=FG)
        y += int(width * 0.085)
    # 正文
    body_font = _font(int(width * 0.034))
    y += int(height * 0.03)
    for line in _wrap(draw, scene["body"], body_font, width - margin * 2)[:6]:
        draw.text((margin, y), line, font=body_font, fill=DIM)
        y += int(width * 0.05)
    # 底部进度条
    bar = int((width - margin * 2) * (index + progress) / total)
    draw.rectangle([margin, height - int(height * 0.08), margin + bar,
                    height - int(height * 0.08) + 5], fill=ACCENT)
    return image


def _audio_seconds(path: Path) -> float:
    """用 ffmpeg 读取音频时长（不引入额外依赖）。"""
    try:
        result = run([ffmpeg_exe(), "-i", str(path), "-f", "null", "-"], timeout=60)
        import re as _re
        match = _re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
        if match:
            return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _tts(text: str, out_path: Path, voice: str) -> bool:
    """有 edge-tts 就合成；没有则返回 False（走无声版）。"""
    try:
        import asyncio
        import edge_tts
    except ImportError:
        return False
    try:
        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(out_path))
        asyncio.run(_run())
        return out_path.is_file() and out_path.stat().st_size > 1000
    except Exception:  # noqa: BLE001
        return False


def render_explainer(text: str, title: str = "", aspect: str = "9:16",
                     scene_seconds: float = 4.0, voice: str = "zh-CN-YunxiNeural",
                     fps: int = 30) -> dict:
    if aspect not in ASPECTS:
        raise ValueError(f"aspect 只支持 {', '.join(ASPECTS)}")
    width, height = ASPECTS[aspect]
    scenes = _split_scenes(text)
    folder = ensure_dir(FUSION_DIR / "explainer")
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{slugify(title or scenes[0]['headline'])}"
    out_path = folder / f"{base}.mp4"
    audio_path = folder / f"{base}.mp3"
    work = Path(tempfile.mkdtemp(prefix="fusion-exp-"))
    has_audio = _tts("".join(scene["body"] for scene in scenes), audio_path, voice)

    # 有配音时以配音时长为准分配镜头；没有则按 scene_seconds。
    audio_seconds = _audio_seconds(audio_path) if has_audio else 0.0
    seconds = audio_seconds + 0.4 if audio_seconds > 1 else len(scenes) * max(2.0, min(scene_seconds, 8.0))
    total_frames = max(1, int(seconds * fps))
    try:
        for frame_index in range(total_frames):
            scene_index = min(len(scenes) - 1, int(frame_index / total_frames * len(scenes)))
            local = (frame_index / total_frames * len(scenes)) - scene_index
            image = _scene_image(scenes[scene_index], scene_index, len(scenes), local, width, height)
            image.save(work / f"f_{frame_index:04d}.png")
        cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(fps),
               "-i", str(work / "f_%04d.png")]
        if has_audio:
            cmd += ["-i", str(audio_path), "-c:a", "aac", "-b:a", "128k", "-shortest"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
        result = run(cmd, timeout=600)
        if result.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"讲解片编码失败：{result.stderr[-300:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out_path), "title": title or scenes[0]["headline"],
            "scenes": scenes, "aspect": aspect, "fps": fps,
            "duration": round(_audio_seconds(out_path) or total_frames / fps, 2), "audio": has_audio,
            "bytes": out_path.stat().st_size,
            "note": "无声版不烧字幕（正文已画在画面里）；有 edge-tts 时自动配音。"
                    "长文建议先拆成 3–5 段，每段单独出片。"}
