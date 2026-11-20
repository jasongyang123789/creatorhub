"""Fixture-only independent CRM API tests; never import app.main or contact platforms."""
from __future__ import annotations

import csv
import io
import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app.fusion import crm, crm_api, customers
from app.models import (AccountActionTask, DmConversation, DmMessage, DouyinAccount,
                        FusionCustomer, FusionCustomerIdentity, LiveLead, CommentRecord)

BASE = "/api/fusion/crm"
AS_OF = "2026-11-19T12:00:00Z"


@pytest.fixture()
def local_api(tmp_path, monkeypatch):
    def no_network(*_args, **_kwargs):
        pytest.fail("CRM tests cannot contact networks or platforms")

    monkeypatch.setattr(socket, "create_connection", no_network)
    engine = create_engine(f"sqlite:///{tmp_path / 'local-crm.db'}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(crm_api, "get_session", lambda: Session(engine))
    monkeypatch.setattr(customers, "get_session", lambda: Session(engine))
    with Session(engine) as session:
        session.add_all([
            DouyinAccount(id=1, platform="douyin", nickname="Fixture account A", cookie="NEVER-EXPOSE"),
            DouyinAccount(id=2, platform="douyin", nickname="Fixture account B"),
            DouyinAccount(id=3, platform="xhs", nickname="Fixture account C"),
            FusionCustomer(id=1, display_name="同名客户", stage="won", score=99),
            FusionCustomer(id=2, display_name="同名客户", stage="won", score=99),
            FusionCustomer(id=3, display_name="无购买客户", stage="won", score=100),
            FusionCustomerIdentity(customer_id=1, platform="douyin", uid="peer-A"),
            FusionCustomerIdentity(customer_id=2, platform="douyin", uid="peer-B"),
            DmConversation(id=11, platform="douyin", account_id=1, conv_id="same", peer_uid="peer-A", peer_nickname="同名", last_time=30),
            DmConversation(id=12, platform="douyin", account_id=2, conv_id="same", peer_uid="peer-B", peer_nickname="同名", last_time=20),
            DmConversation(id=13, platform="xhs", account_id=3, conv_id="same", peer_uid="peer-C", last_time=10),
            DmConversation(id=14, platform="douyin", account_id=1, conv_id="other", peer_uid="peer-D"),
            DmMessage(platform="douyin", account_id=1, conv_id="same", text="A only", create_time=1),
            DmMessage(platform="douyin", account_id=2, conv_id="same", text="B only", create_time=1),
            DmMessage(platform="xhs", account_id=3, conv_id="same", text="C only", create_time=1),
            DmMessage(platform="douyin", account_id=1, conv_id="other", text="D only", create_time=1),
        ])
        session.commit()
    app = FastAPI()
    app.include_router(crm_api.create_router())
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def post(client, path, body, status=201):
    if "/drafts?" in path and "scope_token" not in body:
        conversation, query = path.split("/drafts?", 1)
        detail = client.get(BASE + conversation + "?" + query)
        if detail.status_code == 200:
            body = {**body, "scope_token": detail.json()["scope_token"]}
        else:
            body = {**body, "scope_token": "0" * 64}
    response = client.post(BASE + path, json=body)
    assert response.status_code == status, response.text
    return response.json()


def sale(reference="order-1", customer_id=1, amount_cents=12345,
         occurred_at="2026-11-10T12:00:00Z", **changes):
    return {"reference": reference, "customer_id": customer_id,
            "confirm_customer_id": customer_id, "confirm": True,
            "amount_cents": amount_cents, "currency": "CNY", "occurred_at": occurred_at,
            **changes}


def as_csv(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["customer_id", "reference", "amount_cents", "occurred_at", "currency"])
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row[key] for key in writer.fieldnames})
    return buffer.getvalue()


