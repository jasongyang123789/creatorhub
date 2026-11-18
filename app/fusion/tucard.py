"""爆文转贴图（CREATOR OS 同款）：一篇爆文 → N+1 张贴图卡 PNG。

- 第 1 张封面卡：大标题 + 副题（第一段前 40 字），直接复用 cover.py 的封面版式；
- 第 2..N+1 张正文卡：页码 badge（第 i/N 张）+ 段落大字排版，每卡 ≤120 字，
  超出截断加 …。
复用 cover.py 的 SIZES / _title_size 与 HTML→无头 Chrome 截图管线
（import cover 用其内部函数，不改 cover.py）；
风格仅支持 hallmark / nothing / huashu / guizang 四种。
产物落 storage/fusion/tucards/<stamp>-<slug>/。
"""
from __future__ import annotations

import html
import math
import subprocess
from datetime import datetime
from pathlib import Path

from . import cover
from .paths import FUSION_DIR, chrome_exe, ensure_dir, slugify

STYLES = cover.STYLES  # hallmark / nothing / huashu / guizang
MAX_CARD_CHARS = 120


def _fit_size(style: str, width: int, height: int, text: str) -> int:
    """正文卡字号：以 cover._title_size 起步，按字数与可用高度逐级收缩，防止溢出。"""
    size = cover._title_size(style, width, min(len(text), 10))
    wrap_padding = int(width * 0.075)
    avail = max(120, width - wrap_padding * 2)
    max_h = height * 0.55
    for _ in range(30):
        if size <= 28:
            break
        chars_per_line = max(4, int(avail / max(1, size)))
        lines = math.ceil(max(1, len(text)) / chars_per_line)
        if lines * size * 1.12 <= max_h:
            break
        size = int(size * 0.92)
    return max(28, size)


def _body(style: str, text: str, page: int, total: int, font_size: int) -> str:
    """正文卡 body：与 cover._body 同一套 class（kicker/title/foot），加页码 badge。"""
    text = html.escape(text)
    badge = f"CREATOR OS · 第 {page}/{total} 张"
    foot = f'<span>CREATOR OS</span><span class="num">{page}/{total}</span>'
    size_css = f"font-size:{font_size}px;line-height:1.18"
    if style == "nothing":
        return (f'<div><div class="kicker"><span class="dot"></span>{badge}</div>'
                f'<div class="title" style="{size_css}">{text}</div></div>'
                f'<div><div class="foot">{foot}</div></div>')
    if style == "huashu":
        return (f'<div class="block"></div>'
                f'<div style="position:relative;z-index:2"><div class="kicker">{badge}</div>'
                f'<div class="title" style="{size_css}">{text}</div></div>'
                f'<div style="position:relative;z-index:2"><div class="foot" '
                f'style="margin-top:24px">{foot}</div></div>')
    if style == "guizang":
        return (f'<div><div class="kicker">{badge}</div>'
                f'<div class="title" style="{size_css}">{text}</div></div>'
                f'<div><div class="foot" style="margin-top:22px">{foot}</div></div>')
    return (f'<div><div class="kicker">{badge}</div>'
            f'<div class="title" style="margin-top:24px;{size_css}">{text}</div></div>'
            f'<div class="rule"></div>'
            f'<div><div class="foot" style="margin-top:28px">{foot}</div></div>')


def _shoot(chrome: str, html_text: str, width: int, height: int, html_path: Path,
           png_path: Path) -> None:
    """与 cover.render_cover 同款的无头 Chrome 截图管线。"""
    html_path.write_text(html_text, encoding="utf-8")
    result = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--force-device-scale-factor=1", f"--window-size={width},{height}",
         f"--screenshot={png_path}", html_path.resolve().as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90, stdin=subprocess.DEVNULL)
    if not png_path.is_file() or png_path.stat().st_size < 2000:
        raise RuntimeError(f"贴图卡渲染失败（第 {png_path.stem}）："
                           f"{result.stderr[-300:] or '未生成图片'}")


def render_cards(title: str, paragraphs: list[str], style: str = "huashu",
                 size: str = "1080x1440") -> dict:
    """渲染 N+1 张贴图卡（封面 + 正文），返回目录与全部 PNG 路径。"""
    title = (title or "").strip()
    if not 1 <= len(title) <= 60:
        raise ValueError("title 需 1-60 字")
    paras = [p.strip() for p in (paragraphs or []) if p and p.strip()]
    if not 1 <= len(paras) <= 10:
        raise ValueError("paragraphs 需 1-10 段")
    for para in paras:
        if not 5 <= len(para) <= 200:
            raise ValueError(f"每段需 5-200 字，当前 {len(para)} 字：{para[:20]}…")
    if style not in STYLES:
        raise ValueError(f"style 只支持 {', '.join(STYLES)}")
    if size not in cover.SIZES:
        raise ValueError(f"size 只支持 {', '.join(cover.SIZES)}")
    chrome = chrome_exe()
    if not chrome:
        raise RuntimeError("找不到 Chrome/Edge，无法渲染贴图卡")

    width, height = cover.SIZES[size]
    total = len(paras)
    folder = ensure_dir(FUSION_DIR / "tucards" /
                        f"{datetime.utcnow():%Y%m%d-%H%M%S}-{slugify(title)}")
    cards: list[str] = []

    # 第 1 张：封面卡（复用 cover 的封面 body，副题=第一段前 40 字）
    cover_html = cover._shell(style, width, height,
                              cover._body(style, title, paras[0][:40], "CREATOR OS", 1),
                              title_len=len(title))
    cover_png = folder / "card-00.png"
    _shoot(chrome, cover_html, width, height, folder / "card-00.html", cover_png)
    cards.append(str(cover_png))

    # 第 2..N+1 张：正文卡（页码 badge + 段落大字，≤120 字截断加 …）
    for i, para in enumerate(paras, start=1):
        text = para if len(para) <= MAX_CARD_CHARS else para[:MAX_CARD_CHARS] + "…"
        font_size = _fit_size(style, width, height, text)
        body = _body(style, text, i, total, font_size)
        page_html = cover._shell(style, width, height, body, title_len=len(text))
        png = folder / f"card-{i:02d}.png"
        _shoot(chrome, page_html, width, height, folder / f"card-{i:02d}.html", png)
        cards.append(str(png))

    return {"dir": str(folder), "path": cards[0], "cards": cards, "count": len(cards),
            "style": style, "size": size,
            "bytes": sum(Path(p).stat().st_size for p in cards),
            "note": "CREATOR OS 同款爆文转贴图：封面卡 + 每段一张正文卡（≤120 字截断）。"}
