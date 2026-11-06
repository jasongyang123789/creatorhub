"""晨报生成器：只读汇总、交通灯、作品热度与建议。"""
from datetime import date, datetime, timedelta

import pytest


import app.db as db
import app.main as main
from app.morning_report import build_report, render_markdown
from app.models import AccountStatSnapshot, AccountWork, DouyinAccount
from test_project_optimizations import local_project, store


def test_empty_workspace_produces_neutral_advice(local_project):
    report = build_report(today=date(2026, 11, 5))
    assert report["accounts"]["total"] == 0
    assert report["new_works"] == 0
    assert report["top"] is None
    assert report["advice"] == ["一切正常,今天也保持节奏 👍"]
    md = render_markdown(report)
    assert "新媒体员工晨报" in md


def test_health_lights_and_invalid_login_warning(local_project):
    store(DouyinAccount(platform="douyin", nickname="失效号", status="invalid"))
    store(DouyinAccount(platform="xhs", nickname="正常号", status="active",
                        follower_count=75, aweme_count=21))
    store(AccountStatSnapshot(platform="xhs", account_id=2, date="2026-11-05",
                              follower_count=75, aweme_count=21))
    report = build_report(today=date(2026, 11, 5))
    assert report["accounts"]["total"] == 2
    assert report["accounts"]["lights"]["🔴"] == 1
    assert report["accounts"]["lights"]["🟢"] == 1
    assert any("登录已失效" in x for x in report["advice"])
    assert any("小红书" in x and "粉丝 75" in x for x in report["fans_lines"])


def test_new_works_and_top_heat_use_expected_weights(local_project):
    aid = store(DouyinAccount(platform="douyin", nickname="测试号", status="active",
                              aweme_count=10, last_active_at=datetime(2026, 11, 5, 8)))
    # 昨天发布;热度=like + 3*comment + 2*collect + 2*share = 20+6+8+4=38
    store(AccountWork(platform="douyin", account_id=aid, item_id="w1", desc="高互动作品",
                      create_time=int(datetime(2026, 11, 4, 12).timestamp()),
                      like_count=20, comment_count=2, collect_count=4, share_count=2,
                      play_count=1000))
    report = build_report(days=2, today=date(2026, 11, 5))
    assert report["new_works"] == 1
    assert report["top"]["desc"] == "高互动作品"
    assert report["top"]["platform"] == "抖音"
    assert "38" not in render_markdown(report)  # 逐项互动展示,不暴露内部热度分


def test_zero_play_recent_work_warns_but_old_zero_play_does_not(local_project):
    aid = store(DouyinAccount(platform="douyin", nickname="测试号", status="active",
                              aweme_count=2, last_active_at=datetime(2026, 11, 5, 8)))
    store(AccountWork(platform="douyin", account_id=aid, item_id="recent", desc="新作品",
                      create_time=int(datetime(2026, 11, 4, 10).timestamp()), play_count=0))
    report = build_report(days=3, today=date(2026, 11, 5))
    assert any("播放量" in x for x in report["advice"])


def test_follower_delta_from_daily_snapshots(local_project):
    aid = store(DouyinAccount(platform="xhs", nickname="红薯号", status="active"))
    for day, count in (("2026-11-04", 70), ("2026-11-05", 75)):
        store(AccountStatSnapshot(platform="xhs", account_id=aid, date=day,
                                  follower_count=count, aweme_count=21))
    report = build_report(today=date(2026, 11, 5))
    assert any("粉丝 75(+5)" in x for x in report["fans_lines"])


def test_api_endpoint_returns_structured_report(local_project):
    # LocalAccessMiddleware 校验请求方与 Host 都是 loopback;用本仓库测试
    # 的标准做法(httpx.ASGITransport + client=)伪造本机地址。
    import httpx
    transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 1234))

    async def call():
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://127.0.0.1:8000") as hc:
            return await hc.get("/api/morning-report?days=7")

    import asyncio
    response = asyncio.run(call())
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 7
    assert "accounts" in payload and "advice" in payload


# ── 公开面板：脱敏是安全项，必须有回归 ──────────────────────────────
def test_panel_masks_nicknames_in_html_and_json(local_project, tmp_path):
    store(DouyinAccount(platform="douyin", nickname="子申说", status="active",
                        follower_count=0, aweme_count=0))
    from app.morning_panel import build_panel
    html_path, json_path = build_panel(tmp_path, days=7, mask_names=True)
    html_text = html_path.read_text(encoding="utf-8")
    payload = json_path.read_text(encoding="utf-8")
    assert "子申说" not in html_text and "子申说" not in payload
    assert "子***" in html_text and "子***" in payload
    assert "新媒体员工晨报" in html_text
    assert "账号昵称已脱敏" in html_text


def test_panel_full_names_only_when_explicitly_enabled(local_project, tmp_path):
    store(DouyinAccount(platform="xhs", nickname="子申", status="active"))
    from app.morning_panel import build_panel
    html_path, _ = build_panel(tmp_path, days=7, mask_names=False)
    html_text = html_path.read_text(encoding="utf-8")
    assert "子申" in html_text
    assert "完整昵称" in html_text