def test_router_is_independent_and_scoped_inbox_never_exposes_credentials(local_api):
    client, _ = local_api
    assert client.get(BASE + "/capabilities").json()["outbound_enabled"] is False
    accounts = client.get(BASE + "/inbox/accounts").json()["items"]
    assert {item["account_id"] for item in accounts} == {1, 2, 3}
    response = client.get(BASE + "/inbox", params={"platform": "douyin", "account_id": 1})
    assert {item["conversation_id"] for item in response.json()["items"]} == {11, 14}
    assert "NEVER-EXPOSE" not in response.text
    response = client.get(BASE + "/inbox/11?platform=douyin&account_id=1")
    assert [row["text"] for row in response.json()["messages"]] == ["A only"]
    for query in ("platform=douyin&account_id=2", "platform=xhs&account_id=1", "platform=douyin&account_id=3"):
        assert client.get(BASE + "/inbox/11?" + query).status_code == 404
    assert client.get(BASE + "/inbox/11").status_code == 422


def test_drafts_are_separate_local_state_with_scope_and_revision_guards(local_api):
    client, engine = local_api
    path = "/inbox/11/drafts?platform=douyin&account_id=1"
    data = {"text": "本地答复", "request_key": "draft-A"}
    created = post(client, path, data)
    draft = created["draft"]
    assert created["outbound_enabled"] is False
    assert post(client, path, data)["created"] is False
    post(client, path, {**data, "text": "different"}, 409)
    post(client, "/inbox/11/drafts?platform=douyin&account_id=2", data, 404)
    wrong = client.patch(BASE + f"/inbox/12/drafts/{draft['id']}?platform=douyin&account_id=2",
                         json={"expected_revision": 1, "status": "reviewed"})
    assert wrong.status_code == 404
    route = BASE + f"/inbox/11/drafts/{draft['id']}?platform=douyin&account_id=1"
    assert client.patch(route, json={"expected_revision": 1, "status": "sent"}).status_code == 422
    reviewed = client.patch(route, json={"expected_revision": 1, "status": "reviewed"}).json()
    assert reviewed["draft"]["revision"] == 2
    assert client.patch(route, json={"expected_revision": 1, "text": "stale"}).status_code == 409
    edited = client.patch(route, json={"expected_revision": 2, "text": "重新编辑"}).json()
    assert edited["draft"]["status"] == "draft"
    assert client.patch(route, json={"expected_revision": 3, "status": "discarded"}).status_code == 200
    assert client.patch(route, json={"expected_revision": 4, "status": "draft"}).status_code == 409
    assert client.post(BASE + "/inbox/11/send", json={"text": "must never send"}).status_code == 404
    with Session(engine) as session:
        assert len(session.exec(select(DmMessage)).all()) == 4
        assert session.exec(select(AccountActionTask)).all() == []
        assert len(session.exec(select(crm.CrmReplyDraft)).all()) == 1


def test_changed_peer_and_duplicate_conversation_keys_fail_closed(local_api):
    client, engine = local_api
    draft = post(client, "/inbox/11/drafts?platform=douyin&account_id=1",
                 {"text": "A draft", "request_key": "draft"})["draft"]
    with Session(engine) as session:
        row = session.get(DmConversation, 11)
        row.peer_uid = "new-person"
        session.add(row)
        session.commit()
    route = BASE + f"/inbox/11/drafts/{draft['id']}?platform=douyin&account_id=1"
    assert client.patch(route, json={"expected_revision": 1, "status": "reviewed"}).status_code == 409
    assert client.get(BASE + "/inbox/11?platform=douyin&account_id=1").json()["drafts"] == []
    with Session(engine) as session:
        session.add(DmConversation(platform="douyin", account_id=1, conv_id="same", peer_uid="collision"))
        session.commit()
    assert client.get(BASE + "/inbox/11?platform=douyin&account_id=1").status_code == 409
    post(client, "/inbox/11/drafts?platform=douyin&account_id=1", {"text": "unsafe", "request_key": "new"}, 409)


