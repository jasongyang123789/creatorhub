"""资产管理面板 — "AI新媒体员工"的资产总览(只读快照)。

与晨报的分工:
  - 晨报 = 每天 30 秒的"发生了什么"
  - 资产面板 = 随时打开的"我现在有什么": 每个账号的作品清单、互动合计、
    最近发布时间、风控/异常事件

安全设计与晨报面板一致: 只读、不含登录态、昵称默认脱敏、--full-names 才完整。
"""
from __future__ import annotations

import argparse
import copy
import html
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import select

from .db import get_session
from .models import AccountWork, DouyinAccount, RiskEvent
from .morning_panel import _h, _mask
from .morning_report import PLATFORM_LABEL


def _label(platform: str) -> str:
    return PLATFORM_LABEL.get(platform, platform or "未知")


def _ts(value) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%m-%d %H:%M")
    try:
        return datetime.fromtimestamp(int(value)).strftime("%m-%d %H:%M")
    except (ValueError, TypeError, OSError, OverflowError):
        return str(value)[:16]


def build_asset_report(top_n_works: int = 3) -> dict:
    accounts: list = []
    works: list = []
    risks: list = []
    with get_session() as session:
        accounts = session.exec(select(DouyinAccount)).all()
        works = session.exec(select(AccountWork)).all()
        risks = session.exec(
            select(RiskEvent).order_by(RiskEvent.occurred_at.desc())  # type: ignore[attr-defined]
        ).all() if _has_occurred() else []

    def _heat(w: AccountWork) -> int:
        return ((w.like_count or 0) + (w.comment_count or 0) * 3
                + (w.collect_count or 0) * 2 + (w.share_count or 0) * 2)

    acc_blocks = []
    for a in accounts:
        mine = sorted((w for w in works if w.account_id == a.id),
                      key=lambda w: _heat(w), reverse=True)
        total = {
            "like": sum(w.like_count or 0 for w in mine),
            "comment": sum(w.comment_count or 0 for w in mine),
            "collect": sum(w.collect_count or 0 for w in mine),
            "share": sum(w.share_count or 0 for w in mine),
        }
        latest = max((w.create_time or 0) for w in mine) if mine else 0
        acc_blocks.append({
            "id": a.id,
            "platform": _label(a.platform),
            "nickname": a.nickname or str(a.id),
            "status": a.status or "unknown",
            "follower_count": a.follower_count or 0,
            "works_total": a.aweme_count or 0,
            "synced": len(mine),
            "total": total,
            "latest_publish": _ts(latest),
            "top_works": [{
                "desc": (w.desc or "").strip()[:36] or "(无标题)",
                "like": w.like_count or 0,
                "comment": w.comment_count or 0,
                "collect": w.collect_count or 0,
                "share": w.share_count or 0,
                "create": _ts(w.create_time),
            } for w in mine[:top_n_works]],
        })

    risk_lines = [{
        "account": next(((_label(a.platform) + "「" + (a.nickname or str(a.id)) + "」")
                          for a in accounts if a.id == r.account_id), f"#{r.account_id}"),
        "occurred": _ts(getattr(r, "occurred_at", None)),
        "detail": ((r.detail or "")[:80]) if hasattr(r, "detail") else "",
        "outcome": getattr(r, "outcome", "") or "",
    } for r in risks[:8]]

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "accounts": acc_blocks,
        "risk_events": risk_lines,
        "totals": {
            "accounts": len(accounts),
            "synced_works": len(works),
            "like": sum(b["total"]["like"] for b in acc_blocks),
            "comment": sum(b["total"]["comment"] for b in acc_blocks),
        },
    }


def _has_occurred() -> bool:  # RiskEvent 无 occurred_at 字段的老库兼容
    return True


def _mask_line(name: str) -> str:
    return _mask(name)


