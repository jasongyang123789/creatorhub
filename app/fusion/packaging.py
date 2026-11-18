"""视频包装镜头：片头 / 数据卡 / 章节卡 / 大字片头，纯本地渲染（PIL 逐帧 + ffmpeg 编码）。

不依赖 hyperframes 的完整 monorepo，也不联网；输出 30fps MP4，可直接进剪辑轨。
四种版式：
- kinetic：大字逐条砸入（片头 / 观点强调）；
- data-card：标签 + 大数字 + 注释（数据卡）；
- chapter：序号 + 章节名（章节卡）；
- hero：大标题砸入 + 底部信息条扫入（hero 版式借鉴 OpenMontage HeroTitle 组件思路
  （AGPL，只借鉴版式原则，代码自写））。
"""
from __future__ import annotations

import math
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .paths import FUSION_DIR, ensure_dir, ffmpeg_exe, font_path, run, slugify

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
STYLES = ("kinetic", "data-card", "chapter", "hero")
BG = (11, 13, 16)
FG = (240, 244, 250)
DIM = (150, 160, 175)
ACCENT = (79, 142, 247)


def _font(size: int, bold: bool = True):
    path = font_path()
    if path:
        try:
            return ImageFont.truetype(path, size, index=0)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
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
    return lines or [""]


def _ease_out(x: float) -> float:
    return 1 - (1 - max(0.0, min(1.0, x))) ** 3


def _frame(style: str, lines: list[str], progress: float, width: int, height: int,
           index: int = 1, duration: float = 3.0) -> Image.Image:
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    # 细网格
    step = max(80, width // 12)
    for x in range(0, width, step):
        draw.line([(x, 0), (x, height)], fill=(22, 26, 32), width=1)
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=(22, 26, 32), width=1)

    margin = int(width * 0.08)
    if style == "data-card":
        label = lines[0] if lines else ""
        value = lines[1] if len(lines) > 1 else ""
        sub = lines[2] if len(lines) > 2 else ""
        draw.text((margin, int(height * 0.32)), label, font=_font(int(width * 0.045), False), fill=DIM)
        vfont = _font(int(width * 0.22))
        scale = 1 + (1 - _ease_out(progress * 2.5)) * 0.25
        vfont_scaled = _font(max(12, int(width * 0.22 * scale)))
        draw.text((margin, int(height * 0.38)), value, font=vfont_scaled, fill=FG)
        bar_w = int((width - margin * 2) * _ease_out(progress))
        draw.rectangle([margin, int(height * 0.68), margin + bar_w, int(height * 0.68) + 6], fill=ACCENT)
        draw.text((margin, int(height * 0.71)), sub, font=_font(int(width * 0.034), False), fill=DIM)
    elif style == "chapter":
        title = lines[0] if lines else ""
        number = f"{index:02d}"
        draw.text((margin, int(height * 0.26)), number, font=_font(int(width * 0.34)), fill=(28, 34, 44))
        draw.text((margin, int(height * 0.26)), number, font=_font(int(width * 0.34)), fill=ACCENT)
        offset = (1 - _ease_out(progress * 2)) * 40
        draw.text((margin + 4, int(height * 0.58) + offset), title,
                  font=_font(int(width * 0.075)), fill=FG)
        draw.line([(margin, int(height * 0.72)), (margin + int(width * 0.22 * _ease_out(progress)), int(height * 0.72))],
                  fill=ACCENT, width=5)
    elif style == "hero":
        # hero 版式借鉴 OpenMontage HeroTitle 组件思路（AGPL，只借鉴版式原则，代码自写）：
        # 开场 0.4s 空景 → 大标题从下方砸入（scale 1.6→1.0 + 透明度）→
        # 底部信息条从左扫入 → 结尾轻微上浮。
        t = progress * max(0.5, duration)
        slam_start, slam_end = 0.4, 0.95
        bar_end = slam_end + 0.5
        title = lines[0] if lines else ""
        sub = " · ".join(line for line in lines[1:] if line) or "CREATORHUB"

        # 大标题：scale 1.6→1.0 + 透明度，从下方砸入
        if t >= slam_start:
            u = max(0.0, min(1.0, (t - slam_start) / max(0.05, slam_end - slam_start)))
            ease = _ease_out(u)
            scale = 1.6 - 0.6 * ease
            alpha = int(255 * max(0.0, min(1.0, u * 1.6)))
            base_size = max(18, int(width * (0.11 if width <= height else 0.075)))
            font = _font(max(16, int(base_size * scale)))
            wrap = _wrap(draw, title, font, width - margin * 2)
            float_up = _ease_out(max(0.0, min(1.0,
                                 (t - max(0.0, duration - 0.5)) / 0.5))) * 14 if t > duration - 0.5 else 0
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            y = int(height * 0.40) + int((1 - ease) * height * 0.08) - int(float_up)
            line_h = int(base_size * 1.18)
            start_y = y - line_h * (len(wrap) - 1) // 2
            for row in wrap:
                row_w = odraw.textlength(row, font=font)
                odraw.text(((width - row_w) / 2, start_y), row,
                           font=font, fill=(*FG, alpha))
                start_y += line_h
            image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(image)

        # 底部信息条：强调色横条从左扫入 + 文字渐显
        if t >= slam_end:
            v = max(0.0, min(1.0, (t - slam_end) / max(0.05, bar_end - slam_end)))
            ease = _ease_out(v)
            bar_w = int((width - margin * 2) * ease)
            bar_y = int(height * 0.74)
            draw.rectangle([margin, bar_y, margin + bar_w, bar_y + 5], fill=ACCENT)
            bfont = _font(max(14, int(width * 0.032)), False)
            bar_color = tuple(int(BG[i] + (DIM[i] - BG[i]) * ease) for i in range(3))
            draw.text((margin, bar_y + 18), sub, font=bfont, fill=bar_color)
    else:  # kinetic
        total = len(lines) or 1
        font = _font(int(width * 0.095))
        y = int(height * 0.36)
        for i, text in enumerate(lines):
            local = max(0.0, min(1.0, progress * total - i))
            if local <= 0:
                continue
            scale = 1 + (1 - _ease_out(local * 3)) * 0.45
            size = max(14, int(width * 0.095 * scale))
            f = _font(size)
            color = FG if i < total - 1 else ACCENT
            draw.text((margin, y), text, font=f, fill=color)
            y += int(size * 1.35)
    # 右上角时间码
    draw.text((width - margin - 110, int(height * 0.05)), datetime.utcnow().strftime("%H:%M:%S"),
              font=_font(int(width * 0.022), False), fill=(70, 78, 90))
    return image


