"""Offline v2 packaging and approval checks; no rendering process or server."""
import asyncio
from datetime import datetime

import pytest
from fastapi.exceptions import HTTPException
from pydantic import ValidationError

from app.agent.providers import ToolCall, Turn
from app.agent.workflows import WorkflowStep
from app.fusion import api, daily, intro_v2
from test_agent import client, enabled, finished, install_model
from test_project_optimizations import local_project  # noqa: F401


TEXT = {"title": "新品上线", "hook": "把现场", "message": "做成故事",
        "tagline": "现场见真章", "brand": "FIXTURE"}


def intro_step(**extra):
    return {"kind": "render_intro_v2", "title": TEXT["title"],
            **{f"intro_{key}": value for key, value in TEXT.items() if key != "title"}, **extra}


@pytest.fixture
def render_capture(tmp_path, monkeypatch):
    calls, renders, receipts = [], [], {}
    root = tmp_path / "fusion"
    monkeypatch.setattr(api, "FUSION_DIR", root)
    monkeypatch.setattr(daily, "FUSION_DIR", root)

    def render(*, title, hook, message, tagline, brand, request_key=""):
        values = dict(title=title, hook=hook, message=message, tagline=tagline, brand=brand)
        calls.append({**values, "request_key": request_key})
        if request_key and request_key in receipts:
            original, result = receipts[request_key]
            if original != values:
                raise HTTPException(409, "该请求编号已用于不同文案")
            return {**result, "replayed": True}
        path = root / "intro-v2" / str(len(renders)) / "有声片头 v2 & 样例.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture-video")
        result = dict(path=str(path), width=1080, height=1920, fps=30, duration=6.0,
                      frames=180, audio=True, bytes=path.stat().st_size,
                      style="editorial-v2", replayed=False)
        renders.append(result)
        if request_key:
            receipts[request_key] = (values, result)
        return result

    monkeypatch.setattr(intro_v2, "render_intro_v2", render)
    return calls, renders, root


def test_page_api_and_asset_use_v2(render_capture):
    calls, renders, root = render_capture
    legacy = root / "intros" / "legacy.mp4"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")

    async def scenario():
        async with client() as http:
            page = await http.get("/api/fusion/ui?tab=packaging")
            assert page.status_code == 200
            assert "生成有声片头 v2" in page.text and "旧版 Blender 3D 片头" in page.text

            url = "/api/fusion/intro-v2"
            headers = {"Idempotency-Key": "page-v2-fixture"}
            first = await http.post(url, json=TEXT, headers=headers)
            assert first.status_code == 200, first.text
            result = first.json()
            assert result == renders[0]
            assert calls == [{**TEXT, "request_key": "page-v2-fixture"}]
            repeated = await http.post(url, json=TEXT, headers=headers)
            assert repeated.json() == {**result, "replayed": True}
            assert (await http.post(url, json={**TEXT, "title": "不同"}, headers=headers)).status_code == 409
            assert len(renders) == 1

            listed = await http.get("/api/fusion/assets", params={"kind": "intro_v2"})
            assert listed.status_code == 200
            assert [item["path"] for item in listed.json()["items"]] == [result["path"]]
            assert listed.json()["items"][0]["kind"] == "intro_v2"
            video = await http.get("/api/fusion/file", params={"path": result["path"]})
            assert video.content == b"fixture-video"
            assert video.headers["content-type"].startswith("video/mp4")
            outside = await http.get("/api/fusion/file", params={"path": str(root.parent / "outside.mp4")})
            assert outside.status_code == 403

            default = await http.post(url, json={})
            assert default.status_code == 200, default.text
            assert calls[-1] == {"title": "创作工作台", "hook": "把想法", "message": "做成内容",
                                 "tagline": "让每个想法落地", "brand": "CREATORHUB", "request_key": ""}

    asyncio.run(scenario())
    entries = daily._assets_in_day(datetime(2000, 1, 1), datetime(2100, 1, 1))
    assert {item["path"] for item in entries if item["kind"] == "intro_v2"} == {
        result["path"] for result in renders}
    assert {item["path"] for item in entries if item["kind"] == "intro3d"} == {str(legacy)}


@pytest.mark.parametrize(("field", "value"), [
    ("title", "字" * 17), ("hook", "字" * 13), ("message", "字" * 13),
    ("tagline", "字" * 25), ("brand", "字" * 25), ("title", ""),
    ("title", "  "), ("hook", "\n开场"), ("message", "正文\r"),
    ("tagline", "上行\u2028下行"), ("brand", "名字\x00"), ("brand", "A\u200bB"),
    ("title", 123), ("path", "G:/other.mp4"), ("aspect", "16:9"), ("fps", 24),
])
def test_api_rejects_invalid_text_and_extra_options(render_capture, field, value):
    async def scenario():
        async with client() as http:
            response = await http.post("/api/fusion/intro-v2", json={**TEXT, field: value})
            assert response.status_code == 422, response.text
            assert any(item["loc"] == ["body", field] for item in response.json()["detail"])

    asyncio.run(scenario())
    assert not render_capture[0]


@pytest.mark.parametrize("extra", [{"title": "字" * 17}, {"intro_hook": "上\n下"},
                                    {"intro_message": " "}, {"intro_brand": 42}])
def test_proposals_share_text_constraints(extra):
    with pytest.raises(ValidationError):
        WorkflowStep.model_validate(intro_step(**extra))


def test_agent_v2_draft_renders_once_after_confirmation_without_account(local_project, monkeypatch, render_capture):
    calls, renders, _ = render_capture
    enabled()
    install_model(monkeypatch, [Turn(calls=[ToolCall("v2", "propose_workflow", {
        "title": "片头草稿", "steps": [intro_step()],
    })])])

    async def scenario():
        run = await finished({"prompt": "生成有声片头 v2", "mode": "workflow", "platform": "douyin"})
        assert run["status"] == "completed" and len(run["workflows"]) == 1, run
        draft = run["workflows"][0]
        assert draft["status"] == "draft" and not draft["results"] and not calls
        step = draft["plan"]["steps"][0]
        assert step["account_id"] == 0 and step["platform"] == ""
        assert WorkflowStep.model_validate(step).payload() == TEXT

        async with client() as http:
            path = f"/api/agent/workflows/{draft['id']}/apply"
            approved = {"confirmed": True, "plan_hash": draft["plan_hash"]}
            assert (await http.post(path, json={**approved, "confirmed": False})).status_code == 422
            assert (await http.post(path, json={**approved, "plan_hash": "0" * 64})).status_code == 409
            assert not calls
            first = await http.post(path, json=approved)
            assert first.status_code == 200 and first.json()["status"] == "applied", first.text
            second = await http.post(path, json=approved)
            assert second.status_code == 200 and second.json()["results"] == first.json()["results"]
            assert calls == [{**TEXT, "request_key": f"agent-{draft['id']}-0"}]
            assert len(renders) == 1
            result = first.json()["results"][0]
            assert result["kind"] == "render_intro_v2" and result["id"] == result["path"] == renders[0]["path"]
            assert result["audio"] is True and result["style"] == "editorial-v2"

    asyncio.run(scenario())
