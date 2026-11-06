"""晨报生成器 — "AI新媒体员工"的第一块交付物。

把库里的账号/作品/趋势数据压缩成老板能 30 秒看完的三句话报告:
  1. 昨天/近 N 天概况(账号健康灯 + 新作品 + 粉丝变化)
  2. 最值得看的一条(互动最高作品)
  3. 今天建议(规则引擎: 未登录 / 久未发布 / 零播放 / 风控推迟)

设计原则(见《产品重设计》第四节):
- 不暴露任何内部术语(transport/identity/risk_deferred), 只说结果和建议
- 纯只读; 规则引擎先行, 不强依赖 LLM(LLM 摘要留作可选增强)
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from sqlmodel import select

from .db import get_session
from .models import AccountStatSnapshot, AccountWork, DouyinAccount

PLATFORM_LABEL = {"douyin": "抖音", "xhs": "小红书", "kuaishou": "快手",
                  "shipinhao": "视频号", "tiktok": "TikTok"}

# 账号状态 -> 交通灯(老板语言)
_LIGHT = {"active": "🟢", "invalid": "🔴", "blocked": "🟡"}

# 超过这么久没登录过(资料刷新/同步过), 视为"需要点一下登录"
_STALE_LOGIN_DAYS = 7


def _platform_label(platform: str) -> str:
    return PLATFORM_LABEL.get(platform, platform or "未知")


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def build_report(days: int = 1, today: date | None = None) -> dict:
    """汇总近 days 天数据, 返回结构化晨报 dict(测试友好, 不直接碰IO格式)。"""
    days = max(1, min(int(days), 30))
    today = today or date.today()
    # 统一 UTC 语义: cutoff 为"今天(UTC)零点"的纯日期运算,避免本地时区漂移
    since_ts = (today - timedelta(days=days) - date(1970, 1, 1)).total_seconds()

    accounts: list = []
    works: list = []
    snaps: list = []
    with get_session() as session:
        accounts = session.exec(select(DouyinAccount)).all()
        # create_time 是平台秒级时间戳; 库很小, 全量取出后按天过滤即可
        works = session.exec(select(AccountWork)).all()
        works = [w for w in works if (w.create_time or 0) >= since_ts]
        snaps = session.exec(select(AccountStatSnapshot)).all()

    # ── 1. 账号健康灯 ────────────────────────────────────────────
    lights = Counter(_LIGHT.get(a.status or "", "🟡") for a in accounts)
    now = datetime.utcnow()
    login_warnings: list[str] = []
    for a in accounts:
        last = _parse_ts(a.last_active_at) or _parse_ts(a.created_at)
        if last is not None and last.tzinfo is not None:
            last = last.replace(tzinfo=None)  # 模块内统一 naive-UTC
        stale_days = (now - last).days if last else None
        if a.status == "invalid":
            login_warnings.append(
                f"{_platform_label(a.platform)}「{a.nickname or a.id}」登录已失效,请重新扫码")
        elif stale_days is not None and stale_days >= _STALE_LOGIN_DAYS:
            login_warnings.append(
                f"{_platform_label(a.platform)}「{a.nickname or a.id}」"
                f"{stale_days} 天没活动了,建议打开检查一次登录")

    # ── 2. 新作品与粉丝变化 ──────────────────────────────────────
    new_works: list[AccountWork] = [
        w for w in works if (w.create_time or 0) >= since_ts]
    works_by_account: dict[int, int] = Counter(w.account_id for w in new_works)
    acc_by_id = {a.id: a for a in accounts}

    def _snap_series(account_id: int) -> list[AccountStatSnapshot]:
        return sorted((s for s in snaps if s.account_id == account_id),
                      key=lambda s: s.date)

    fan_lines: list[str] = []
    for a in accounts:
        series = _snap_series(a.id)
        if len(series) >= 2:
            delta = series[-1].follower_count - series[-2].follower_count
            if delta:
                sign = "+" if delta > 0 else ""
                fan_lines.append(
                    f"{_platform_label(a.platform)}「{a.nickname or a.id}」"
                    f"粉丝 {series[-1].follower_count}({sign}{delta})")
        elif series:
            fan_lines.append(
                f"{_platform_label(a.platform)}「{a.nickname or a.id}」"
                f"粉丝 {series[-1].follower_count}(数据累计中)")

    # ── 3. 最值得看的一条 ─────────────────────────────────────────
    def _heat(w: AccountWork) -> int:
        return (w.like_count or 0) + (w.comment_count or 0) * 3 + \
               (w.collect_count or 0) * 2 + (w.share_count or 0) * 2

    top_work = max(works, key=_heat, default=None)
    top_line = None
    if top_work is not None and _heat(top_work) > 0:
        acc = acc_by_id.get(top_work.account_id)
        top_line = {
            "platform": _platform_label(top_work.platform),
            "account": (acc.nickname if acc else "") or str(top_work.account_id),
            "desc": (top_work.desc or "").strip()[:40] or "(无标题)",
            "like": top_work.like_count or 0,
            "comment": top_work.comment_count or 0,
            "share": top_work.share_count or 0,
            "collect": top_work.collect_count or 0,
        }

    # ── 4. 今天建议(规则引擎) ─────────────────────────────────────
    advice: list[str] = []
    advice.extend(login_warnings)
    three_days_ago = (today - timedelta(days=3) - date(1970, 1, 1)).total_seconds()
    zero_play = [w for w in new_works
                 if (w.play_count or 0) == 0
                 and (w.create_time or 0) >= three_days_ago]
    if zero_play:
        advice.append(
            f"有 {len(zero_play)} 条 3 天内发布的新作品还没播放量,建议到平台确认是否正常")
    idle = [a for a in accounts
            if a.status == "active"
            and works_by_account.get(a.id, 0) == 0
            and (a.aweme_count or 0) > 0]
    if idle:
        names = "、".join(
            f"{_platform_label(a.platform)}「{a.nickname or a.id}」" for a in idle[:3])
        advice.append(f"{names} 近 {days} 天没发过新内容,有存量的号建议补一条")
    if not advice:
        advice.append("一切正常,今天也保持节奏 👍")

    return {
        "date": today.isoformat(),
        "days": days,
        "accounts": {
            "total": len(accounts),
            "lights": dict(lights),
            "lines": [
                {"platform": _platform_label(a.platform),
                 "nickname": a.nickname or str(a.id),
                 "status": a.status or "unknown",
                 "light": _LIGHT.get(a.status or "", "🟡"),
                 "follower_count": a.follower_count or 0,
                 "aweme_count": a.aweme_count or 0}
                for a in accounts],
        },
        "new_works": len(new_works),
        "fans_lines": fan_lines,
        "top": top_line,
        "advice": advice,
    }


def render_markdown(report: dict) -> str:
    """渲染成老板 30 秒能读完的纯文本晨报。"""
    lights = report["accounts"]["lights"]
    light_text = " ".join(f"{k}{v}" for k, v in sorted(lights.items())) or "🟢"
    lines = [
        f"# ☀️ 新媒体员工晨报 · {report['date']}",
        "",
        f"**账号健康**:{light_text}(共 {report['accounts']['total']} 个号)"
        f" · 近 {report['days']} 天新作品 **{report['new_works']}** 条",
    ]
    if report["fans_lines"]:
        lines.append("")
        lines.append("**粉丝动态**:" + ";".join(report["fans_lines"]))
    top = report.get("top")
    if top:
        lines += [
            "",
            f"**最值得看的一条**:{top['platform']}「{top['account']}」"
            f"{top['desc']}",
            f"  👍 {top['like']} · 💬 {top['comment']} · 🔁 {top['share']} · ⭐ {top['collect']}",
        ]
    lines += ["", "**今天建议**:"]
    lines += [f"- {a}" for a in report["advice"]]
    lines += ["", "---", "*由你的 AI 新媒体员工自动生成*"]
    return "\n".join(lines)


def build_and_render(days: int = 1) -> tuple[dict, str]:
    report = build_report(days=days)
    return report, render_markdown(report)


def main() -> int:  # pragma: no cover - CLI 便利入口
    import argparse

    parser = argparse.ArgumentParser(description="生成新媒体晨报")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--out", default="", help="写出路径(默认打印)")
    args = parser.parse_args()
    _, text = build_and_render(days=args.days)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"saved: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