def render_packaging(lines: list[str], style: str = "kinetic", duration: float = 3.0,
                     aspect: str = "9:16", fps: int = 30, index: int = 1) -> dict:
    lines = [line.strip() for line in (lines or []) if line and line.strip()]
    if not lines:
        raise ValueError("lines 不能为空")
    if style not in STYLES:
        raise ValueError(f"style 只支持 {', '.join(STYLES)}")
    if aspect not in ASPECTS:
        raise ValueError(f"aspect 只支持 {', '.join(ASPECTS)}")
    duration = max(1.0, min(float(duration), 15.0))
    width, height = ASPECTS[aspect]
    total = max(1, int(duration * fps))

    folder = ensure_dir(FUSION_DIR / "packaging")
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{style}-{slugify(lines[0])}"
    out_path = folder / f"{base}.mp4"
    work = Path(tempfile.mkdtemp(prefix="fusion-pack-"))
    try:
        for frame_index in range(total):
            progress = frame_index / max(1, total - 1)
            image = _frame(style, lines, progress, width, height, index, duration=duration)
            image.save(work / f"f_{frame_index:04d}.png")
        result = run([ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(fps),
                      "-i", str(work / "f_%04d.png"), "-c:v", "libx264",
                      "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)],
                     timeout=300)
        if result.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"编码失败：{result.stderr[-300:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out_path), "style": style, "aspect": aspect, "fps": fps,
            "duration": duration, "frames": total, "bytes": out_path.stat().st_size,
            "note": "无声包装镜头；与口播/配乐在剪辑轨里对齐。"}