def test_sop_enrollment_dedupes_without_resurrecting_done_tasks(local_api):
    client, engine = local_api
    sop = post(client, "/sops", {"name": "首次回访", "steps": [
        {"title": "核实需求", "delay_hours": 0}, {"title": "本地准备报价", "delay_hours": 24}]})
    body = {"customer_id": 1, "start_at": "2026-11-18T12:00:00Z", "request_key": "followup-1"}
    enrolled = post(client, f"/sops/{sop['id']}/enroll", body)
    assert len(enrolled["tasks"]) == 2
    assert post(client, f"/sops/{sop['id']}/enroll", body)["created"] is False
    # Refreshing the UI generates a new request key; the same SOP/customer/start
    # still represents one cycle and must not recreate tasks.
    assert post(client, f"/sops/{sop['id']}/enroll", {**body, "request_key": "other-tab"})["created"] is False
    post(client, f"/sops/{sop['id']}/enroll", {**body, "start_at": AS_OF}, 409)
    task = enrolled["tasks"][0]
    route = BASE + f"/tasks/{task['id']}"
    assert client.patch(route, json={"expected_revision": 1, "status": "done"}).status_code == 200
    assert client.patch(route, json={"expected_revision": 1, "status": "pending"}).status_code == 409
    replay = post(client, f"/sops/{sop['id']}/enroll", body)
    assert next(row for row in replay["tasks"] if row["id"] == task["id"])["status"] == "done"
    due = client.get(BASE + "/tasks", params={"due_only": "true", "as_of": AS_OF}).json()
    assert due["total"] == 1
    history = client.get(route + "/history").json()["items"]
    assert [event["to_status"] for event in history] == ["pending", "done"]
    assert client.patch(BASE + f"/sops/{sop['id']}", json={"active": False}).status_code == 200
    post(client, f"/sops/{sop['id']}/enroll", {**body, "start_at": AS_OF, "request_key": "new-cycle"}, 409)
    # New router + sessions still see the same due tasks; no process-memory queue.
    app = FastAPI()
    app.include_router(crm_api.create_router())
    with TestClient(app) as fresh:
        assert fresh.get(BASE + "/tasks?customer_id=1").json()["total"] == 2
    with Session(engine) as session:
        assert session.exec(select(AccountActionTask)).all() == []


def test_manual_tasks_due_snooze_cancel_reopen_and_dedupe(local_api):
    client, _ = local_api
    body = {"customer_id": 1, "title": "线下回访", "due_at": "2026-11-01T00:00:00Z", "request_key": "manual-1"}
    task = post(client, "/tasks", body)["task"]
    assert post(client, "/tasks", body)["created"] is False
    post(client, "/tasks", {**body, "title": "changed"}, 409)
    route = BASE + f"/tasks/{task['id']}"
    response = client.patch(route, json={"expected_revision": 1, "due_at": "2026-12-01T00:00:00Z", "status": "in_progress"})
    assert response.status_code == 200
    assert client.get(BASE + "/tasks", params={"due_only": True, "as_of": AS_OF}).json()["total"] == 0
    assert client.patch(route, json={"expected_revision": 2, "status": "cancelled"}).status_code == 200
    assert client.patch(route, json={"expected_revision": 3, "status": "pending", "due_at": "2026-11-18T00:00:00Z"}).status_code == 200
    assert client.get(BASE + "/tasks", params={"due_only": True, "as_of": AS_OF}).json()["total"] == 1
    assert len(client.get(route + "/history").json()["items"]) == 4


@pytest.mark.parametrize("changes", [
    {"confirm": False}, {"confirm": 1}, {"confirm": "true"},
    {"confirm_customer_id": 2}, {"customer_id": "同名客户"},
    {"amount_cents": 1.5}, {"amount_cents": 1.0}, {"amount_cents": True},
    {"amount_cents": "100"}, {"amount_cents": 0}, {"amount_cents": -1},
    {"currency": "USD"}, {"occurred_at": "2026-11-10T12:00:00"},
    {"occurred_at": "2999-01-01T00:00:00Z"},
])
def test_transaction_input_requires_explicit_id_confirmation_and_integer_cny(local_api, changes):
    client, _ = local_api
    post(client, "/transactions", sale(**changes), 422)
    assert client.get(BASE + "/transactions").json()["total"] == 0


