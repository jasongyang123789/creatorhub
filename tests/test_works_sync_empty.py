"""works/sync: a confirmed-empty account is a successful sync, not an error.

Only fetcher results that prove the platform answered with zero works become
HTTP 200 + {"empty": True}. Ambiguous results (nothing intercepted, possibly
logged out or rate limited) must keep failing so the user checks the login.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.main as main
from app.models import DouyinAccount
from test_project_optimizations import local_project, store


@pytest.fixture
def works_api(local_project, monkeypatch):
    cfg = local_project.cfg
    cfg.engine.douyin_read_mode = "browser"
    account_id = store(DouyinAccount(
        platform="douyin", nickname="empty-account", status="active",
        sec_uid="SELF_UID"))
    outcome = {"items": [], "err": "empty"}

    async def guarded_read_pair(_account_id, _kind, _label, _fetch, empty_result=None):
        return outcome["items"], outcome["err"]

    monkeypatch.setattr(main, "engine", SimpleNamespace(
        _proxy_bad=lambda _acc: False, guarded_read_pair=guarded_read_pair))
    monkeypatch.setattr(local_project.browser, "identity_for",
                        lambda _acc: SimpleNamespace(ua="UA"), raising=False)
    monkeypatch.setattr(main, "douyin_client_environment", lambda _x: {})
    monkeypatch.setattr(main, "resolve_transport", lambda *_a, **_kw: {
        "effective_mode": "browser", "configured_mode": "browser"})
    return SimpleNamespace(account_id=account_id, outcome=outcome)


@pytest.mark.parametrize("err", [
    "当前账号没有已发布的视频或图文作品",
    "TikTok 本账号主页已响应，但没有已发布作品",
])
def test_confirmed_empty_returns_success_with_reason(works_api, err):
    works_api.outcome.update(items=[], err=err)
    result = asyncio.run(main.sync_account_works(works_api.account_id))
    assert result["ok"] is True
    assert result["empty"] is True
    assert result["fetched"] == 0 and result["added"] == 0
    assert "暂无" in result["reason"]


@pytest.mark.parametrize("err", [
    "empty",
    "未拦截到作品数据(可能未登录/被风控/该用户无公开作品)",
    "logged_out:登录态失效,请重新登录",
    "",
])
def test_ambiguous_empty_still_fails(works_api, err):
    works_api.outcome.update(items=[], err=err)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(main.sync_account_works(works_api.account_id))
    assert caught.value.status_code == 400
    assert "未抓到作品" in caught.value.detail


def test_missing_uid_keeps_its_own_message(works_api):
    works_api.outcome.update(items=[], err="missing_uid:账号缺自身 uid")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(main.sync_account_works(works_api.account_id))
    assert caught.value.status_code == 400
    assert caught.value.detail == "账号缺自身 uid"


def test_frontend_shows_neutral_message_for_empty_sync():
    source = (main.Path(main.__file__).parent / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("async function syncMyWorks()")
    body = source[start:source.index("\n}\n", start)]
    assert "r.empty" in body
    assert body.index("r.empty") < body.index("同步完成:抓到")
