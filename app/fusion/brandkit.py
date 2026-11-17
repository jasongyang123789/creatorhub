"""品牌包生成：三概念 SVG + 测试板 PNG（logo-design skill 的方法论）。

方法论来自 logo-design-skill（MIT）：黑稿起稿、几何构图、概念检查点、
尺寸阶梯（512→16px）与单色/反白测试。字母用 <text> 属探索稿，会在
note 里标注"发布前请换成路径字稿"。代码自写。
"""
from __future__ import annotations

import html
import subprocess
from datetime import datetime
from pathlib import Path

from .paths import FUSION_DIR, chrome_exe, ensure_dir, slugify

ACCENTS = {"blue": "#2462ea", "ikb": "#002FA7", "orange": "#f97316", "green": "#16a34a",
           "red": "#dc2626", "black": "#111111"}


def _monogram_svg(initial: str, accent: str) -> str:
    """概念 A：几何外框 + 首字。外框 = 旋转 45° 方框 + 圆点。"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
<rect width="256" height="256" fill="#ffffff"/>
<rect x="48" y="48" width="160" height="160" transform="rotate(45 128 128)" fill="none" stroke="#111111" stroke-width="10"/>
<circle cx="128" cy="128" r="18" fill="{accent}"/>
<text x="128" y="158" text-anchor="middle" font-family="Arial Black, Microsoft YaHei, sans-serif" font-size="88" font-weight="900" fill="#111111">{html.escape(initial)}</text>
</svg>"""


def _abstract_svg(accent: str) -> str:
    """概念 B：纯几何抽象标——两枚错位圆 + 一根竖条。"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
<rect width="256" height="256" fill="#ffffff"/>
<circle cx="104" cy="118" r="56" fill="#111111"/>
<circle cx="164" cy="138" r="56" fill="none" stroke="{accent}" stroke-width="12"/>
<rect x="118" y="40" width="14" height="176" rx="7" fill="#111111"/>
</svg>"""


def _wordmark_svg(name: str, accent: str) -> str:
    """概念 C：字标——粗体紧排 + 强调色下划线条。"""
    chars = len(name)
    width = max(256, min(720, chars * 64 + 64))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 256">
<rect width="{width}" height="256" fill="#ffffff"/>
<text x="{width / 2}" y="140" text-anchor="middle" font-family="Arial Black, Microsoft YaHei, sans-serif" font-size="72" font-weight="900" letter-spacing="-2" fill="#111111">{html.escape(name)}</text>
<rect x="{width / 2 - chars * 22}" y="168" width="{chars * 44}" height="10" rx="5" fill="{accent}"/>
</svg>"""


def _sheet(concepts: list[dict], name: str, adjectives: str, accent: str) -> str:
    """测试板 HTML：尺寸阶梯 / 单色 / 反白 / 眯眼测试，一次看清。"""
    rows = []
    for concept in concepts:
        svg = Path(concept["svg"]).read_text(encoding="utf-8")
        # 反白版：底变黑、主色换白
        reversed_svg = (svg.replace('fill="#ffffff"', 'fill="#000000"')
                          .replace('fill="#111111"', 'fill="#f5f5f5"')
                          .replace('stroke="#111111"', 'stroke="#f5f5f5"'))
        one_colour = svg.replace(f'fill="{accent}"', 'fill="#111111"')
        rows.append(f"""<div class="concept">
<div class="head"><b>{html.escape(concept['name'])}</b><span>{html.escape(concept['idea'])}</span></div>
<div class="row">
  <div class="cell"><div class="big">{svg}</div><label>512 主视觉</label></div>
  <div class="cell ladder">
    <div class="s1">{svg}</div><div class="s2">{svg}</div><div class="s3">{svg}</div>
    <label>128 / 32 / 16px</label>
  </div>
  <div class="cell"><div class="one">{one_colour}</div><label>单色</label></div>
  <div class="cell"><div class="rev">{reversed_svg}</div><label>反白</label></div>