def test_transactions_dedupe_conflicts_id_scope_and_audited_void(local_api):
    client, _ = local_api
    result = post(client, "/transactions", sale())
    transaction = result["transaction"]
    assert transaction["customer_id"] == 1
    assert transaction["amount_cents"] == 12345
    assert post(client, "/transactions", sale())["created"] is False
    post(client, "/transactions", sale(amount_cents=100), 409)
    post(client, "/transactions", sale(customer_id=2), 409)
    assert client.get(BASE + "/transactions?customer_id=2").json()["total"] == 0
    route = f"/transactions/{transaction['id']}/void"
    post(client, route, {"confirm": True, "confirm_customer_id": 2, "expected_revision": 1, "reason": "wrong owner"}, 422)
    voided = post(client, route, {"confirm": True, "confirm_customer_id": 1, "expected_revision": 1, "reason": "录入错误"}, 200)
    assert voided["status"] == "void" and voided["void_reason"] == "录入错误"
    post(client, "/transactions", sale(), 409)
    assert client.get(BASE + "/rfm?customer_id=1").json()["items"][0]["frequency"] == 0


def test_csv_is_preview_then_confirm_exact_ids_and_replays_persistently(local_api):
    client, _ = local_api
    rows = [sale(reference="csv-A", amount_cents=101), sale(reference="csv-B", customer_id=2, amount_cents=202)]
    preview = post(client, "/transactions/import/preview", {"csv_text": "\ufeff" + as_csv(rows)})
    assert preview["total_cents"] == 303 and preview["ledger_written"] is False
    assert client.get(BASE + "/transactions").json()["total"] == 0
    route = f"/transactions/import/{preview['import_id']}/confirm"
    confirm = {"confirm": True, "content_hash": preview["content_hash"], "confirmed_customer_ids": [1, 2]}
    post(client, route, {**confirm, "confirmed_customer_ids": [1]}, 422)
    post(client, route, {**confirm, "content_hash": "0" * 64}, 422)
    assert post(client, route, confirm, 200)["created"] == 2
    assert post(client, route, confirm, 200)["replayed"] is True
    again = post(client, "/transactions/import/preview", {"csv_text": as_csv(rows)})
    replay = post(client, f"/transactions/import/{again['import_id']}/confirm",
                  {**confirm, "content_hash": again["content_hash"]}, 200)
    assert replay["created"] == 0 and replay["duplicates"] == 2
    assert client.get(BASE + "/transactions").json()["total"] == 2


@pytest.mark.parametrize("csv_text", [
    "nickname,reference,amount_cents,occurred_at,currency\n同名客户,a,100,2026-11-10T00:00:00Z,CNY",
    "customer_id,reference,amount_cents,occurred_at,currency\n1,a,10.50,2026-11-10T00:00:00Z,CNY",
    "customer_id,reference,amount_cents,occurred_at,currency\n1,a,100,2026-11-10T00:00:00Z,USD",
    "customer_id,reference,amount_cents,occurred_at,currency\n999,a,100,2026-11-10T00:00:00Z,CNY",
    "customer_id,reference,amount_cents,occurred_at,currency\n1,a,100,2026-11-10T00:00:00Z",
])
def test_invalid_csv_never_writes_ledger(local_api, csv_text):
    client, _ = local_api
    response = client.post(BASE + "/transactions/import/preview", json={"csv_text": csv_text})
    assert response.status_code in {404, 422}
    assert client.get(BASE + "/transactions").json()["total"] == 0


