"""健康分：四个维度扣分制，每分可解释。"""
from datetime import datetime, timedelta

import pytest

import app.db as db
import app.main as main
from app.health_score import compute_health
from app.models import AccountStatSnapshot, AccountWork, DouyinAccount, RiskEvent
from test_project_optimizations import local_project, store

NOW = datetime(2026, 11, 7, 12)


def test_perfect_account_scores_full_green(local_project):
    aid = store(DouyinAccount(platform="xhs", nickname="好号", status="active",
                              aweme_count=5,
                              last_active_at=NOW - timedelta(days=1)))
    store(AccountWork(platform="xhs", account_id=aid, item_id="w1",
                      create_time=int((NOW - timedelta(days=1)).timestamp()),
                      like_count=10, play_count=100))
    report = compute_health(aid, now=NOW)
    assert report["score"] == 100
    assert report["grade"] == "🟢"
    assert report["factors"] == []


def test_invalid_login_is_instant_red(local_project):
    aid = store(DouyinAccount(platform="douyin", nickname="失效号",
                              status="invalid", aweme_count=3))
    report = compute_health(aid, now=NOW)
    assert report["score"] <= 75
    assert report["grade"] in ("🟡", "🔴")
    assert any(f["name"] == "登录态" and f["impact"] == 25
               for f in report["factors"])


def test_stale_publish_and_never_synced_deduct_activeness(local_project):
    old = NOW - timedelta(days=20)
    aid = store(DouyinAccount(platform="douyin", nickname="停更号",
                              status="active", aweme_count=8,
                              last_active_at=NOW - timedelta(days=1)))
    store(AccountWork(platform="douyin", account_id=aid, item_id="old",
                      create_time=int(old.timestamp()), like_count=5))
    report = compute_health(aid, now=NOW)
    impacts = {f["name"]: f for f in report["factors"]}
    assert "发布频率" in impacts and impacts["发布频率"]["impact"] == 30
    assert "已经 20 天没发新内容" in impacts["发布频率"]["detail"]


def test_never_synced_with_platform_works_gets_hint(local_project):
    aid = store(DouyinAccount(platform="douyin", nickname="没同步", status="active",
                              aweme_count=6, last_active_at=NOW - timedelta(days=1)))
    report = compute_health(aid, now=NOW)
    factors = {f["name"]: f["detail"] for f in report["factors"]}
    assert "同步作品" in factors.get("发布频率", "")


def test_recent_risk_events_deduct_and_cap_at_20(local_project):
    aid = store(DouyinAccount(platform="xhs", nickname="风险号", status="active",
                              aweme_count=2,
                              last_active_at=NOW - timedelta(days=1)))
    for i in range(4):
        store(RiskEvent(account_id=aid, outcome="risk",
                        detail=f"事件{i}",
                        occurred_at=NOW - timedelta(days=1)))
    report = compute_health(aid, now=NOW)
    impact = next(f["impact"] for f in report["factors"] if f["name"] == "风控事件")
    assert impact == 20  # 4次*8=32 → 封顶20
    # 另有"平台有作品但从未同步"扣20（aweme_count=2 且无 AccountWork）
    assert report["score"] == 60


def test_zero_play_fresh_work_deducts_data_factor(local_project):
    aid = store(DouyinAccount(platform="douyin", nickname="0播", status="active",
                              aweme_count=1, last_active_at=NOW - timedelta(hours=2)))
    store(AccountWork(platform="douyin", account_id=aid, item_id="z1",
                      create_time=int((NOW - timedelta(days=1)).timestamp()),
                      play_count=0))
    report = compute_health(aid, now=NOW)
    assert any("0 播放" in f["detail"] for f in report["factors"])


def test_follower_decline_shows_in_factors(local_project):
    aid = store(DouyinAccount(platform="xhs", nickname="掉粉号", status="active",
                              last_active_at=NOW - timedelta(days=1)))
    store(AccountStatSnapshot(platform="xhs", account_id=aid, date="2026-11-05",
                              follower_count=100, aweme_count=3))
    store(AccountStatSnapshot(platform="xhs", account_id=aid, date="2026-11-06",
                              follower_count=88, aweme_count=3))
    report = compute_health(aid, now=NOW)
    assert any("粉丝下滑 12" in f["detail"] for f in report["factors"])


def test_missing_account_returns_zero_safe(local_project):
    report = compute_health(99999, now=NOW)
    assert report["score"] == 0 and report["grade"] == "🔴"