def render_html(report: dict, *, mask_names: bool = True) -> str:
    g = datetime.now().strftime("%Y-%m-%d %H:%M")
    tot = report["totals"]
    cards = (
        f'<div class="card"><div class="k">账号</div><div class="v">{tot["accounts"]}</div></div>'
        f'<div class="card"><div class="k">已同步作品</div><div class="v">{tot["synced_works"]}</div></div>'
        f'<div class="card"><div class="k">互动合计</div><div class="v">{tot["like"] + tot["comment"]}</div>'
        f'<div class="s">👍{tot["like"]} · 💬{tot["comment"]}</div></div>')

    blocks = []
    for acc in report["accounts"]:
        name = _mask_line(acc["nickname"]) if mask_names else acc["nickname"]
        light = {"active": "🟢", "invalid": "🔴"}.get(acc["status"], "🟡")
        top_rows = "".join(
            f'<tr><td>{_h(w["desc"])}</td>'
            f'<td class="num">{_h(w["like"])}</td>'
            f'<td class="num">{_h(w["comment"])}</td>'
            f'<td class="num">{_h(w["collect"] + w["share"])}</td>'
            f'<td class="num">{_h(w["create"])}</td></tr>'
            for w in acc["top_works"]) or '<tr><td colspan="5" class="empty">暂无已同步作品</td></tr>'
        blocks.append(f"""
<section>
  <h2>{light} {_h(acc['platform'])} · {_h(name)}
      <span class="meta">粉丝 {_h(acc['follower_count'])} · 平台作品 {_h(acc['works_total'])} · 已同步 {_h(acc['synced'])} · 最近发布 {_h(acc['latest_publish'])}</span>
  </h2>
  <div class="sum">👍 {_h(acc['total']['like'])} · 💬 {_h(acc['total']['comment'])} · ⭐ {_h(acc['total']['collect'])} · 🔁 {_h(acc['total']['share'])}</div>
  <table><thead><tr><th>作品</th><th class="num">赞</th><th class="num">评</th>
  <th class="num">藏+转</th><th class="num">发布</th></tr></thead><tbody>{top_rows}</tbody></table>
</section>""")

    risk_html = ""
    if report["risk_events"]:
        def _who(e):
            name = _mask_line(e["account"]) if mask_names else e["account"]
            return name
        rows = "".join(
            f'<tr><td>{_h(e["occurred"])}</td><td>{_h(_who(e))}</td>'
            f'<td>{_h(e["detail"]) or _h(e["outcome"])}</td></tr>'
            for e in report["risk_events"])
        risk_html = (f'<section><h2>⚠️ 近期风控/异常事件</h2>'
                     f'<table><tbody>{rows}</tbody></table></section>')

    mask_note = "账号昵称已脱敏" if mask_names else "完整昵称（仅限内部访问）"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI新媒体员工 · 资产总览</title>
<style>
  :root{{--bg:#f6f7f9;--card:#fff;--ink:#1a1d23;--sub:#6b7280;--line:#e5e7eb;--accent:#e0245e}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
  .wrap{{max-width:860px;margin:0 auto;padding:24px 16px 48px}}
  h1{{font-size:22px;margin:0 0 4px}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0 8px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}}
  .card .k{{color:var(--sub);font-size:13px}}
  .card .v{{font-size:28px;font-weight:700;margin:2px 0}}
  section{{background:var(--card);border:1px solid var(--line);border-radius:14px;
    padding:16px 18px;margin-top:14px}}
  h2{{font-size:16px;margin:0 0 10px}}
  h2 .meta{{display:block;font-size:12px;color:var(--sub);font-weight:400;margin-top:2px}}
  .sum{{font-size:13px;color:#374151;margin-bottom:10px}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  th{{text-align:left;color:var(--sub);font-weight:500;font-size:12px;
    border-bottom:1px solid var(--line);padding:6px 4px}}
  td{{padding:8px 4px;border-bottom:1px solid #f1f2f4;word-break:break-all}}
  tr:last-child td{{border-bottom:none}}
  .num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  .empty{{color:var(--sub)}}
  footer{{margin-top:22px;color:var(--sub);font-size:12px;text-align:center}}
  .badge{{display:inline-block;background:#fff1f5;color:var(--accent);border-radius:999px;
    padding:1px 9px;font-size:12px;margin-left:6px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>🧰 AI 新媒体员工 · 资产总览<span class="badge">只读</span></h1>
  <div class="cards">{cards}</div>
  {''.join(blocks)}
  {risk_html}
  <footer>生成于 {_h(g)} · 只读快照，不含登录凭证 · {mask_note}</footer>
</div>
</body>
</html>
"""


def _masked(report: dict) -> dict:
    out = copy.deepcopy(report)
    for acc in out["accounts"]:
        acc["nickname"] = _mask(acc["nickname"])
    for e in out["risk_events"]:
        e["account"] = _mask_line(e["account"])
    return out


def build_asset_panel(out_dir: str | Path, *, mask_names: bool = True) -> tuple[Path, Path]:
    report = build_asset_report()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = out_dir / "index.html"
    json_path = out_dir / "data.json"
    html_path.write_text(render_html(report, mask_names=mask_names), encoding="utf-8")
    payload = _masked(report) if mask_names else report
    json_path.write_text(json.dumps(
        {"generated_at": stamp, **payload}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return html_path, json_path


def main() -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="生成资产总览面板")
    parser.add_argument("--out", default="out/asset-panel")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--full-names", action="store_true")
    args = parser.parse_args()
    from .config import load_config
    from .db import init_db
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(str(root / args.config))
    init_db(str((root / cfg.db_path).resolve()))
    html_path, json_path = build_asset_panel(
        root / args.out, mask_names=not args.full_names)
    print(f"panel: {html_path}")
    print(f"data:  {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
