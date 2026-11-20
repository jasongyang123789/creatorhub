"""Independent local CRM router.

Mount in the host (after importing models, before/after init_db is fine)::

    from .fusion.crm_api import create_router as create_crm_router
    app.include_router(create_crm_router())

GET /crm serves the dedicated page and its own scripts. API: /api/fusion/crm.
No changes to fusion/api.py, main.py or agent workflows are required here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from sqlmodel import Session, select

from ..db import get_session
from . import crm

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Key = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Platform = Annotated[str, Query(min_length=1, max_length=40)]
AccountId = Annotated[int, Query(ge=1)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
CustomerId = Annotated[int, Field(ge=1)]
TaskStatus = Literal["pending", "in_progress", "done", "cancelled"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("confirm", mode="before", check_fields=False)
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("confirm 必须为显式 JSON true")
        return value


class CustomerIn(Input):
    display_name: Title
    note: str = Field(default="", max_length=2000)


class DraftIn(Input):
    text: Text
    request_key: Key
    scope_token: str = Field(pattern=r"^[a-f0-9]{64}$")


class DraftPatch(Input):
    expected_revision: int = Field(ge=1)
    text: Text | None = None
    status: Literal["draft", "reviewed", "discarded"] | None = None

    @model_validator(mode="after")
    def changed(self):
        if self.text is None and self.status is None:
            raise ValueError("需要 text 或 status")
        return self


class SopStep(Input):
    title: Title
    delay_hours: int = Field(ge=0, le=87600)


class SopIn(Input):
    name: Title
    steps: list[SopStep] = Field(min_length=1, max_length=20)


class SopPatch(Input):
    active: bool


class EnrollmentIn(Input):
    customer_id: CustomerId
    start_at: str = Field(min_length=10, max_length=50)
    request_key: Key


class TaskIn(Input):
    customer_id: CustomerId
    title: Title
    due_at: str = Field(min_length=10, max_length=50)
    note: str = Field(default="", max_length=2000)
    request_key: Key


class TaskPatch(Input):
    expected_revision: int = Field(ge=1)
    status: TaskStatus | None = None
    due_at: str | None = Field(default=None, min_length=10, max_length=50)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def changed(self):
        if self.status is None and self.due_at is None and self.note is None:
            raise ValueError("需要状态、到期时间或备注的变更")
        return self


class TransactionIn(Input):
    customer_id: CustomerId
    confirm_customer_id: CustomerId
    confirm: Literal[True]
    reference: Key
    amount_cents: int = Field(ge=1, le=1_000_000_000_000)
    currency: Literal["CNY"]
    occurred_at: str = Field(min_length=10, max_length=50)
    note: str = Field(default="", max_length=2000)


class VoidIn(Input):
    confirm: Literal[True]
    confirm_customer_id: CustomerId
    expected_revision: int = Field(ge=1)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class CsvIn(Input):
    csv_text: str = Field(min_length=1, max_length=1_000_000)


class ImportConfirm(Input):
    confirm: Literal[True]
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed_customer_ids: list[CustomerId] = Field(min_length=1, max_length=500)


def crm_session():
    with get_session() as session:
        try:
            crm.ensure_schema(session)
            yield session
        except crm.CrmError as exc:
            session.rollback()
            raise HTTPException(exc.status, str(exc)) from exc


Db = Annotated[Session, Depends(crm_session)]


def create_router() -> APIRouter:
    router = APIRouter(tags=["local-crm"])
    api = APIRouter(prefix="/api/fusion/crm")
    web_dir = Path(__file__).resolve().parents[1] / "web"

    @router.get("/crm", include_in_schema=False)
    def crm_page():
        return FileResponse(web_dir / "crm.html", headers={"Cache-Control": "no-store"})

    @router.get("/crm/crm.js", include_in_schema=False)
    def crm_script():
        return FileResponse(web_dir / "crm.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-store"})

    @router.get("/crm/crm.css", include_in_schema=False)
    def crm_style():
        return FileResponse(web_dir / "crm.css", media_type="text/css",
                            headers={"Cache-Control": "no-store"})

    @api.get("/capabilities")
    def capabilities() -> dict:
        return {"local_only": True, "outbound_enabled": False,
                "draft_states": ["draft", "reviewed", "discarded"],
                "task_states": ["pending", "in_progress", "done", "cancelled"],
                "rfm_policy": crm.RFM_POLICY,
                "csv_columns": ["customer_id", "reference", "amount_cents", "occurred_at", "currency", "note"]}

    @api.get("/customers")
    def customers(db: Db, query: Annotated[str, Query(max_length=100)] = "",
                  limit: Limit = 100, offset: Offset = 0) -> dict:
        return crm.list_customers(db, query, limit, offset)

    @api.post("/customers", status_code=201)
    def create_customer(body: CustomerIn, db: Db) -> dict:
        return crm.create_customer(db, body.display_name, body.note)

    @api.get("/customers/{customer_id}")
    def customer(customer_id: Annotated[int, ApiPath(ge=1)], db: Db) -> dict:
        return crm.customer_card(db, crm.require_customer(db, customer_id))

    @api.get("/inbox/accounts")
    def accounts(db: Db) -> dict:
        return crm.inbox_accounts(db)

    @api.get("/inbox")
    def inbox(db: Db, platform: Annotated[str, Query(max_length=40)] = "",
              account_id: Annotated[int | None, Query(ge=1)] = None,
              query: Annotated[str, Query(max_length=100)] = "",
              limit: Limit = 50, offset: Offset = 0) -> dict:
        return crm.list_inbox(db, platform, account_id, query, limit, offset)

    @api.get("/inbox/{conversation_id}")
    def conversation(conversation_id: Annotated[int, ApiPath(ge=1)], platform: Platform,
                     account_id: AccountId, db: Db, limit: Limit = 100,
                     offset: Offset = 0) -> dict:
        return crm.get_conversation(db, conversation_id, platform, account_id, limit, offset)

    @api.post("/inbox/{conversation_id}/drafts", status_code=201)
    def draft(conversation_id: Annotated[int, ApiPath(ge=1)], platform: Platform,
              account_id: AccountId, body: DraftIn, db: Db) -> dict:
        return crm.create_draft(db, conversation_id, platform, account_id, body.text,
                                body.request_key, body.scope_token)

    @api.patch("/inbox/{conversation_id}/drafts/{draft_id}")
    def patch_draft(conversation_id: Annotated[int, ApiPath(ge=1)], draft_id: str,
                    platform: Platform, account_id: AccountId, body: DraftPatch, db: Db) -> dict:
        return crm.update_draft(db, conversation_id, draft_id, platform, account_id,
                                body.expected_revision, body.text, body.status)

    @api.get("/sops")
    def sops(db: Db) -> dict:
        rows = db.exec(select(crm.CrmSop).order_by(crm.CrmSop.created_at.desc())).all()
        return {"items": [crm.sop_card(row) for row in rows], "local_only": True}

    @api.post("/sops", status_code=201)
    def create_sop(body: SopIn, db: Db) -> dict:
        return crm.create_sop(db, body.name, [step.model_dump() for step in body.steps])

    @api.patch("/sops/{sop_id}")
    def patch_sop(sop_id: str, body: SopPatch, db: Db) -> dict:
        row = db.get(crm.CrmSop, sop_id)
        if row is None:
            raise HTTPException(404, "SOP 不存在")
        row.active = body.active
        db.add(row)
        db.commit()
        db.refresh(row)
        return crm.sop_card(row)

    @api.post("/sops/{sop_id}/enroll", status_code=201)
    def enroll(sop_id: str, body: EnrollmentIn, db: Db) -> dict:
        return crm.enroll_sop(db, sop_id, body.customer_id, body.start_at, body.request_key)

    @api.get("/tasks")
    def tasks(db: Db, customer_id: Annotated[int | None, Query(ge=1)] = None,
              status: Literal["", "pending", "in_progress", "done", "cancelled"] = "",
              due_only: bool = False, as_of: Annotated[str | None, Query(max_length=50)] = None,
              limit: Limit = 100, offset: Offset = 0) -> dict:
        return crm.list_tasks(db, customer_id, status, due_only, as_of, limit, offset)

    @api.post("/tasks", status_code=201)
    def create_task(body: TaskIn, db: Db) -> dict:
        return crm.create_task(db, **body.model_dump())

    @api.patch("/tasks/{task_id}")
    def patch_task(task_id: str, body: TaskPatch, db: Db) -> dict:
        return crm.update_task(db, task_id, **body.model_dump())

    @api.get("/tasks/{task_id}/history")
    def task_history(task_id: str, db: Db) -> dict:
        if db.get(crm.CrmTask, task_id) is None:
            raise HTTPException(404, "待办不存在")
        events = db.exec(select(crm.CrmTaskEvent).where(crm.CrmTaskEvent.task_id == task_id)
                         .order_by(crm.CrmTaskEvent.revision)).all()
        return {"items": [crm.record(row) for row in events]}

    @api.get("/transactions")
    def transactions(db: Db, customer_id: Annotated[int | None, Query(ge=1)] = None,
                     limit: Limit = 100, offset: Offset = 0) -> dict:
        return crm.list_transactions(db, customer_id, limit, offset)

    @api.post("/transactions", status_code=201)
    def transaction(body: TransactionIn, db: Db) -> dict:
        values = crm.transaction_values(body.customer_id, body.reference, body.amount_cents,
                                        body.occurred_at, body.currency, body.note)
        return crm.create_transaction(db, values, body.confirm_customer_id)

    @api.post("/transactions/{transaction_id}/void")
    def void(transaction_id: str, body: VoidIn, db: Db) -> dict:
        return crm.void_transaction(db, transaction_id, body.confirm_customer_id,
                                    body.expected_revision, body.reason)

    @api.post("/transactions/import/preview", status_code=201)
    def preview_csv(body: CsvIn, db: Db) -> dict:
        return crm.preview_import(db, body.csv_text)

    @api.post("/transactions/import/{import_id}/confirm")
    def confirm_csv(import_id: str, body: ImportConfirm, db: Db) -> dict:
        return crm.confirm_import(db, import_id, body.content_hash, body.confirmed_customer_ids)

    @api.get("/rfm")
    def rfm(db: Db, as_of: Annotated[str | None, Query(max_length=50)] = None,
            window_days: Annotated[int, Query(ge=1, le=3650)] = 365,
            customer_id: Annotated[int | None, Query(ge=1)] = None,
            limit: Limit = 100, offset: Offset = 0) -> dict:
        return crm.rfm(db, as_of, window_days, customer_id, limit, offset)

    router.include_router(api)
    return router
