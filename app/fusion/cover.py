"""封面 / 图文卡片渲染：HTML 模板 → 无头 Chrome 截图 → PNG。

三种风格对应三个已验证的设计来源：
- hallmark：编辑感排版、米白纸面、衬线大标题（hallmark 的反 AI 味原则）；
- nothing：OLED 黑 + 点阵 + 等宽标签（nothing-design 的 tokens）；
- huashu：高饱和色块 + 超粗中文标题（huashu-design 的成品感）。
"""
from __future__ import annotations

import html
import subprocess
from datetime import datetime
from pathlib import Path

from .paths import FUSION_DIR, chrome_exe, ensure_dir, slugify

SIZES = {"1080x1440": (1080, 1440), "1080x1920": (1080, 1920), "1920x1080": (1920, 1080),
         "1080x1080": (1080, 1080)}
STYLES = ("hallmark", "nothing", "huashu")


def _title_size(style: str, width: int, length: int) -> int:
    """按标题字数选字号，避免长标题把末字挤到下一行。"""
    base = {"nothing": 0.115, "huashu": 0.135, "hallmark": 0.105}[style]
    if length <= 5:
        factor = base
    elif length <= 7:
        factor = base * 0.82
    elif length <= 10:
        factor = base * 0.68
    else:
        factor = base * 0.56
    return max(28, int(width * factor))