</div></div>""")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
body{{margin:0;background:#f5f6f8;color:#111;font:14px/1.5 'Microsoft YaHei',system-ui,sans-serif}}
.head{{display:flex;gap:12px;align-items:baseline;padding:14px 4px 8px}}
.head span{{color:#666;font-size:12px}}
.concept{{background:#fff;border:1px solid #e3e6ec;border-radius:12px;padding:18px;margin:16px}}
.row{{display:flex;gap:18px;align-items:flex-start}}
.cell{{text-align:center}} .cell label{{display:block;color:#888;font-size:11px;margin-top:6px}}
.big svg{{width:200px;height:200px}}
.s1 svg{{width:64px;height:64px}} .s2 svg{{width:32px;height:32px}} .s3 svg{{width:16px;height:16px}}
.ladder div{{display:inline-block;margin-right:6px;vertical-align:bottom}}
.one svg,.rev svg{{width:110px;height:110px}}
.rev{{background:#000;display:inline-block;padding:8px}}
.pagehead{{padding:26px 20px 4px}} .pagehead h1{{font-size:20px;margin:0 0 4px}}
.pagehead p{{color:#666;margin:0;font-size:12.5px}}
</style></head><body>
<div class="pagehead"><h1>{html.escape(name)} · 品牌包测试板</h1>
<p>调性词：{html.escape(adjectives) or "—"} · 强调色 {accent} · 三个概念，每个都过尺寸阶梯 / 单色 / 反白 · {datetime.utcnow():%Y-%m-%d}</p></div>
{''.join(rows)}
</body></html>"""


def render_brandkit(name: str, adjectives: str = "", accent: str = "blue") -> dict:
    if not name.strip():
        raise ValueError("品牌名不能为空")
    if accent not in ACCENTS:
        raise ValueError(f"accent 只支持 {', '.join(ACCENTS)}")
    accent_hex = ACCENTS[accent]
    name = name.strip()[:20]
    initial = name[:1].upper()
    folder = ensure_dir(FUSION_DIR / "brandkits" / f"{datetime.utcnow():%Y%m%d-%H%M%S}-{slugify(name)}")

    concepts = [
        {"name": "概念 A · 几何字标", "idea": "旋转外框 + 首字 + 强调色圆点",
         "svg": folder / "concept-a-monogram.svg"},
        {"name": "概念 B · 抽象几何", "idea": "两圆错位 + 立柱：秩序与连接",
         "svg": folder / "concept-b-abstract.svg"},
        {"name": "概念 C · 纯字标", "idea": "粗体紧排 + 强调色条",
         "svg": folder / "concept-c-wordmark.svg"},
    ]
    for concept in concepts:
        path = Path(concept["svg"])
        if "A" in concept["name"]:
            content = _monogram_svg(initial, accent_hex)
        elif "B" in concept["name"]:
            content = _abstract_svg(accent_hex)
        else:
            content = _wordmark_svg(name, accent_hex)
        path.write_text(content, encoding="utf-8")
        concept["path"] = str(path)

    sheet_path = folder / "sheet.html"
    sheet_path.write_text(_sheet(concepts, name, adjectives, accent_hex), encoding="utf-8")

    chrome = chrome_exe()
    if not chrome:
        raise RuntimeError("找不到 Chrome/Edge，无法渲染测试板")
    board_path = folder / "board.png"
    result = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--window-size=1280,960", "--screenshot=" + str(board_path),
         sheet_path.resolve().as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90, stdin=subprocess.DEVNULL)
    if not board_path.is_file() or board_path.stat().st_size < 5000:
        raise RuntimeError(f"测试板渲染失败：{result.stderr[-300:] or '未生成图片'}")
    return {"dir": str(folder), "board": str(board_path), "sheet": str(sheet_path),
            "concepts": [{"name": c["name"], "idea": c["idea"], "svg": c["path"]} for c in concepts],
            "bytes": board_path.stat().st_size,
            "note": "探索稿用 <text> 拼字；定稿前请按 logo-design 流程把字稿转路径并做商标查重。"}