def test_csv_revalidates_identity_and_rolls_back_all_rows_on_late_conflict(local_api):
    client, engine = local_api
    preview = post(client, "/transactions/import/preview", {"csv_text": as_csv([
        sale(reference="import-first"), sale(reference="import-conflict")])})
    post(client, "/transactions", sale(reference="import-conflict", amount_cents=999))
    route = f"/transactions/import/{preview['import_id']}/confirm"
    body = {"confirm": True, "content_hash": preview["content_hash"], "confirmed_customer_ids": [1]}
    post(client, route, body, 409)
    assert [row["reference"] for row in client.get(BASE + "/transactions").json()["items"]] == ["import-conflict"]
    preview2 = post(client, "/transactions/import/preview", {"csv_text": as_csv([sale(reference="changed-identity")])})
    with Session(engine) as session:
        identity = session.exec(select(FusionCustomerIdentity).where(FusionCustomerIdentity.customer_id == 1)).first()
        identity.uid = "changed"
        session.add(identity)
        session.commit()
    post(client, f"/transactions/import/{preview2['import_id']}/confirm",
         {**body, "content_hash": preview2["content_hash"]}, 409)
    assert client.get(BASE + "/transactions").json()["total"] == 1


def test_rfm_uses_confirmed_ledger_recency_frequency_and_cents_only(local_api):
    client, _ = local_api
    post(client, "/transactions", sale(reference="old", amount_cents=88888, occurred_at="2025-01-01T00:00:00Z"))
    post(client, "/transactions", sale(reference="recent-a", amount_cents=101, occurred_at="2026-11-10T12:00:00Z"))
    post(client, "/transactions", sale(reference="recent-b", amount_cents=202, occurred_at="2026-11-18T12:00:00Z"))
    post(client, "/transactions", sale(reference="other-id", customer_id=2, amount_cents=999, occurred_at="2025-11-01T00:00:00Z"))
    result = client.get(BASE + "/rfm", params={"as_of": AS_OF, "window_days": 365}).json()
    rows = {row["customer_id"]: row for row in result["items"]}
    assert rows[1]["recency_days"] == 1
    assert rows[1]["frequency"] == 2
    assert rows[1]["monetary_cents"] == 303
    assert rows[2]["frequency"] == 0 and rows[2]["recency_days"] == 383
    assert rows[2]["segment"] == "dormant"
    assert rows[3]["recency_days"] is None and rows[3]["segment"] == "no_purchase"
    earlier = client.get(BASE + "/rfm", params={"as_of": "2026-11-11T12:00:00Z", "customer_id": 1}).json()["items"][0]
    assert earlier["frequency"] == 1 and earlier["monetary_cents"] == 101
    assert result["policy"]["source"] == "confirmed_nonvoid_paid_transactions_only"


def test_customer_search_is_display_only_no_automatic_nickname_identity_merge(local_api):
    client, engine = local_api
    assert client.get(BASE + "/customers?query=同名客户").json()["total"] == 2
    with Session(engine) as session:
        session.add_all([
            LiveLead(account_id=1, platform="douyin", user_id="lead-different", nickname="同名客户", status="won"),
            CommentRecord(platform="douyin", aweme_id="fixture", comment_id="fixture", user_nickname="同名客户", user_sec_uid="comment-different"),
        ])
        session.commit()
    customers.sync_customers()
    with Session(engine) as session:
        lead_identity = session.exec(select(FusionCustomerIdentity).where(FusionCustomerIdentity.uid == "lead-different")).one()
        comment_identity = session.exec(select(FusionCustomerIdentity).where(FusionCustomerIdentity.uid == "comment-different")).one()
        assert lead_identity.customer_id not in {1, 2}
        assert comment_identity.customer_id not in {1, 2, lead_identity.customer_id}
    # A confirmed ledger customer cannot be silently deleted by the legacy merge.
    post(client, "/transactions", sale())
    with pytest.raises(ValueError, match="CRM"):
        customers.merge_customers(2, 1)
    with Session(engine) as session:
        assert session.get(FusionCustomer, 1) is not None
        assert session.exec(select(crm.CrmTransaction)).one().customer_id == 1


