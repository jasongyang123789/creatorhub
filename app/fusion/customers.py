"""客户 OneID：把私信、直播线索、评论里的同一个人归并成一张客户主档。

对齐 HiveMtk 的客户数据层（OneID / 客户 360 / 线索 / 跟进）：
- 只读扫描本地三个数据源：私信会话（DmConversation）、直播线索（LiveLead）、
  评论（CommentRecord，sec_uid 非空时）
- 身份写进 FusionCustomerIdentity，按 (platform, uid) 精确归属；新身份新建主档，
  不再按昵称归并。历史主档保持原样，成交需人工核实客户 ID
- 每次同步后按身份聚合回写主档（昵称 / 最近活跃 / 阶段 / 分数）；
  merge / update 提供人工纠偏
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_
from sqlmodel import select

from ..db import get_session
from ..models import (CommentRecord, DmConversation, DmMessage, FusionCustomer,
                      FusionCustomerIdentity, LiveLead)

STAGES = ("new", "following", "won", "lost")
_STAGE_RANK = {"lost": 0, "new": 1, "following": 2, "won": 3}


def _dt(value: int | None) -> datetime | None:
    """unix 秒 → UTC datetime；非法值返回 None。"""
    if not value:
        return None
    try:
        return datetime.utcfromtimestamp(int(value))
    except (ValueError, OSError):
        return None


def _ts(value: int | None) -> str | None:
    """unix 秒 → ISO 字符串（Z 后缀），与私信/评论列表口径一致。"""
    dt = _dt(value)
    return dt.isoformat() + "Z" if dt else None


def _max_dt(*values: datetime | None) -> datetime | None:
    """取多个时间里的最大值，忽略 None。"""
    live = [value for value in values if value]
    return max(live) if live else None


def _stage_of(statuses: list[str]) -> str:
    """从线索状态推导跟进阶段：won > following > new > lost；无线索 new。"""
    if not statuses:
        return "new"
    return max(statuses, key=lambda status: _STAGE_RANK.get(status, 1))


def _scan_events(session) -> list[dict]:
    """把三个来源拉平成统一事件：platform / uid / source / name / last_seen。"""
    events: list[dict] = []
    for conv in session.exec(select(DmConversation)).all():
        events.append({"platform": conv.platform, "uid": conv.peer_uid or f"conv:{conv.id}",
                       "source": "dm", "name": conv.peer_nickname or "",
                       "last_seen": _dt(conv.last_time)})
    for lead in session.exec(select(LiveLead)).all():
        events.append({"platform": lead.platform, "uid": lead.user_id or f"lead:{lead.id}",
                       "source": "lead", "name": lead.nickname or "",
                       "last_seen": lead.updated_at})
    for row in session.exec(select(CommentRecord).where(
            CommentRecord.user_sec_uid != "")).all():
        events.append({"platform": row.platform, "uid": row.user_sec_uid,
                       "source": "comment", "name": row.user_nickname or "",
                       "last_seen": _dt(row.create_time)})
    return events


def _customer_leads(session, customer: FusionCustomer,
                    identities: list[FusionCustomerIdentity]) -> list[LiveLead]:
    """该客户的全部线索：只按平台与身份 uid 精确匹配，不以昵称推断身份。"""
    leads: dict[int, LiveLead] = {}
    for identity in identities:
        conds = [LiveLead.user_id == identity.uid]
        if identity.uid.startswith("lead:") and identity.uid[5:].isdigit():
            conds.append(LiveLead.id == int(identity.uid[5:]))
        for row in session.exec(select(LiveLead).where(
                LiveLead.platform == identity.platform, or_(*conds))).all():
            leads[row.id] = row
    return list(leads.values())


def _aggregate_customer(session, customer: FusionCustomer) -> None:
    """按身份聚合回写主档：昵称 / 最近活跃 / 阶段 / 分数。"""
    identities = session.exec(select(FusionCustomerIdentity).where(
        FusionCustomerIdentity.customer_id == customer.id).order_by(
        FusionCustomerIdentity.id)).all()
    if not identities:
        return
    name = next((row.display_name for row in identities if row.display_name), "")
    leads = _customer_leads(session, customer, identities)
    customer.display_name = name or customer.display_name
    customer.stage = _stage_of([lead.status for lead in leads])
    customer.score = max((int(lead.score or 0) for lead in leads), default=0)
    customer.last_seen = _max_dt(customer.last_seen, *(row.last_seen for row in identities))
    customer.updated_at = datetime.utcnow()
    session.add(customer)


def sync_customers() -> dict:
    """扫描私信/线索/评论 → 身份表 → 聚合回写主档。返回处理计数。"""
    touched_customers: set[int] = set()
    identity_count = 0
    with get_session() as session:
        for event in _scan_events(session):
            identity = session.exec(select(FusionCustomerIdentity).where(
                FusionCustomerIdentity.platform == event["platform"],
                FusionCustomerIdentity.uid == event["uid"])).first()
            if identity is None:
                # 只按稳定平台身份复用；昵称不是身份，不自动合并历史主档。
                customer = FusionCustomer(display_name=event["name"],
                                          platform=event["platform"], stage="new",
                                          first_seen=datetime.utcnow())
                session.add(customer)
                session.flush()
                identity = FusionCustomerIdentity(
                    customer_id=customer.id, platform=event["platform"], uid=event["uid"],
                    source=event["source"], display_name=event["name"],
                    last_seen=event["last_seen"])
            else:
                # 已有身份：只补空昵称与推近 last_seen；source 保留首次来源
                if not identity.display_name and event["name"]:
                    identity.display_name = event["name"]
                identity.last_seen = _max_dt(identity.last_seen, event["last_seen"])
            session.add(identity)
            session.flush()
            identity_count += 1
            touched_customers.add(identity.customer_id)
        for customer in session.exec(select(FusionCustomer)).all():
            _aggregate_customer(session, customer)
        session.commit()
    return {"customers": len(touched_customers), "identities": identity_count}


def _resolve_customer_id(customer_id: int | str) -> int:
    """解析 customer_id → 主档 id。

    整数或整数字符串按主档 id 直查；"platform:uid" 先触发一次同步再按身份查。
    """
    if isinstance(customer_id, int):
        return customer_id
    text = str(customer_id).strip()
    if text.isdigit():
        return int(text)
    if ":" in text:
        platform, uid = text.split(":", 1)
        sync_customers()
        with get_session() as session:
            identity = session.exec(select(FusionCustomerIdentity).where(
                FusionCustomerIdentity.platform == platform,
                FusionCustomerIdentity.uid == uid)).first()
            if identity:
                return int(identity.customer_id)
    raise ValueError("没有找到这个客户")


def list_customers(platform: str = "", query: str = "", limit: int = 20) -> dict:
    """列出客户主档：先同步本地数据源，再按最近活跃倒序（1-50 条）。"""
    limit = max(1, min(limit, 50))
    platform = (platform or "").strip()
    query = (query or "").strip()
    sync_customers()
    with get_session() as session:
        customers = session.exec(select(FusionCustomer).order_by(
            FusionCustomer.last_seen.desc())).all()
        grouped: dict[int, list[FusionCustomerIdentity]] = {}
        for row in session.exec(select(FusionCustomerIdentity).order_by(
                FusionCustomerIdentity.id)).all():
            grouped.setdefault(row.customer_id, []).append(row)
        items = []
        for customer in customers:
            rows = grouped.get(customer.id, [])
            if platform and customer.platform != platform and all(
                    row.platform != platform for row in rows):
                continue
            if query and query not in (customer.display_name or "") and all(
                    query not in (row.display_name or "") for row in rows):
                continue
            items.append({
                "id": customer.id, "display_name": customer.display_name or "(未命名)",
                "platform": customer.platform, "stage": customer.stage,
                "score": customer.score,
                "identities": [{"platform": row.platform, "uid": row.uid,
                                "source": row.source, "display_name": row.display_name}
                               for row in rows],
                "last_seen": customer.last_seen.isoformat() + "Z" if customer.last_seen else None,
                "note": customer.note,
            })
    return {"total": len(items), "items": items[:limit],
            "note": "私信/线索/评论按 (platform, uid) 归并身份，不按昵称匹配；历史 OneID 请人工核实，详情见客户 360。"}


def get_customer_360(customer_id: int | str) -> dict:
    """客户 360：主档 + 全部身份 + 私信/线索/评论时间线 + 跟进建议。

    customer_id 支持主档 id（int 或整数字符串）与 "platform:uid" 身份串。
    """
    customer_key = _resolve_customer_id(customer_id)
    with get_session() as session:
        customer = session.get(FusionCustomer, customer_key)
        if customer is None:
            raise ValueError("没有找到这个客户")
        identities = session.exec(select(FusionCustomerIdentity).where(
            FusionCustomerIdentity.customer_id == customer.id).order_by(
            FusionCustomerIdentity.id)).all()

        # 私信：dm 身份 → 会话 → 最近 30 条消息（多身份合并取最新）
        stamped: list[tuple[int, dict]] = []
        for identity in identities:
            if identity.source != "dm":
                continue
            conds = [DmConversation.peer_uid == identity.uid]
            if identity.uid.startswith("conv:"):
                suffix = identity.uid[5:]
                if suffix.isdigit():
                    conds.append(DmConversation.id == int(suffix))
                else:
                    conds.append(DmConversation.conv_id == identity.uid)
            conv = session.exec(select(DmConversation).where(
                DmConversation.platform == identity.platform, or_(*conds))).first()
            if conv is None:
                continue
            conv_key = conv.conv_id or conv.peer_uid or f"conv:{conv.id}"
            for row in session.exec(select(DmMessage).where(
                    DmMessage.platform == conv.platform,
                    DmMessage.account_id == conv.account_id,
                    DmMessage.conv_id == conv_key).order_by(
                    DmMessage.create_time.desc()).limit(30)).all():
                stamped.append((row.create_time or 0, {
                    "direction": row.direction, "text": (row.text or "")[:200],
                    "time": _ts(row.create_time)}))
        messages = [item for _, item in sorted(stamped, key=lambda pair: pair[0],
                                               reverse=True)[:30]]

        # 线索：只按稳定平台身份精确匹配
        leads = _customer_leads(session, customer, identities)

        # 评论：同平台且 sec_uid 命中身份 uid，最近 20 条；不按昵称推断
        comment_conds = []
        uid_set = {row.uid for row in identities}
        if uid_set:
            comment_conds.append(CommentRecord.user_sec_uid.in_(uid_set))
        comment_rows = []
        if comment_conds:
            comment_rows = session.exec(select(CommentRecord).where(
                CommentRecord.platform == customer.platform, or_(*comment_conds)).order_by(
                CommentRecord.create_time.desc()).limit(20)).all()

        return {
            "id": customer.id, "platform": customer.platform,
            "nickname": customer.display_name or "(未命名)",
            "stage": customer.stage, "score": customer.score,
            "messages": messages,
            "leads": [{"status": row.status, "score": row.score, "note": row.note,
                       "content": (row.content or "")[:160],
                       "updated_at": row.updated_at.isoformat()} for row in leads],
            "comments": [{"text": (row.text or "")[:160], "time": _ts(row.create_time),
                          "like": row.like_count} for row in comment_rows],
            "suggestions": _suggest(messages, leads),
            "identities": [{"platform": row.platform, "uid": row.uid, "source": row.source,
                            "display_name": row.display_name} for row in identities],
            "note": "OneID 主档聚合：只按平台身份精确匹配，不按昵称归并；历史主档保持原样，请人工核实客户 ID。",
        }


def merge_customers(a_id: int | str, b_id: int | str) -> dict:
    """把 b 客户并进 a：身份改挂、昵称/阶段/分数/最近活跃取优，删除 b 行。"""
    a_key, b_key = _resolve_customer_id(a_id), _resolve_customer_id(b_id)
    if a_key == b_key:
        raise ValueError("两个 customer_id 指向同一个客户")
    with get_session() as session:
        master = session.get(FusionCustomer, a_key)
        slave = session.get(FusionCustomer, b_key)
        if master is None or slave is None:
            raise ValueError("没有找到这个客户")
        # Legacy merge deletes the source. Never orphan or silently reattribute
        # confirmed sales / durable follow-ups; resolving these needs a separate
        # explicit transfer flow, not an automatic historical migration.
        from .crm import customer_has_crm_state
        if customer_has_crm_state(session, a_key) or customer_has_crm_state(session, b_key):
            raise ValueError("客户已有 CRM 成交或跟进任务，禁止直接归并；请保留客户 ID 并人工核对")
        for identity in session.exec(select(FusionCustomerIdentity).where(
                FusionCustomerIdentity.customer_id == slave.id)).all():
            identity.customer_id = master.id
            session.add(identity)
        master.display_name = master.display_name or slave.display_name
        master.stage = _stage_of([master.stage, slave.stage])
        master.score = max(master.score, slave.score)
        master.last_seen = _max_dt(master.last_seen, slave.last_seen)
        notes = [text for text in (master.note, slave.note) if text]
        master.note = "；".join(dict.fromkeys(notes))
        master.updated_at = datetime.utcnow()
        merged_id = master.id
        session.delete(slave)
        session.commit()
    return get_customer_360(merged_id)


def update_customer(customer_id: int | str, stage: str | None = None,
                    note: str | None = None) -> dict:
    """人工纠偏：改跟进阶段 / 备注；stage 必须是四档之一。"""
    if stage is not None and stage not in STAGES:
        raise ValueError(f"stage 只支持 {'/'.join(STAGES)}")
    customer_key = _resolve_customer_id(customer_id)
    with get_session() as session:
        customer = session.get(FusionCustomer, customer_key)
        if customer is None:
            raise ValueError("没有找到这个客户")
        if stage is not None:
            customer.stage = stage
        if note is not None:
            customer.note = note
        customer.updated_at = datetime.utcnow()
        session.add(customer)
        session.commit()
    return get_customer_360(customer_key)


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
