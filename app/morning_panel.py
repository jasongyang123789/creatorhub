"""晨报公开面板生成器 — 把 build_report() 渲染成可部署的静态页。

用途: 部署到服务器给老板/自己远程看(手机也能看)。
安全设计(重要):
  - 只读快照, 不含 Cookie / profile / 任何登录态
  - 默认脱敏账号昵称(首字 + ***), 粉丝数与作品数保留
  - --full 才输出完整昵称, 仅限内网/加口令访问时使用
"""
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path

from .morning_report import build_report


def _mask(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "***"
    return name[0] + "***"


def _h(text: object) -> str:
    return html.escape(str(text), quote=True)


def render_html(report: dict, *, mask_names: bool = True,
                generated_at: str | None = None) -> str:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    acc = report["accounts"]
    lights = acc["lights"]
    green = lights.get("🟢", 0)
    yellow = lights.get("🟡", 0)
    red = lights.get("🔴", 0)

    cards = [
        ("账号", str(acc["total"]), f"🟢{green} 🟡{yellow} 🔴{red}"),
        ("近%d天新作品" % report["days"], str(report["new_works"]),
         "条发布" if report["new_works"] else "暂无"),
        ("最热作品互动",
         str((report["top"] or {}).get("like", 0)),
         "赞" if report.get("top") else "暂无数据"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{_h(k)}</div>'
        f'<div class="v">{_h(v)}</div><div class="s">{_h(s)}</div></div>'
        for k, v, s in cards)

    rows = []
    for line in acc["lines"]:
        name = _mask(line["nickname"]) if mask_names else line["nickname"]
        rows.append(
            "<tr>"
            f'<td>{_h(line["light"])} {_h(line["platform"])}</td>'
            f'<td>{_h(name)}</td>'
            f'<td class="num">{_h(line["follower_count"])}</td>'
            f'<td class="num">{_h(line["aweme_count"])}</td>'
            "</tr>")
    table = ("<table><thead><tr><th>平台</th><th>账号</th>"
             "<th class=\"num\">粉丝</th><th class=\"num\">作品</th></tr></thead>"
             f"<tbody>{''.join(rows)}</tbody></table>") if rows else \
        '<p class="empty">还没有账号，去工作台登录一个再回来。</p>'

    fan_html = ""
    if report["fans_lines"]:
        items = "".join(f"<li>{_h(x)}</li>" for x in report["fans_lines"])
        fan_html = f'<section><h2>粉丝动态</h2><ul class="fans">{items}</ul></section>'

    top = report.get("top")
    top_html = ""
    if top:
        name = _mask(top["account"]) if mask_names else top["account"]
        top_html = (
            '<section><h2>最值得看的一条</h2><div class="top">'
            f'<div class="t-title">{_h(top["desc"])}</div>'
            f'<div class="t-meta">{_h(top["platform"])} · {_h(name)}</div>'
            f'<div class="t-stats">👍 {_h(top["like"])} · 💬 {_h(top["comment"])}'
            f' · 🔁 {_h(top["share"])} · ⭐ {_h(top["collect"])}</div>'
            "</div></section>")

    advice = "".join(f"<li>{_h(a)}</li>" for a in report["advice"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>新媒体员工晨报 · {_h(report['date'])}</title>
<style>
  :root{{--bg:#f6f7f9;--card:#fff;--ink:#1a1d23;--sub:#6b7280;--line:#e5e7eb;--accent:#e0245e}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
  .wrap{{max-width:760px;margin:0 auto;padding:24px 16px 48px}}
  header{{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap}}
  h1{{font-size:22px;margin:0 0 4px}}
  .date{{color:var(--sub);font-size:13px}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0 8px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}}
  .card .k{{color:var(--sub);font-size:13px}}
  .card .v{{font-size:30px;font-weight:700;letter-spacing:-.5px;margin:2px 0}}
  .card .s{{color:var(--sub);font-size:12px}}
  section{{background:var(--card);border:1px solid var(--line);border-radius:14px;
    padding:16px 18px;margin-top:14px}}
  h2{{font-size:15px;margin:0 0 10px;color:var(--sub);font-weight:600;letter-spacing:.2px}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  th{{text-align:left;color:var(--sub);font-weight:500;font-size:12px;
    border-bottom:1px solid var(--line);padding:6px 4px}}
  td{{padding:8px 4px;border-bottom:1px solid #f1f2f4}}
  tr:last-child td{{border-bottom:none}}
  .num{{text-align:right;font-variant-numeric:tabular-nums}}
  ul{{margin:0;padding-left:18px}} li{{margin:4px 0}}
  .top .t-title{{font-size:17px;font-weight:600;margin-bottom:4px}}
  .top .t-meta{{color:var(--sub);font-size:13px}}
  .top .t-stats{{margin-top:8px;font-size:14px;color:#374151}}
  .empty{{color:var(--sub);margin:0}}
  footer{{margin-top:22px;color:var(--sub);font-size:12px;text-align:center;line-height:1.8}}
  .badge{{display:inline-block;background:#fff1f5;color:var(--accent);border-radius:999px;
    padding:1px 9px;font-size:12px;margin-left:6px}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>☀️ 新媒体员工晨报<span class="badge">AI 自动生成</span></h1>
      <div class="date">{_h(report['date'])} · 近 {report['days']} 天口径</div>
    </div>
  </header>

  <div class="cards">{card_html}</div>

  <section><h2>账号健康</h2>{table}</section>
  {fan_html}
  {top_html}
  <section><h2>今天建议</h2><ul>{advice}</ul></section>

  <footer>
    生成于 {_h(generated_at)} · 只读快照，不含任何登录凭证<br>
    {'账号昵称已脱敏' if mask_names else '完整昵称（仅限内部访问）'}
  </footer>
</div>
</body>
</html>
"""


def _masked(report: dict) -> dict:
    """深拷贝并脱敏昵称(供 data.json 用, 与页面保持一致)。"""
    import copy
    out = copy.deepcopy(report)
    for line in out["accounts"]["lines"]:
        line["nickname"] = _mask(line["nickname"])
    if out.get("top"):
        out["top"]["account"] = _mask(out["top"]["account"])
    out["fans_lines"] = [_mask_fans(x) for x in out["fans_lines"]]
    return out


def _mask_fans(line: str) -> str:
    """'小红书「子申」粉丝 75(+5)' -> '小红书「子***」粉丝 75(+5)'"""
    if "「" not in line or "」" not in line:
        return line
    head, rest = line.split("「", 1)
    name, tail = rest.split("」", 1)
    return f"{head}「{_mask(name)}」{tail}"


def build_panel(out_dir: str | Path, *, days: int = 7,
                mask_names: bool = True) -> tuple[Path, Path]:
    report = build_report(days=days)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = out_dir / "index.html"
    json_path = out_dir / "data.json"
    html_path.write_text(render_html(report, mask_names=mask_names,
                                     generated_at=stamp), encoding="utf-8")
    payload = _masked(report) if mask_names else report
    json_path.write_text(json.dumps(
        {"generated_at": stamp, **payload}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return html_path, json_path


def main() -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description="生成晨报公开面板")
    parser.add_argument("--out", default="out/morning-panel")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--config", default="config.yaml",
                        help="相对项目根的配置文件")
    parser.add_argument("--full-names", action="store_true",
                        help="输出完整昵称（仅限内网/口令保护时使用）")
    args = parser.parse_args()

    from pathlib import Path as _Path
    from .config import load_config
    from .db import init_db

    root = _Path(__file__).resolve().parents[1]
    cfg = load_config(str(root / args.config))
    init_db(str((root / cfg.db_path).resolve()))
    html_path, json_path = build_panel(
        root / args.out, days=args.days, mask_names=not args.full_names)
    print(f"panel: {html_path}")
    print(f"data:  {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())