def test_local_customer_creation_does_not_claim_platform_identity(local_api):
    client, _ = local_api
    card = post(client, "/customers", {"display_name": "手工客户", "note": "人工核实档案"})
    assert card["id"] > 3 and card["identities"] == []
    post(client, "/transactions", sale(reference="manual-customer", customer_id=card["id"]))
    assert client.get(BASE + f"/transactions?customer_id={card['id']}").json()["total"] == 1


def test_page_assets_are_served_by_the_independent_router(local_api):
    client, _ = local_api
    page = client.get("/crm")
    assert page.status_code == 200
    assert 'src="/crm/crm.js"' in page.text and 'href="/crm/crm.css"' in page.text
    assert "保存本地回复草稿" in page.text
    assert client.get("/crm/crm.js").status_code == 200
    assert client.get("/crm/crm.css").status_code == 200


def test_schema_is_additive_when_router_is_loaded_after_host_init(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'older-host.db'}",
                           connect_args={"check_same_thread": False})
    crm_tables = set(crm._TABLES)
    original_tables = [table for table in SQLModel.metadata.sorted_tables if table not in crm_tables]
    SQLModel.metadata.create_all(engine, tables=original_tables)
    with Session(engine) as session:
        session.add(FusionCustomer(id=1, display_name="Historical ambiguous OneID"))
        session.commit()
    monkeypatch.setattr(crm_api, "get_session", lambda: Session(engine))
    app = FastAPI()
    app.include_router(crm_api.create_router())
    with TestClient(app) as client:
        assert client.get(BASE + "/tasks").json()["items"] == []
        assert client.get(BASE + "/customers/1").json()["display_name"] == "Historical ambiguous OneID"
    from sqlalchemy import inspect
    names = set(inspect(engine).get_table_names())
    assert {table.name for table in crm._TABLES} <= names
    engine.dispose()


def test_drafts_tasks_ledger_and_import_receipts_survive_engine_restart(local_api, monkeypatch):
    client, engine = local_api
    draft = post(client, "/inbox/11/drafts?platform=douyin&account_id=1",
                 {"text": "persistent local draft", "request_key": "persistent"})["draft"]
    task = post(client, "/tasks", {"customer_id": 1, "title": "持久待办",
                "due_at": "2026-11-18T00:00:00Z", "request_key": "persistent"})["task"]
    preview = post(client, "/transactions/import/preview", {"csv_text": as_csv([sale(reference="restart")])})
    url = str(engine.url)
    engine.dispose()
    reopened = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(crm_api, "get_session", lambda: Session(reopened))
    app = FastAPI()
    app.include_router(crm_api.create_router())
    with TestClient(app) as fresh:
        assert fresh.get(BASE + "/inbox/11?platform=douyin&account_id=1").json()["drafts"][0]["id"] == draft["id"]
        assert fresh.get(BASE + "/tasks").json()["items"][0]["id"] == task["id"]
        result = post(fresh, f"/transactions/import/{preview['import_id']}/confirm", {
            "confirm": True, "content_hash": preview["content_hash"], "confirmed_customer_ids": [1]}, 200)
        assert result["created"] == 1
        assert fresh.get(BASE + "/transactions").json()["items"][0]["reference"] == "restart"
    reopened.dispose()



def test_new_draft_requires_fresh_scope_token_after_peer_changes(local_api):
    client, engine = local_api
    token = client.get(BASE + "/inbox/11?platform=douyin&account_id=1").json()["scope_token"]
    with Session(engine) as session:
        row = session.get(DmConversation, 11)
        row.peer_uid = "different-person"
        session.add(row)
        session.commit()
    post(client, "/inbox/11/drafts?platform=douyin&account_id=1", {
        "text": "stale compose buffer", "request_key": "stale", "scope_token": token}, 409)
    with Session(engine) as session:
        assert session.exec(select(crm.CrmReplyDraft)).all() == []

