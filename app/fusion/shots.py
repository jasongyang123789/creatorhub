"""镜头配方库：把 video-shotcraft 的 124 张镜头卡变成 Agent 可检索的知识。

卡片字段：name / summary / use / duration / energy / intention / category / tags。
Agent 在 propose_workflow 前先检索，再决定用哪种镜头、需要什么素材。
"""
from __future__ import annotations

import json
from functools import lru_cache

from .paths import SHOT_LIBRARY


@lru_cache(maxsize=1)
def _library() -> dict:
    try:
        data = json.loads(SHOT_LIBRARY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"镜头库不可读：{SHOT_LIBRARY}（{exc}）") from exc
    cards = []
    for card in data.get("cards", []):
        cards.append({
            "name": card.get("name", ""),
            "summary": card.get("summary", ""),
            "use": card.get("use", ""),
            "duration": card.get("duration", ""),
            "energy": card.get("energy", ""),
            "intention": card.get("intention", ""),
            "category": card.get("category") or "",
            "tags": card.get("tags") or [],
            "source": card.get("source", ""),
        })
    return {"cards": cards, "categories": list((data.get("categories") or {}).keys()),
            "stats": data.get("stats", {})}


def list_shots(query: str = "", category: str = "", limit: int = 10) -> dict:
    """按语义关键词与类目检索镜头卡；空查询返回类目概览。"""
    library = _library()
    cards = library["cards"]
    query = (query or "").strip().lower()
    category = (category or "").strip().lower()
    limit = max(1, min(limit, 30))

    if not query and not category:
        counts = {}
        for card in cards:
            counts[card["category"] or "uncategorized"] = counts.get(card["category"] or "uncategorized", 0) + 1
        return {"total": len(cards), "categories": counts,
                "samples": [{"name": c["name"], "summary": c["summary"][:60], "category": c["category"]}
                            for c in cards[:limit]],
                "note": "传 query 或 category 检索具体镜头。"}

    scored = []
    for card in cards:
        if category and (card["category"] or "").lower() != category:
            continue
        haystack = " ".join([card["name"], card["summary"], card["use"], card["intention"],
                             " ".join(str(tag) for tag in card["tags"])]).lower()
        score = 0
        if query:
            for token in query.split():
                if token in haystack:
                    score += 3 if token in card["name"].lower() else 1
            if score == 0:
                continue
        scored.append((score, card))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    return {"query": query, "category": category, "total": len(scored),
            "items": [card for _, card in scored[:limit]],
            "note": "镜头卡是配方描述，不是可直接播放的素材；渲染仍走包装/讲解片管线。"}


def get_shot(name: str) -> dict:
    for card in _library()["cards"]:
        if card["name"].lower() == (name or "").strip().lower():
            return card
    raise ValueError(f"没有名为 {name} 的镜头卡")
