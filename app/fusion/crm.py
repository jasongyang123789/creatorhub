"""Local-only CRM: scoped reply drafts, durable follow-ups and confirmed CNY sales.

No platform adapters, message senders, workflow runners or background schedulers
are imported here. These tables are deliberately separate from DmMessage and
AccountActionTask: a reviewed local reply can never become an outbound task.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from weakref import WeakSet

from sqlalchemy import UniqueConstraint, func
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, SQLModel, Session, select

from ..models import (DmConversation, DmMessage, DouyinAccount, FusionCustomer,
                      FusionCustomerIdentity)


class CrmError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class CrmReplyDraft(SQLModel, table=True):
    __tablename__ = "fusioncrmreplydraft"
    __table_args__ = (UniqueConstraint("platform", "account_id", "conversation_id",
                                      "request_key", name="uq_crm_reply_request"),)
    id: str = Field(default_factory=new_id, primary_key=True)
    platform: str = Field(index=True)
    account_id: int = Field(index=True)
    conversation_id: int = Field(index=True)
    scope_hash: str
    request_key: str
    text: str
    status: str = "draft"  # draft | reviewed | discarded; NONE is sendable
    revision: int = 1
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class CrmSop(SQLModel, table=True):
    __tablename__ = "fusioncrmsop"
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    steps_json: str
    active: bool = True
    created_at: datetime = Field(default_factory=now_utc)


class CrmEnrollment(SQLModel, table=True):
    __tablename__ = "fusioncrmenrollment"
    __table_args__ = (UniqueConstraint("sop_id", "customer_id", "request_key",
                                      name="uq_crm_enrollment_request"),)
    id: str = Field(primary_key=True)  # stable digest of SOP/customer/start instant
    sop_id: str = Field(index=True)
    customer_id: int = Field(index=True)
    start_at: datetime
    request_key: str
    created_at: datetime = Field(default_factory=now_utc)


class CrmTask(SQLModel, table=True):
    __tablename__ = "fusioncrmtask"
    id: str = Field(primary_key=True)  # durable idempotency key, including step
    customer_id: int = Field(index=True)
    sop_id: str = Field(default="", index=True)
    enrollment_id: str = Field(default="", index=True)
    title: str
    due_at: datetime = Field(index=True)
    status: str = Field(default="pending", index=True)
    note: str = ""
    revision: int = 1
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class CrmTaskEvent(SQLModel, table=True):
    __tablename__ = "fusioncrmtaskevent"
    __table_args__ = (UniqueConstraint("task_id", "revision", name="uq_crm_task_revision"),)
    id: str = Field(default_factory=new_id, primary_key=True)
    task_id: str = Field(index=True)
    revision: int
    from_status: str
    to_status: str
    due_at: datetime
    note: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class CrmTransaction(SQLModel, table=True):
    __tablename__ = "fusioncrmtransaction"
    __table_args__ = (UniqueConstraint("reference", name="uq_crm_transaction_reference"),)
    id: str = Field(default_factory=new_id, primary_key=True)
    customer_id: int = Field(index=True)
    reference: str
    currency: str = "CNY"
    amount_cents: int
    occurred_at: datetime = Field(index=True)
    note: str = ""
    source: str = "manual"
    import_id: str = ""
    status: str = "confirmed"  # confirmed paid sale | void; never inferred from leads
    revision: int = 1
    void_reason: str = ""
    voided_at: datetime | None = None
    created_at: datetime = Field(default_factory=now_utc)


class CrmImport(SQLModel, table=True):
    __tablename__ = "fusioncrmimport"
    id: str = Field(default_factory=new_id, primary_key=True)
    content_hash: str
    rows_json: str
    customers_json: str
    status: str = "preview"
    result_json: str = "{}"
    created_at: datetime = Field(default_factory=now_utc)
    confirmed_at: datetime | None = None


_TABLES = [model.__table__ for model in (CrmReplyDraft, CrmSop, CrmEnrollment,
           CrmTask, CrmTaskEvent, CrmTransaction, CrmImport)]
_ready: WeakSet[Engine] = WeakSet()
_schema_lock = threading.Lock()


def ensure_schema(session: Session) -> None:
    """Add ONLY the new tables, also when imported after the host init_db()."""
    engine = session.get_bind()
    with _schema_lock:
        if engine not in _ready:
            SQLModel.metadata.create_all(engine, tables=_TABLES)
            _ready.add(engine)


def stamp(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def record(row: SQLModel) -> dict:
    return {key: stamp(value) if isinstance(value, datetime) else value
            for key, value in row.model_dump().items()}


def parse_time(value: str, *, past_only: bool = False) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("timezone required")
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError, AttributeError):
        raise CrmError("时间必须是带时区的 ISO 8601，例如 2026-11-19T08:00:00Z", 422)
    if past_only and dt > now_utc():
        raise CrmError("成交时间不能在未来", 422)
    return dt


def digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def require_customer(session: Session, customer_id: int) -> FusionCustomer:
    row = session.get(FusionCustomer, customer_id)
    if row is None:
        raise CrmError("客户 ID 不存在；请重新选择并核实身份", 404)
    return row


def customer_card(session: Session, row: FusionCustomer) -> dict:
    identities = session.exec(select(FusionCustomerIdentity).where(
        FusionCustomerIdentity.customer_id == row.id).order_by(FusionCustomerIdentity.id)).all()
    return {"id": row.id, "display_name": row.display_name, "platform": row.platform,
            "stage": row.stage, "note": row.note,
            "identities": [{"id": item.id, "platform": item.platform, "uid": item.uid,
                            "source": item.source} for item in identities],
            "identity_warning": "历史 OneID 可能曾按昵称归并；成交必须人工核实客户 ID。"}


def list_customers(session: Session, query: str = "", limit: int = 100,
                   offset: int = 0) -> dict:
    stmt = select(FusionCustomer)
    if query:
        stmt = stmt.where((FusionCustomer.display_name.contains(query, autoescape=True)) |
                          (FusionCustomer.id == (int(query) if query.isascii() and query.isdigit() else -1)))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(stmt.order_by(FusionCustomer.id).offset(offset).limit(limit)).all()
    return {"total": total, "items": [customer_card(session, row) for row in rows]}


def create_customer(session: Session, name: str, note: str) -> dict:
    row = FusionCustomer(display_name=name, note=note, first_seen=now_utc())
    session.add(row)
    session.commit()
    session.refresh(row)
    return customer_card(session, row)


def conversation_key(row: DmConversation) -> str:
    return row.conv_id or row.peer_uid or f"conv:{row.id}"


def scoped_conversation(session: Session, conversation_id: int, platform: str,
                        account_id: int) -> tuple[DmConversation, str]:
    row = session.get(DmConversation, conversation_id)
    owner = session.get(DouyinAccount, account_id)
    if (row is None or owner is None or row.platform != platform or
            row.account_id != account_id or owner.platform != platform):
        raise CrmError("这个平台/账号下没有该会话", 404)
    key = conversation_key(row)
    # Duplicate upstream keys cannot safely separate messages, even with distinct peers.
    candidates = session.exec(select(DmConversation).where(
        DmConversation.platform == platform, DmConversation.account_id == account_id)).all()
    if sum(conversation_key(item) == key for item in candidates) != 1:
        raise CrmError("本地会话标识重复，无法安全区分消息；请先人工核对", 409)
    scope_hash = digest(platform, account_id, conversation_id, key, row.peer_uid,
                        row.peer_sec_uid, owner.uid, owner.sec_uid)
    return row, scope_hash


def inbox_accounts(session: Session) -> dict:
    counts = session.exec(select(DmConversation.platform, DmConversation.account_id,
        func.count()).group_by(DmConversation.platform, DmConversation.account_id)).all()
    items = []
    for platform, account_id, count in counts:
        owner = session.get(DouyinAccount, account_id)
        if owner is not None and owner.platform == platform:
            items.append({"platform": platform, "account_id": account_id,
                          "nickname": owner.nickname, "conversations": count})
    return {"items": items}


def list_inbox(session: Session, platform: str = "", account_id: int | None = None,
               query: str = "", limit: int = 50, offset: int = 0) -> dict:
    stmt = select(DmConversation).join(DouyinAccount,
        (DmConversation.account_id == DouyinAccount.id) &
        (DmConversation.platform == DouyinAccount.platform))
    if platform:
        stmt = stmt.where(DmConversation.platform == platform)
    if account_id is not None:
        stmt = stmt.where(DmConversation.account_id == account_id)
    if query:
        stmt = stmt.where(DmConversation.peer_nickname.contains(query, autoescape=True) |
                          DmConversation.peer_uid.contains(query, autoescape=True))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(stmt.order_by(DmConversation.last_time.desc(), DmConversation.id.desc())
                        .offset(offset).limit(limit)).all()
    items = [{"conversation_id": row.id, "platform": row.platform,
              "account_id": row.account_id, "peer_uid": row.peer_uid,
              "peer_nickname": row.peer_nickname, "last_text": row.last_text,
              "last_time": row.last_time, "unread_count": row.unread_count} for row in rows]
    return {"items": items, "total": total, "local_only": True}


def get_conversation(session: Session, conversation_id: int, platform: str,
                     account_id: int, limit: int = 100, offset: int = 0) -> dict:
    row, scope_hash = scoped_conversation(session, conversation_id, platform, account_id)
    stmt = select(DmMessage).where(DmMessage.platform == platform,
        DmMessage.account_id == account_id, DmMessage.conv_id == conversation_key(row))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    messages = session.exec(stmt.order_by(DmMessage.create_time.desc(), DmMessage.id.desc())
                            .offset(offset).limit(limit)).all()
    drafts = session.exec(select(CrmReplyDraft).where(CrmReplyDraft.platform == platform,
        CrmReplyDraft.account_id == account_id, CrmReplyDraft.conversation_id == conversation_id,
        CrmReplyDraft.scope_hash == scope_hash).order_by(CrmReplyDraft.created_at.desc())).all()
    return {"conversation": {"conversation_id": row.id, "platform": platform,
             "account_id": account_id, "peer_uid": row.peer_uid,
             "peer_nickname": row.peer_nickname},
            "messages": [{"id": msg.id, "direction": msg.direction, "text": msg.text,
                          "msg_type": msg.msg_type, "create_time": msg.create_time}
                         for msg in reversed(messages)],
            "message_total": total, "scope_token": scope_hash,
            "drafts": [{key: value for key, value in record(draft).items()
                        if key not in {"scope_hash", "request_key"}} for draft in drafts],
            "local_only": True, "outbound_enabled": False}


def create_draft(session: Session, conversation_id: int, platform: str, account_id: int,
                 text: str, request_key: str, scope_token: str) -> dict:
    _, scope_hash = scoped_conversation(session, conversation_id, platform, account_id)
    if scope_token != scope_hash:
        raise CrmError("会话身份已变化，请重新打开会话后保存新草稿", 409)
    stmt = select(CrmReplyDraft).where(CrmReplyDraft.platform == platform,
        CrmReplyDraft.account_id == account_id, CrmReplyDraft.conversation_id == conversation_id,
        CrmReplyDraft.request_key == request_key)
    row = session.exec(stmt).first()
    if row:
        if row.scope_hash != scope_hash or row.text != text or row.status != "draft":
            raise CrmError("此草稿请求键已使用；请刷新后编辑已有草稿", 409)
        return {"draft": record(row), "created": False, "outbound_enabled": False}
    row = CrmReplyDraft(platform=platform, account_id=account_id,
        conversation_id=conversation_id, scope_hash=scope_hash,
        text=text, request_key=request_key)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise CrmError("草稿请求冲突；请刷新会话", 409)
    session.refresh(row)
    return {"draft": record(row), "created": True, "outbound_enabled": False}


def update_draft(session: Session, conversation_id: int, draft_id: str, platform: str,
                 account_id: int, expected_revision: int, text: str | None,
                 status: str | None) -> dict:
    _, scope_hash = scoped_conversation(session, conversation_id, platform, account_id)
    row = session.get(CrmReplyDraft, draft_id)
    if (row is None or row.platform != platform or row.account_id != account_id or
            row.conversation_id != conversation_id):
        raise CrmError("这个会话下没有该草稿", 404)
    if row.scope_hash != scope_hash:
        raise CrmError("会话身份已经变化，不能复用旧草稿", 409)
    if row.revision != expected_revision:
        raise CrmError("草稿已被修改，请刷新", 409)
    if row.status == "discarded":
        raise CrmError("已丢弃草稿不能恢复，请新建", 409)
    # Compare-and-set protects against another worker editing after the read.
    changes = {"revision": expected_revision + 1, "updated_at": now_utc()}
    if text is not None:
        changes["text"] = text
        changes["status"] = "draft"  # editing invalidates a previous local review
    if status is not None:
        changes["status"] = status
    from sqlalchemy import update
    result = session.execute(update(CrmReplyDraft).where(CrmReplyDraft.id == draft_id,
        CrmReplyDraft.revision == expected_revision).values(**changes))
    if result.rowcount != 1:
        session.rollback()
        raise CrmError("草稿已被修改，请刷新", 409)
    session.commit()
    session.refresh(row)
    return {"draft": record(row), "outbound_enabled": False}


def create_sop(session: Session, name: str, steps: list[dict]) -> dict:
    row = CrmSop(name=name, steps_json=json.dumps(steps, ensure_ascii=False))
    session.add(row)
    session.commit()
    session.refresh(row)
    return sop_card(row)


def sop_card(row: CrmSop) -> dict:
    data = record(row)
    data["steps"] = json.loads(data.pop("steps_json"))
    return data


def enroll_sop(session: Session, sop_id: str, customer_id: int,
               start_at: str, request_key: str) -> dict:
    require_customer(session, customer_id)
    sop = session.get(CrmSop, sop_id)
    if sop is None:
        raise CrmError("SOP 不存在", 404)
    start = parse_time(start_at)
    enrollment_id = digest(sop_id, customer_id, stamp(start))
    existing = session.exec(select(CrmEnrollment).where(
        CrmEnrollment.sop_id == sop_id, CrmEnrollment.customer_id == customer_id,
        (CrmEnrollment.id == enrollment_id) | (CrmEnrollment.request_key == request_key))).first()
    if existing:
        if existing.start_at != start:
            raise CrmError("同一 enrollment 请求键不能改变起始时间", 409)
        enrollment_id = existing.id
        tasks = session.exec(select(CrmTask).where(CrmTask.enrollment_id == enrollment_id)
                             .order_by(CrmTask.due_at, CrmTask.id)).all()
        return {"enrollment_id": enrollment_id, "created": False,
                "tasks": [record(row) for row in tasks], "local_only": True}
    if not sop.active:
        raise CrmError("该 SOP 已停用，不能添加新任务", 409)
    enrollment = CrmEnrollment(id=enrollment_id, sop_id=sop_id,
        customer_id=customer_id, start_at=start, request_key=request_key)
    tasks = []
    for index, step in enumerate(json.loads(sop.steps_json)):
        try:
            due = start + timedelta(hours=step["delay_hours"])
        except OverflowError:
            raise CrmError("SOP 到期时间超出范围", 422)
        task = CrmTask(id=digest(enrollment_id, index), customer_id=customer_id,
            sop_id=sop_id, enrollment_id=enrollment_id, title=step["title"], due_at=due)
        tasks.append(task)
        session.add(task)
        session.add(CrmTaskEvent(task_id=task.id, revision=1, from_status="",
                                to_status="pending", due_at=due, note="SOP 创建本地待办"))
    session.add(enrollment)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise CrmError("该 SOP 登记正在处理；请用相同请求键重试", 409)
    for row in tasks:
        session.refresh(row)
    return {"enrollment_id": enrollment_id, "created": True,
            "tasks": [record(row) for row in tasks], "local_only": True}


def create_task(session: Session, customer_id: int, title: str, due_at: str,
                note: str, request_key: str) -> dict:
    require_customer(session, customer_id)
    due = parse_time(due_at)
    task_id = digest("manual", customer_id, request_key)
    row = session.get(CrmTask, task_id)
    if row:
        if row.title != title or row.due_at != due or row.note != note:
            raise CrmError("同一待办请求键已使用", 409)
        return {"task": record(row), "created": False}
    row = CrmTask(id=task_id, customer_id=customer_id, title=title, due_at=due, note=note)
    session.add(row)
    session.add(CrmTaskEvent(task_id=task_id, revision=1, from_status="",
                            to_status="pending", due_at=due, note=note))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise CrmError("待办请求正在处理；请用相同请求键重试", 409)
    session.refresh(row)
    return {"task": record(row), "created": True}


def list_tasks(session: Session, customer_id: int | None, status: str,
               due_only: bool, as_of: str | None, limit: int, offset: int) -> dict:
    clock = parse_time(as_of) if as_of else now_utc()
    stmt = select(CrmTask)
    if customer_id is not None:
        require_customer(session, customer_id)
        stmt = stmt.where(CrmTask.customer_id == customer_id)
    if status:
        stmt = stmt.where(CrmTask.status == status)
    if due_only:
        stmt = stmt.where(CrmTask.due_at <= clock, CrmTask.status.in_(["pending", "in_progress"]))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(stmt.order_by(CrmTask.due_at, CrmTask.id).offset(offset).limit(limit)).all()
    items = []
    for row in rows:
        item = record(row)
        item["is_due"] = row.due_at <= clock and row.status in {"pending", "in_progress"}
        customer = session.get(FusionCustomer, row.customer_id)
        item["customer_name"] = customer.display_name if customer else "[客户已不存在]"
        items.append(item)
    return {"items": items, "total": total, "as_of": stamp(clock), "local_only": True}


def update_task(session: Session, task_id: str, expected_revision: int,
                status: str | None, due_at: str | None, note: str | None) -> dict:
    row = session.get(CrmTask, task_id)
    if row is None:
        raise CrmError("待办不存在", 404)
    if row.revision != expected_revision:
        raise CrmError("待办已被修改，请刷新", 409)
    old_status = row.status
    new_status = status or old_status
    due = parse_time(due_at) if due_at else row.due_at
    completed = (row.completed_at or now_utc()) if new_status == "done" else None
    from sqlalchemy import update
    result = session.execute(update(CrmTask).where(CrmTask.id == task_id,
        CrmTask.revision == expected_revision).values(status=new_status, due_at=due,
        note=note if note is not None else row.note, revision=expected_revision + 1,
        completed_at=completed, updated_at=now_utc()))
    if result.rowcount != 1:
        session.rollback()
        raise CrmError("待办已被修改，请刷新", 409)
    session.add(CrmTaskEvent(task_id=task_id, revision=expected_revision + 1,
        from_status=old_status, to_status=new_status, due_at=due,
        note=note if note is not None else row.note))
    session.commit()
    session.refresh(row)
    return record(row)


def transaction_values(customer_id: int, reference: str, amount_cents: int,
                       occurred_at: str, currency: str = "CNY", note: str = "") -> dict:
    if type(customer_id) is not int or customer_id < 1:
        raise CrmError("成交必须使用正整数客户 ID，不能使用昵称", 422)
    if type(amount_cents) is not int or not 1 <= amount_cents <= 1_000_000_000_000:
        raise CrmError("amount_cents 必须是 1 到 1000000000000 的整数分，不能使用小数元", 422)
    reference = reference.strip()
    if not reference or len(reference) > 120 or currency != "CNY" or len(note) > 2000:
        raise CrmError("需要唯一成交单号、CNY 币种和有效备注", 422)
    return {"customer_id": customer_id, "reference": reference,
            "amount_cents": amount_cents, "occurred_at": stamp(parse_time(occurred_at, past_only=True)),
            "currency": currency, "note": note}


def _existing_transaction(session: Session, values: dict) -> CrmTransaction | None:
    row = session.exec(select(CrmTransaction).where(
        CrmTransaction.reference == values["reference"])).first()
    if row:
        existing = record(row)
        if any(existing[key] != value for key, value in values.items()) or row.status != "confirmed":
            raise CrmError(f"成交单号 {values['reference']} 已使用且内容不同或已作废", 409)
    return row


def _add_transaction(session: Session, values: dict, source: str, import_id: str = "") -> CrmTransaction:
    data = dict(values)
    data["occurred_at"] = parse_time(data["occurred_at"], past_only=True)
    row = CrmTransaction(**data, source=source, import_id=import_id)
    session.add(row)
    return row


def create_transaction(session: Session, values: dict, confirm_customer_id: int) -> dict:
    if values["customer_id"] != confirm_customer_id:
        raise CrmError("确认的客户 ID 与成交归属不一致", 422)
    require_customer(session, confirm_customer_id)
    row = _existing_transaction(session, values)
    if row:
        return {"transaction": record(row), "created": False}
    row = _add_transaction(session, values, "manual")
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise CrmError("成交单号正在处理，请刷新或用相同内容重试", 409)
    session.refresh(row)
    return {"transaction": record(row), "created": True}


def void_transaction(session: Session, transaction_id: str, confirm_customer_id: int,
                     expected_revision: int, reason: str) -> dict:
    row = session.get(CrmTransaction, transaction_id)
    if row is None:
        raise CrmError("成交记录不存在", 404)
    if row.customer_id != confirm_customer_id:
        raise CrmError("作废确认的客户 ID 不一致", 422)
    if row.revision != expected_revision or row.status != "confirmed":
        raise CrmError("成交记录已被修改或作废，请刷新", 409)
    from sqlalchemy import update
    result = session.execute(update(CrmTransaction).where(CrmTransaction.id == transaction_id,
        CrmTransaction.revision == expected_revision, CrmTransaction.status == "confirmed").values(
        status="void", revision=expected_revision + 1, void_reason=reason, voided_at=now_utc()))
    if result.rowcount != 1:
        session.rollback()
        raise CrmError("成交记录已被修改，请刷新", 409)
    session.commit()
    session.refresh(row)
    return record(row)


def list_transactions(session: Session, customer_id: int | None, limit: int, offset: int) -> dict:
    stmt = select(CrmTransaction)
    if customer_id is not None:
        require_customer(session, customer_id)
        stmt = stmt.where(CrmTransaction.customer_id == customer_id)
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(stmt.order_by(CrmTransaction.occurred_at.desc(), CrmTransaction.id)
                        .offset(offset).limit(limit)).all()
    return {"items": [record(row) for row in rows], "total": total, "currency": "CNY"}


_CSV_REQUIRED = {"customer_id", "reference", "amount_cents", "occurred_at", "currency"}


def preview_import(session: Session, csv_text: str) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")), strict=True)
    try:
        fields = reader.fieldnames or []
        if (len(fields) != len(set(fields)) or not _CSV_REQUIRED.issubset(fields) or
                set(fields) - (_CSV_REQUIRED | {"note"})):
            raise CrmError("CSV 列必须为 customer_id,reference,amount_cents,occurred_at,currency（可加 note）；禁止昵称映射", 422)
        rows, references, cards = [], set(), {}
        for number, raw in enumerate(reader, start=2):
            if len(rows) >= 500 or None in raw or any(value is None for value in raw.values()):
                raise CrmError(f"CSV 第 {number} 行列数错误或超过 500 行", 422)
            if not raw["customer_id"].isascii() or not raw["customer_id"].isdigit() or not raw["amount_cents"].isascii() or not raw["amount_cents"].isdigit():
                raise CrmError(f"CSV 第 {number} 行的客户 ID / 金额分必须是整数", 422)
            values = transaction_values(int(raw["customer_id"]), raw["reference"],
                int(raw["amount_cents"]), raw["occurred_at"], raw["currency"], raw.get("note", ""))
            if values["reference"] in references:
                raise CrmError(f"CSV 重复成交单号：{values['reference']}", 409)
            references.add(values["reference"])
            customer_id = values["customer_id"]
            cards[str(customer_id)] = customer_card(session, require_customer(session, customer_id))
            _existing_transaction(session, values)  # conflicts abort the ENTIRE preview
            rows.append(values)
    except (csv.Error, ValueError) as exc:
        if isinstance(exc, CrmError):
            raise
        raise CrmError("CSV 格式或数字无效", 422)
    if not rows:
        raise CrmError("CSV 没有成交数据", 422)
    content_hash = digest(rows, cards)
    row = CrmImport(content_hash=content_hash, rows_json=json.dumps(rows, ensure_ascii=False),
                    customers_json=json.dumps(cards, ensure_ascii=False))
    session.add(row)
    session.commit()
    return {"import_id": row.id, "content_hash": content_hash, "status": "preview",
            "rows": rows, "customers": list(cards.values()), "row_count": len(rows),
            "total_cents": sum(item["amount_cents"] for item in rows), "currency": "CNY",
            "requires_confirmation": True, "ledger_written": False}


def confirm_import(session: Session, import_id: str, content_hash: str,
                   confirmed_customer_ids: list[int]) -> dict:
    row = session.get(CrmImport, import_id)
    if row is None:
        raise CrmError("导入预览不存在", 404)
    cards = json.loads(row.customers_json)
    expected_ids = {int(key) for key in cards}
    if (row.content_hash != content_hash or set(confirmed_customer_ids) != expected_ids or
            len(confirmed_customer_ids) != len(expected_ids)):
        raise CrmError("预览摘要或明确确认的客户 ID 列表不匹配", 422)
    if row.status == "confirmed":
        return {**json.loads(row.result_json), "replayed": True}
    for key, previous in cards.items():
        if customer_card(session, require_customer(session, int(key))) != previous:
            raise CrmError("预览后客户身份或主档已变化；请重新预览并确认", 409)
    rows = json.loads(row.rows_json)
    ids, created = [], 0
    # Claim this preview with compare-and-set in the same atomic ledger transaction.
    from sqlalchemy import update
    claim = session.execute(update(CrmImport).where(CrmImport.id == import_id,
        CrmImport.status == "preview").values(status="confirming"))
    if claim.rowcount != 1:
        session.rollback()
        raise CrmError("该导入正在确认，请刷新后重试", 409)
    try:
        for values in rows:
            existing = _existing_transaction(session, values)
            if existing:
                ids.append(existing.id)
            else:
                transaction = _add_transaction(session, values, "csv", import_id)
                ids.append(transaction.id)
                created += 1
        result = {"import_id": import_id, "status": "confirmed", "created": created,
                  "duplicates": len(rows) - created, "transaction_ids": ids, "replayed": False}
        row.status = "confirmed"
        row.result_json = json.dumps(result)
        row.confirmed_at = now_utc()
        session.add(row)
        session.commit()
    except (IntegrityError, CrmError) as exc:
        session.rollback()
        if isinstance(exc, CrmError):
            raise
        raise CrmError("成交单号冲突；本次导入全部回滚，请重新预览", 409)
    return result


RFM_POLICY = {
    "currency": "CNY", "monetary_unit": "integer_cents",
    "source": "confirmed_nonvoid_paid_transactions_only",
    "recency": "days_since_latest_purchase_at_or_before_as_of",
    "frequency": "paid_transaction_count_in_trailing_window",
    "monetary": "sum_amount_cents_in_trailing_window",
    "r_days_max": [30, 60, 90, 180], "f_min": [2, 3, 5, 10],
    "m_cents_min": [10000, 100000, 500000, 1000000],
    "identity_warning": "历史 OneID 可能有昵称归并；仅按人工确认的客户 ID 记账，不按昵称匹配。",
}


def rfm(session: Session, as_of: str | None, window_days: int,
        customer_id: int | None = None, limit: int = 100, offset: int = 0) -> dict:
    clock = parse_time(as_of) if as_of else now_utc()
    try:
        start = clock - timedelta(days=window_days)
    except OverflowError:
        raise CrmError("RFM 窗口超出日期范围", 422)
    stmt = select(FusionCustomer)
    if customer_id is not None:
        require_customer(session, customer_id)
        stmt = stmt.where(FusionCustomer.id == customer_id)
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    customers = session.exec(stmt.order_by(FusionCustomer.id).offset(offset).limit(limit)).all()
    ids = [row.id for row in customers]
    sales = session.exec(select(CrmTransaction).where(CrmTransaction.customer_id.in_(ids),
        CrmTransaction.status == "confirmed", CrmTransaction.occurred_at <= clock)).all() if ids else []
    grouped: dict[int, list] = {}
    for sale in sales:
        grouped.setdefault(sale.customer_id, []).append(sale)
    items = []
    for customer in customers:
        purchases = grouped.get(customer.id, [])
        latest = max((sale.occurred_at for sale in purchases), default=None)
        recency = (clock - latest).days if latest else None
        window = [sale for sale in purchases if sale.occurred_at >= start]
        frequency = len(window)
        monetary = sum(sale.amount_cents for sale in window)
        r_score = (5 - sum(recency > days for days in RFM_POLICY["r_days_max"])) if recency is not None else 0
        f_score = (1 + sum(frequency >= n for n in RFM_POLICY["f_min"])) if frequency else 0
        m_score = (1 + sum(monetary >= n for n in RFM_POLICY["m_cents_min"])) if monetary else 0
        segment = ("no_purchase" if latest is None else "dormant" if recency > 180 else
                   "at_risk" if r_score <= 2 else "champions" if min(r_score, f_score, m_score) >= 4 else
                   "loyal" if f_score >= 3 else "recent" if r_score >= 4 else "developing")
        items.append({"customer_id": customer.id, "display_name": customer.display_name,
            "recency_days": recency, "frequency": frequency, "monetary_cents": monetary,
            "last_purchase_at": stamp(latest), "r_score": r_score,
            "f_score": f_score, "m_score": m_score, "segment": segment})
    return {"items": items, "total": total, "as_of": stamp(clock), "window_start": stamp(start),
            "window_days": window_days, "policy": RFM_POLICY}


def customer_has_crm_state(session: Session, customer_id: int) -> bool:
    """Stop the legacy destructive merge from orphaning confirmed local state."""
    ensure_schema(session)
    return any(session.exec(select(model).where(model.customer_id == customer_id).limit(1)).first()
               is not None for model in (CrmTransaction, CrmTask, CrmEnrollment))
