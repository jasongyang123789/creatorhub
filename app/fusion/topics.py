"""选题情报：把 radar 的日报/选题与本地已同步作品翻译成「今天拍什么」。

数据来源（只读）：
1. G:/ai-verify/radar/out/topics_latest.md —— LLM 提炼的 Top N 选题；
2. G:/ai-verify/radar/out/report_latest.md —— 当日热度榜（含作者、互动）；
3. CreatorHub 本地 AccountWork —— 已同步作品，用作「爆款对标」的实证。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import select

from ..db import get_session
from ..models import AccountWork
from .paths import RADAR_DIR

TOPIC_BLOCK = re.compile(r"^###\s*(\d+)[.、]\s*(.+?)\s*$", re.MULTILINE)
FIELD = re.compile(r"^-\s*(素材来源|选题角度|建议形式)\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
REPORT_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*([\d,]+)\s*\|[^|]*\|[^|]*\|\s*@?([^|]+?)\s*\|\s*([^|]+?)\s*\|", re.MULTILINE)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_topics(text: str) -> list[dict]:
    """解析 topics_latest.md：每个 ### N. 标题 后跟三条字段。"""
    matches = list(TOPIC_BLOCK.finditer(text))
    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        fields = {name: value.strip() for name, value in FIELD.findall(block)}
        items.append({
            "rank": int(match.group(1)),
            "title": match.group(2).strip(),
            "source": fields.get("素材来源", ""),
            "angle": fields.get("选题角度", ""),
            "format": fields.get("建议形式", "").replace("**", ""),
        })
    return items


def _parse_report(text: str, limit: int) -> list[dict]:
    rows = []
    for rank, heat, author, text_cell in REPORT_ROW.findall(text):
        if int(rank) > limit:
            continue
        rows.append({
            "rank": int(rank),
            "heat": int(heat.replace(",", "")),
            "author": author.strip(),
            "text": re.sub(r"\s+", " ", text_cell).strip()[:160],
        })
    return rows


def _local_hot_works(days: int, limit: int) -> list[dict]:
    """本地已同步作品里按互动排序，作为对标证据。"""
    cutoff = datetime.utcnow() - timedelta(days=max(1, min(days, 30)))
    with get_session() as session:
        works = session.exec(
            select(AccountWork)
            .where(AccountWork.fetched_at >= cutoff)
            .order_by((AccountWork.like_count + AccountWork.comment_count).desc())
            .limit(max(1, min(limit, 20)))
        ).all()
        return [{
            "platform": row.platform,
            "desc": (row.desc or "")[:120],
            "like": row.like_count or 0,
            "comment": row.comment_count or 0,
            "play": row.play_count or 0,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        } for row in works]


def list_topic_candidates(days: int = 1, limit: int = 8) -> dict:
    """返回选题候选：radar 提炼优先，报告热度与本地爆款做补充。"""
    limit = max(1, min(limit, 20))
    topics = _parse_topics(_read(RADAR_DIR / "out" / "topics_latest.md"))
    report = _parse_report(_read(RADAR_DIR / "out" / "report_latest.md"), limit * 3)
    local = _local_hot_works(days, limit)

    # 热度按标题里的关键词做一次弱匹配，命中则挂上 heat，便于排序。
    for item in topics:
        title = item["title"]
        hit = next((row for row in report
                    if any(token and token in row["text"] for token in title.split()[:3])), None)
        item["heat"] = hit["heat"] if hit else 0
        item["evidence"] = hit["author"] if hit else ""

    candidates = topics[:limit]
    gaps = []
    if not candidates:
        gaps.append("radar 暂无选题输出；请先运行 radar 日报（或检查 CREATORHUB_RADAR_DIR）")
    if not local:
        gaps.append("本地还没有已同步作品，爆款对标为空；先在 CreatorHub 同步作品")
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source": str(RADAR_DIR / "out"),
        "candidates": candidates,
        "trending": report[:limit],
        "local_hot_works": local,
        "gaps": gaps,
        "note": "选题来自 radar 的 LLM 提炼；热度与本地作品仅作对照，不代表平台实时数据。",
    }


def get_benchmark(keyword: str = "", limit: int = 10) -> dict:
    """爆款对标：在本地已同步作品里按关键词与互动筛选。"""
    keyword = (keyword or "").strip()
    limit = max(1, min(limit, 20))
    with get_session() as session:
        query = select(AccountWork)
        if keyword:
            query = query.where(AccountWork.desc.contains(keyword))
        rows = session.exec(
            query.order_by((AccountWork.like_count + AccountWork.comment_count).desc()).limit(limit)
        ).all()
        return {
            "keyword": keyword,
            "items": [{
                "platform": row.platform,
                "desc": (row.desc or "")[:160],
                "like": row.like_count or 0,
                "comment": row.comment_count or 0,
                "play": row.play_count or 0,
                "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
            } for row in rows],
            "note": "仅统计已同步到本地的作品；未同步的平台数据不在结果内。",
        }
