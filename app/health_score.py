"""账号健康分 — "AI新媒体员工"的体检仪。

0-100 分，四个维度（权重合计 100）：
  活跃度 30: 最近发布节奏（有账号7天+没发内容 → 扣）
  数据力 25: 互动合计趋势（粉丝下滑/作品零播放 → 扣）
  登录健康 25: 登录态与新鲜度（invalid 直接红；长期未活动 → 扣）
  风险面 20: 近期风控事件（事件越多扣越多）

输出 {score, grade(红黄绿), factors:[{name, impact, detail}]}——
每个扣分都带原因，"员工"要能向老板解释每一分。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import select

from .db import get_session
from .models import AccountStatSnapshot, AccountWork, DouyinAccount, RiskEvent

GRADE = {(80, 101): "🟢", (50, 80): "🟡", (-1, 50): "🔴"}

STALE_PUBLISH_DAYS = 7      # 超过这么多天没发新内容 → 活跃度扣分
STALE_LOGIN_DAYS = 7        # 超过这么多天无任何活动 → 登录健康扣分
RISK_RECENT_DAYS = 14       # 近两周的风控事件才计入风险面
ZERO_PLAY_DAYS = 3          # 3天内发布的作品仍 0 播放 → 数据力扣分


def _days_since(dt: datetime | None, now: datetime) -> int | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)  # 模块内统一 naive-UTC
    return max(0, (now - dt).days)


def compute_health(account_id: int, *, now: datetime | None = None) -> dict:
    """给单个账号算健康分。只读。"""
    now = now or datetime.utcnow()
    factors: list[dict] = []
    score = 100.0

    def deduct(name: str, impact: float, detail: str) -> None:
        nonlocal score
        score -= impact
        factors.append({"name": name, "impact": round(impact, 1), "detail": detail})

    with get_session() as session:
        acc = session.get(DouyinAccount, account_id)
        if acc is None:
            return {"score": 0, "grade": "🔴", "factors": [
                {"name": "账号", "impact": 100, "detail": "账号不存在"}]}
        works = session.exec(select(AccountWork).where(
            AccountWork.account_id == account_id)).all()
        snaps = sorted(
            (s for s in session.exec(select(AccountStatSnapshot).where(
                AccountStatSnapshot.account_id == account_id)).all()),
            key=lambda s: s.date)
        risk_events = session.exec(select(RiskEvent).where(
            RiskEvent.account_id == account_id)).all()

    # ── 登录健康 25 ─────────────────────────────────────────────
    if acc.status == "invalid":
        deduct("登录态", 25, "登录已失效，所有功能暂停，请重新扫码")
    else:
        last_active = _days_since(acc.last_active_at or acc.created_at, now)
        if last_active is None:
            deduct("登录新鲜度", 10, "从未刷新过资料/活动记录，点一次「刷新资料」")
        elif last_active >= STALE_LOGIN_DAYS:
            deduct("登录新鲜度", min(15, last_active - STALE_LOGIN_DAYS + 5),
                   f"{last_active} 天没有任何活动，建议打开检查一次登录")

    # ── 活跃度 30 ───────────────────────────────────────────────
    platform_works = [w for w in works if (w.create_time or 0) > 0]
    latest_publish = max((w.create_time for w in platform_works), default=0)
    if latest_publish:
        latest_dt = datetime.fromtimestamp(latest_publish)
        gap = _days_since(latest_dt, now)
        if gap >= STALE_PUBLISH_DAYS * 2:
            deduct("发布频率", 30, f"已经 {gap} 天没发新内容")
        elif gap >= STALE_PUBLISH_DAYS:
            deduct("发布频率", 15, f"{gap} 天没发新内容，节奏该续上了")
    elif acc.aweme_count and acc.aweme_count > 0:
        deduct("发布频率", 20, "平台上有作品但从未同步过，点一次「同步作品」")
    else:
        deduct("发布频率", 30, "没有任何已同步的作品记录")

    # ── 数据力 25 ───────────────────────────────────────────────
    recent_cut = (now - timedelta(days=ZERO_PLAY_DAYS)).timestamp()
    fresh_zero = [w for w in platform_works
                  if w.create_time >= recent_cut and (w.play_count or 0) == 0]
    if fresh_zero:
        deduct("数据表现", min(15, 5 * len(fresh_zero)),
               f"{len(fresh_zero)} 条新作品 {ZERO_PLAY_DAYS} 天内仍 0 播放，到平台确认状态")
    if len(snaps) >= 2:
        delta = snaps[-1].follower_count - snaps[-2].follower_count
        if delta < 0:
            deduct("粉丝趋势", min(10, 3 + abs(delta) // 10),
                   f"粉丝下滑 {abs(delta)}")
        elif delta == 0 and len(snaps) >= 3:
            deduct("粉丝趋势", 3, "粉丝连续无增长，考虑换内容角度")

    # ── 风险面 20 ───────────────────────────────────────────────
    recent_cut_dt = now - timedelta(days=RISK_RECENT_DAYS)
    recent_risks = [r for r in risk_events
                    if getattr(r, "occurred_at", None)
                    and _days_since(r.occurred_at, now) is not None
                    and (now - (r.occurred_at if r.occurred_at.tzinfo is None
                                else r.occurred_at.replace(tzinfo=None))).days
                    <= RISK_RECENT_DAYS]
    if recent_risks:
        deduct("风控事件", min(20, 8 * len(recent_risks)),
               f"近 {RISK_RECENT_DAYS} 天有 {len(recent_risks)} 次风控/异常记录，降低操作频率")

    score = max(0, round(score))
    grade = next((g for (lo, hi), g in GRADE.items() if lo <= score < hi), "🔴")
    return {
        "account_id": account_id,
        "platform": acc.platform,
        "nickname": acc.nickname,
        "score": score,
        "grade": grade,
        "factors": [f for f in factors if f["impact"] > 0],
        "checked_at": now.isoformat(timespec="seconds"),
    }


def compute_all() -> list[dict]:
    with get_session() as session:
        ids = [a.id for a in session.exec(select(DouyinAccount)).all()]
    return sorted((compute_health(i) for i in ids),
                  key=lambda r: r["score"])
