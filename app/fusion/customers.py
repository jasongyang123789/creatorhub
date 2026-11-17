"""客户 360（轻量版）：把私信、直播线索、评论里的同一个人聚到一张卡上。

对齐 HiveMtk 的客户数据层（OneID / 客户 360 / 线索 / 跟进），但只读本地已有数据：
- 私信会话与消息（DmConversation / DmMessage）
- 直播线索（LiveLead，含跟进状态 new/following/won/lost）
- 评论（CommentRecord，按昵称匹配）
不新建表、不写平台。
"""
from __future__ import annotations

from datetime import datetime

from sqlmodel import select

from ..db import get_session
from ..models import CommentRecord, DmConversation, DmMessage, LiveLead


def _ts(value: int | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.utcfromtimestamp(int(value)).isoformat() + "Z"
    except (ValueError, OSError):
        return None


def list_customers(platform: str = "", query: str = "", limit: int = 20) -> dict:
    """列出可跟进的客户：私信会话 + 直播线索合并，按最近互动排序。"""
    limit = max(1, min(limit, 50))
    query = (query or "").strip()
    rows: dict[str, dict] = {}

    with get_session() as session:
        convs = session.exec(select(DmConversation)).all()
        for conv in convs:
            if platform and conv.platform != platform:
                continue
            if query and query not in (conv.peer_nickname or ""):
                continue
            key = f"{conv.platform}:{conv.peer_uid or conv.conv_id}"
            rows[key] = {
                "id": key, "platform": conv.platform, "account_id": conv.account_id,
                "nickname": conv.peer_nickname or "(未命名)",
                "last_text": (conv.last_text or "")[:80], "last_time": _ts(conv.last_time),
                "unread": conv.unread_count or 0, "stage": "私信",
                "source": "dm", "conversation_id": conv.id,
            }
        leads = session.exec(select(LiveLead)).all()
        for lead in leads:
            if platform and lead.platform != platform:
                continue
            if query and query not in (lead.nickname or ""):
                continue
            key = f"{lead.platform}:{lead.user_id or lead.id}"
            item = rows.setdefault(key, {
                "id": key, "platform": lead.platform, "account_id": lead.account_id,
                "nickname": lead.nickname or "(未命名)", "last_text": "",
                "last_time": None, "unread": 0, "stage": lead.status,
                "source": "lead", "conversation_id": None,
            })
            item["stage"] = lead.status
            item["lead_note"] = (lead.note or "")[:120]
            item["lead_score"] = lead.score
            if lead.updated_at and (not item["last_time"] or lead.updated_at.isoformat() > item["last_time"]):
                item["last_time"] = lead.updated_at.isoformat() + "Z"

    items = sorted(rows.values(), key=lambda row: row.get("last_time") or "", reverse=True)
    return {"total": len(items), "items": items[:limit],
            "note": "私信与直播线索按平台+用户 ID 合并；评论未计入列表，进详情看。"}


def get_customer_360(customer_id: str) -> dict:
    """按 `platform:uid` 取时间线：私信消息 + 线索 + 同昵称评论。"""
    try:
        platform, uid = customer_id.split(":", 1)
    except ValueError as exc:
        raise ValueError("customer_id 形如 douyin:123456") from exc

    with get_session() as session:
        conv = session.exec(select(DmConversation).where(
            DmConversation.platform == platform,
            (DmConversation.peer_uid == uid) | (DmConversation.conv_id == uid))).first()
        messages = []
        if conv:
            rows = session.exec(select(DmMessage).where(
                DmMessage.conversation_id == conv.id).order_by(DmMessage.create_time.desc()).limit(30)).all()
            messages = [{"direction": row.direction, "text": (row.text or "")[:200],
                         "time": _ts(row.create_time)} for row in rows]
        leads = session.exec(select(LiveLead).where(
            LiveLead.platform == platform,
            (LiveLead.user_id == uid) | (LiveLead.id == (int(uid) if uid.isdigit() else -1)))).all()
        nickname = conv.peer_nickname if conv else (leads[0].nickname if leads else "")
        comments = []
        if nickname:
            rows = session.exec(select(CommentRecord).where(
                CommentRecord.platform == platform,
                CommentRecord.user_nickname == nickname).order_by(
                CommentRecord.create_time.desc()).limit(20)).all()
            comments = [{"text": (row.text or "")[:160], "time": _ts(row.create_time),
                         "like": row.like_count} for row in rows]

    if not conv and not leads:
        raise ValueError("没有找到这个客户")
    return {
        "id": customer_id, "platform": platform, "nickname": nickname or "(未命名)",
        "messages": messages,
        "leads": [{"status": row.status, "score": row.score, "note": row.note,
                   "content": (row.content or "")[:160], "updated_at": row.updated_at.isoformat()}
                  for row in leads],
        "comments": comments,
        "suggestions": _suggest(messages, leads),
        "note": "只聚合本地已同步数据；未同步的私信/评论不在此列。",
    }


def _suggest(messages: list[dict], leads: list) -> list[str]:
    tips = []
    if messages and messages[0]["direction"] == "in":
        tips.append("对方最后一条是发来的，先回消息（24 小时内回复率最高）")
    if leads and any(lead.status == "new" for lead in leads):
        tips.append("线索还没跟进过：先问清行业+最想解决的一件事")
    if leads and any(lead.status == "following" for lead in leads):
        tips.append("已在跟进：今天给一个具体下一步（时间+动作），不要只问『考虑得怎么样』")
    if not messages and not leads:
        tips.append("只有评论互动：先把人引到私信，再谈需求")
    return tips or ["保持节奏：每周至少一次有价值的信息触达"]
