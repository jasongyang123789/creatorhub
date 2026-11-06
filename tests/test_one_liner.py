"""一句话任务解析：平台/日期/动作/摘要，输出提案而非直接入队。"""
from datetime import date, timedelta

from app.one_liner import parse_one_liner, render_proposal

TODAY = date(2026, 11, 11)  # 周三


def test_platforms_multi_and_aliases():
    p = parse_one_liner("发新款到抖音和小红书", today=TODAY)
    assert p["platforms"] == ["douyin", "xhs"]
    assert p["platforms_detected"] is True
    p2 = parse_one_liner("更新作品到视频号", today=TODAY)
    assert "shipinhao" in p2["platforms"]


def test_weekday_resolves_to_next_occurrence():
    # 周三说"周五" → 两天后
    p = parse_one_liner("周五晚上8点发视频", today=TODAY)
    assert p["when"] == "2026-11-13T20:00"
    assert "20:00" in p["when_label"]


def test_explicit_date_and_evening_pm():
    p = parse_one_liner("11月20日下午3点发到小红书", today=TODAY)
    assert p["when"] == "2026-11-20T15:00"
    assert p["platforms"] == ["xhs"]


def test_tomorrow_and_default_hour():
    p = parse_one_liner("明天发一条探店", today=TODAY)
    assert p["when"] == "2026-11-12T08:00"  # 无时间词 → 默认早上8点
    assert p["action"] == "publish"


def test_unparseable_date_is_none_not_guessed():
    p = parse_one_liner("发一条不痛不痒的内容", today=TODAY)
    assert p["when"] is None and p["when_label"] is None
    assert p["confidence"] < 0.8


def test_collect_and_monitor_actions():
    assert parse_one_liner("采集同行爆款到素材库", today=TODAY)["action"] == "collect"
    assert parse_one_liner("盯着评论区", today=TODAY)["action"] == "monitor"
    assert parse_one_liner("同步一下数据", today=TODAY)["action"] == "sync"


def test_summary_strips_instruction_words():
    p = parse_one_liner("周五晚上8点发新款视频到抖音和小红书", today=TODAY)
    assert "抖音" not in p["summary"]
    assert "新款视频" in p["summary"]


def test_needs_confirm_always_true_and_no_side_effects():
    p = parse_one_liner("立刻发布", today=TODAY)
    assert p["needs_confirm"] is True
    assert p["when"] is None  # "立刻"不硬猜日期


def test_render_proposal_card():
    p = parse_one_liner("周五晚上8点发新款视频到抖音", today=TODAY)
    card = render_proposal(p)
    assert "任务提案" in card and "抖音" in card and "20:00" in card


def test_days_ahead_never_land_in_past():
    p = parse_one_liner("周一发布", today=date(2026, 11, 9))  # 11-09 是周一
    # 当天说"周一"默认指下周一
    assert p["when"].startswith("2026-11-16")


def test_date_far_ahead_within_year():
    p = parse_one_liner("12月1日发年终盘点", today=TODAY)
    assert p["when"] == "2026-12-01T08:00" or p["when"].startswith("2026-12-01")
    assert timedelta(days=1) < (date.fromisoformat(p["when"][:10]) - TODAY) < timedelta(days=400)
