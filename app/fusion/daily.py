"""日报聚合（AfterNoise 形态：手机 PWA 一屏日报）。

四块内容，全部来自本地已有数据：选题 / 对标 / 新客户线索 / 当日资产。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import select

from ..db import get_session
from ..models import DmConversation, LiveLead
from . import topics
from .paths import FUSION_DIR

FOLDERS = {"cover": "covers", "packaging": "packaging", "explainer": "explainer",
           "short": "shorts", "extract": "extract", "diagram": "diagrams",
           "brandkit": "brandkits", "preview": "preview"}


def _day_bounds(date: str) -> tuple[datetime, datetime]:
    if date:
        day = datetime.strptime(date, "%Y-%m-%d")
    else:
        day = datetime.utcnow()
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _ts(value: int | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.utcfromtimestamp(int(value))
    except (ValueError, OSError):
        return None


def _assets_in_day(start: datetime, end: datetime) -> list[dict]:
    items = []
    for key, name in FOLDERS.items():
        folder = FUSION_DIR / name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".png", ".mp4", ".html", ".svg"}:
                mtime = datetime.utcfromtimestamp(path.stat().st_mtime)
                if start <= mtime < end:
                    items.append({"kind": key,
                                  "name": path.name, "path": str(path),
                                  "bytes": path.stat().st_size})
    items.sort(key=lambda row: row["path"])
    return items


def build_daily(date: str = "") -> dict:
    """聚合当天（UTC）的选题、对标、新客户、资产。"""
    start, end = _day_bounds(date)
    try:
        topic_data = topics.list_topic_candidates(days=1, limit=6)
        candidates = topic_data.get("candidates", [])
        gaps = topic_data.get("gaps", [])
    except Exception as exc:  # noqa: BLE001
        radar_ok = False
        candidates, gaps = [], [f"radar 不可用：{exc}"]
    else:
        radar_ok = True

    benchmark_items = topics.get_benchmark("", limit=5).get("items", [])[:5]

    customers = []
    with get_session() as session:
        for conv in session.exec(select(DmConversation)).all():
            when = _ts(conv.last_time)
            if when and start <= when < end:
                customers.append({"nickname": conv.peer_nickname or "(未命名)",
                                  "platform": conv.platform, "source": "私信",
                                  "text": (conv.last_text or "")[:60],
                                  "when": when.isoformat() + "Z"})
        for lead in session.exec(select(LiveLead)).all():
            if lead.updated_at and start <= lead.updated_at < end:
                customers.append({"nickname": lead.nickname or "(未命名)",
                                  "platform": lead.platform, "source": "线索",
                                  "text": f"状态 {lead.status}",
                                  "when": lead.updated_at.isoformat() + "Z"})
    customers.sort(key=lambda row: row["when"], reverse=True)

    return {
        "date": date or datetime.utcnow().strftime("%Y-%m-%d"),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sections": {
            "topics": {"title": "今天拍什么", "radar_ok": radar_ok, "items": candidates,
                       "gaps": gaps},
            "benchmark": {"title": "今日对标", "items": benchmark_items},
            "customers": {"title": "新客户 / 线索", "items": customers[:12]},
            "assets": {"title": "今日资产", "items": _assets_in_day(start, end)[:40]},
        },
    }