def _shell(style: str, width: int, height: int, body: str, title_len: int = 6) -> str:
    common = f"""*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{width}px;height:{height}px;overflow:hidden}}
body{{-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}}
.wrap{{position:relative;width:{width}px;height:{height}px;overflow:hidden;padding:{int(width*0.075)}px;display:flex;flex-direction:column;justify-content:space-between}}
.kicker{{font-size:{max(14, int(width*0.018))}px;letter-spacing:.22em;text-transform:uppercase}}
.title{{font-weight:800;letter-spacing:-.02em;line-height:1.08;word-break:break-word;text-wrap:balance}}
.sub{{font-size:{max(18, int(width*0.026))}px;line-height:1.5}}
.foot{{display:flex;justify-content:space-between;align-items:flex-end;font-size:{max(13, int(width*0.015))}px;letter-spacing:.08em}}
.num{{font-variant-numeric:tabular-nums}}"""
    if style == "nothing":
        palette = """body{background:#000;color:#f2f2f2;font-family:'Microsoft YaHei','PingFang SC',system-ui,sans-serif}
.wrap{background-image:radial-gradient(#1b1b1b 1px,transparent 1px);background-size:22px 22px}
.kicker{font-family:ui-monospace,Consolas,monospace;color:#8a8a8a}
.title{font-family:'Arial Black','Microsoft YaHei',sans-serif;font-size:var(--title-size);text-transform:uppercase}
.sub{color:#b9b9b9}
.dot{width:14px;height:14px;border-radius:50%%;background:#f05f41;display:inline-block;margin-right:10px}
.foot{font-family:ui-monospace,Consolas,monospace;color:#8a8a8a}"""
    elif style == "huashu":
        palette = """body{background:#f4f1e8;color:#111;font-family:'Microsoft YaHei','PingFang SC',system-ui,sans-serif}
.block{position:absolute;left:0;top:0;width:%dpx;height:%dpx;background:#2462ea}
.title{position:relative;z-index:2;color:#fff;font-size:var(--title-size);font-weight:900;line-height:1.02;letter-spacing:-.03em}
.wrap{padding-top:%dpx}
.kicker{color:#fff;font-weight:700;opacity:.9}
.sub{color:#333}
.foot{color:#555}
.tag{display:inline-block;background:#ffd34d;color:#111;font-weight:800;padding:10px 22px;border-radius:6px;font-size:%dpx;transform:rotate(-2deg)}""" % (
            width, int(height * 0.52), int(height * 0.30), max(18, int(width * 0.028)))
    else:  # hallmark
        palette = """body{background:#faf7f0;color:#1a1a1a;font-family:Georgia,'Songti SC','Microsoft YaHei',serif}
.title{font-size:var(--title-size);font-weight:700;line-height:1.06;letter-spacing:-.02em}
.kicker{font-family:ui-monospace,Consolas,monospace;color:#8a8175}
.sub{color:#4a453d;font-family:'Microsoft YaHei','PingFang SC',sans-serif}
.foot{font-family:ui-monospace,Consolas,monospace;color:#8a8175}
.rule{height:1px;background:#1a1a1a;opacity:.25;margin:%dpx 0}""" % (int(height * 0.03))
    title_size = _title_size(style, width, title_len)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>:root{{--title-size:{title_size}px}}
{common}
{palette}</style></head><body><div class="wrap">{body}</div></body></html>"""


def _body(style: str, title: str, subtitle: str, badge: str, index: int) -> str:
    title = html.escape(title)
    subtitle = html.escape(subtitle)
    badge = html.escape(badge or "CREATORHUB")
    if style == "nothing":
        return (f'<div><div class="kicker"><span class="dot"></span>{badge}</div>'
                f'<div class="title">{title}</div></div>'
                f'<div><div class="sub">{subtitle}</div>'
                f'<div class="foot"><span>CREATOR OS / {datetime.utcnow():%Y.%m}</span>'
                f'<span class="num">NO.{index:03d}</span></div></div>')
    if style == "huashu":
        return (f'<div class="block"></div>'
                f'<div><div class="kicker">{badge}</div>'
                f'<div class="title">{title}</div></div>'
                f'<div><div class="tag">{subtitle}</div>'
                f'<div class="foot" style="margin-top:24px"><span>CREATORHUB</span>'
                f'<span class="num">{datetime.utcnow():%Y-%m-%d}</span></div></div>')
    return (f'<div><div class="kicker">{badge}</div>'
            f'<div class="title" style="margin-top:24px">{title}</div></div>'
            f'<div class="rule"></div>'
            f'<div><div class="sub">{subtitle}</div>'
            f'<div class="foot" style="margin-top:28px"><span>CREATORHUB · 内容生产</span>'
            f'<span class="num">{datetime.utcnow():%Y.%m.%d}</span></div></div>')


def render_cover(title: str, subtitle: str = "", style: str = "hallmark",
                 size: str = "1080x1440", badge: str = "CREATORHUB",
                 index: int = 1) -> dict:
    if not title.strip():
        raise ValueError("标题不能为空")
    if style not in STYLES:
        raise ValueError(f"style 只支持 {', '.join(STYLES)}")
    if size not in SIZES:
        raise ValueError(f"size 只支持 {', '.join(SIZES)}")
    chrome = chrome_exe()
    if not chrome:
        raise RuntimeError("找不到 Chrome/Edge，无法渲染封面")

    width, height = SIZES[size]
    html_text = _shell(style, width, height, _body(style, title, subtitle, badge, index),
                       title_len=len(title))
    folder = ensure_dir(FUSION_DIR / "covers")
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{style}-{slugify(title)}"
    html_path = folder / f"{base}.html"
    png_path = folder / f"{base}.png"
    html_path.write_text(html_text, encoding="utf-8")

    result = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--force-device-scale-factor=1", f"--window-size={width},{height}",
         f"--screenshot={png_path}", html_path.resolve().as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90, stdin=subprocess.DEVNULL)
    if not png_path.is_file() or png_path.stat().st_size < 2000:
        raise RuntimeError(f"封面渲染失败：{result.stderr[-300:] or '未生成图片'}")
    return {"path": str(png_path), "html": str(html_path), "style": style,
            "width": width, "height": height, "bytes": png_path.stat().st_size,
            "note": "模板只做版式；发布前请过一遍去 AI 味质检（/api/fusion/deai）。"}
