"""架构图渲染：nodes/edges → 暗色 SVG → 无头 Chrome 截图 → PNG。

设计系统来自 architecture-diagram-generator（MIT）：语义色组件、slate-950 底、
点阵网格、JetBrains Mono 排版。代码自写，仅借鉴公开的配色与版式规范。
"""
from __future__ import annotations

import html
import subprocess
from datetime import datetime
from pathlib import Path

from .paths import FUSION_DIR, chrome_exe, ensure_dir, slugify

# 语义色（与 architecture-diagram skill 一致）
TYPES = {
    "frontend": ("rgba(8, 51, 68, 0.4)", "#22d3ee"),
    "backend": ("rgba(6, 78, 59, 0.4)", "#34d399"),
    "database": ("rgba(76, 29, 149, 0.4)", "#a78bfa"),
    "cloud": ("rgba(120, 53, 15, 0.3)", "#fbbf24"),
    "security": ("rgba(136, 19, 55, 0.4)", "#fb7185"),
    "bus": ("rgba(251, 146, 60, 0.3)", "#fb923c"),
    "external": ("rgba(30, 41, 59, 0.5)", "#94a3b8"),
}
SIZES = {"1600x1000": (1600, 1000), "1920x1080": (1920, 1080), "1080x1080": (1080, 1080)}
BOX_W, BOX_H = 210, 92

FONT = ("ui-monospace,'JetBrains Mono',Consolas,monospace")


def _layout(nodes: list[dict], width: int, height: int) -> dict[str, tuple[int, int]]:
    """自动布局：每行 3 个，居中排布；显式 x/y（0-1 比例坐标）优先。"""
    positions: dict[str, tuple[int, int]] = {}
    pending = []
    for node in nodes:
        x, y = node.get("x"), node.get("y")
        if x is not None and y is not None:
            positions[node["id"]] = (int(x * (width - BOX_W - 120) + 60),
                                     int(y * (height - BOX_H - 120) + 60))
        else:
            pending.append(node)
    cols = 3
    margin_x, margin_y = 70, 90
    for index, node in enumerate(pending):
        col, row = index % cols, index // cols
        row_count = min(cols, len(pending) - row * cols)
        row_width = row_count * BOX_W + (row_count - 1) * 70
        start_x = (width - row_width) // 2
        positions[node["id"]] = (start_x + col * (BOX_W + 70),
                                 margin_y + row * (BOX_H + 90))
    return positions


def _node_svg(node: dict, pos: tuple[int, int]) -> str:
    kind = node.get("type", "external")
    fill, stroke = TYPES.get(kind, TYPES["external"])
    label = html.escape(node.get("label", node["id"]))
    sub = html.escape(node.get("sub", ""))
    x, y = pos
    sub_svg = (f'<text x="{x + BOX_W / 2}" y="{y + 58}" text-anchor="middle" '
               f'font-size="9" fill="#94a3b8">{sub}</text>') if sub else ""
    return (f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" fill="#0f172a"/>'
            f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text x="{x + BOX_W / 2}" y="{y + (40 if sub else 52)}" text-anchor="middle" '
            f'font-size="12" fill="#e2e8f0" font-weight="600">{label}</text>{sub_svg}')


def _edge_svg(edge: dict, positions: dict[str, tuple[int, int]]) -> str | None:
    start, end = positions.get(edge["from"]), positions.get(edge["to"])
    if not start or not end:
        return None
    x1, y1 = start[0] + BOX_W / 2, start[1] + BOX_H / 2
    x2, y2 = end[0] + BOX_W / 2, end[1] + BOX_H / 2
    dash = ' stroke-dasharray="5,5"' if edge.get("dashed") else ""
    color = edge.get("color", "#64748b")
    label = html.escape(edge.get("label", ""))
    mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
    label_svg = (f'<rect x="{mid_x - len(label) * 4.2 - 6}" y="{mid_y - 22}" '
                 f'width="{len(label) * 8.4 + 12}" height="18" rx="3" fill="#020617"/>'
                 f'<text x="{mid_x}" y="{mid_y - 9}" text-anchor="middle" font-size="9" '
                 f'fill="#94a3b8">{label}</text>') if label else ""
    return (f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" '
            f'stroke="{color}" stroke-width="1.5"{dash} marker-end="url(#arrow)"/>'
            f'{label_svg}')


def render_diagram(title: str, nodes: list[dict], edges: list[dict],
                   size: str = "1600x1000", index: int = 1) -> dict:
    if not nodes or len(nodes) > 14:
        raise ValueError("nodes 需 1–14 个，每项含 id/label，可选 type/sub/x/y")
    ids = [str(n.get("id", "")).strip() for n in nodes]
    if len(set(ids)) != len(ids) or not all(ids):
        raise ValueError("node id 必须非空且唯一")
    for node in nodes:
        if node.get("type") and node["type"] not in TYPES:
            raise ValueError(f"type 只支持 {', '.join(TYPES)}")
    if len(edges) > 18:
        raise ValueError("edges 最多 18 条")
    if size not in SIZES:
        raise ValueError(f"size 只支持 {', '.join(SIZES)}")
    width, height = SIZES[size]
    positions = _layout(nodes, width, height)
    missing = [edge for edge in edges if edge["from"] not in positions or edge["to"] not in positions]
    if missing:
        raise ValueError(f"edge 引用了不存在的节点：{missing[0]['from']} → {missing[0]['to']}")

    edges_svg = "".join(filter(None, (_edge_svg(e, positions) for e in edges)))
    nodes_svg = "".join(_node_svg(n, positions[n["id"]]) for n in nodes)
    legend = "".join(
        f'<rect x="{40 + i * 150}" y="{height - 34}" width="10" height="10" rx="2" fill="{stroke}"/>'
        f'<text x="{56 + i * 150}" y="{height - 25}" font-size="10" fill="#94a3b8">{name}</text>'
        for i, (name, (_, stroke)) in enumerate(TYPES.items()))

    doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>html,body{{margin:0;background:#020617}}svg{{display:block}}
text{{font-family:{FONT};}}</style></head><body>
<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
<defs>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
<path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/></pattern>
<marker id="arrow" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
<polygon points="0 0, 10 3.5, 0 7" fill="#64748b"/></marker>
</defs>
<rect width="{width}" height="{height}" fill="#020617"/>
<rect width="{width}" height="{height}" fill="url(#grid)"/>
<text x="40" y="52" font-size="18" fill="#e2e8f0" font-weight="700">{html.escape(title)}</text>
<text x="{width - 40}" y="52" text-anchor="end" font-size="10" fill="#64748b">CREATORHUB · {datetime.utcnow():%Y.%m.%d}</text>
{edges_svg}
{nodes_svg}
{legend}
</svg></body></html>"""

    folder = ensure_dir(FUSION_DIR / "diagrams")
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{slugify(title, limit=30)}"
    html_path = folder / f"{base}.html"
    png_path = folder / f"{base}.png"
    html_path.write_text(doc, encoding="utf-8")

    chrome = chrome_exe()
    if not chrome:
        raise RuntimeError("找不到 Chrome/Edge，无法渲染架构图")
    result = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--window-size={width},{height}", f"--screenshot={png_path}",
         html_path.resolve().as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90, stdin=subprocess.DEVNULL)
    if not png_path.is_file() or png_path.stat().st_size < 3000:
        raise RuntimeError(f"架构图渲染失败：{result.stderr[-300:] or '未生成图片'}")
    return {"path": str(png_path), "html": str(html_path), "width": width, "height": height,
            "nodes": len(nodes), "edges": len(edges), "bytes": png_path.stat().st_size}
