"""融合层离线回归：选题解析、去 AI 味、镜头库、封面/包装/讲解片真实渲染。"""
from __future__ import annotations

import asyncio

import pytest

import app.db as db
import app.main as main  # noqa: F401
from app.fusion import cover, customers, deai, explainer, extract, packaging, shots, topics


@pytest.fixture()
def temp_db(tmp_path):
    db.init_db(str(tmp_path / "fusion-test.db"))
    yield


def test_deai_flags_ai_phrases_and_scores():
    result = deai.critique_text(
        "在当今这个时代，我们要全方位赋能商家，打造闭环链路。"
        "综上所述，首先我们要抓手，其次要颗粒度，最后要心智。"
    )
    assert result["score"] < 60
    assert result["verdict"] == "建议重写"
    kinds = {issue["type"] for issue in result["issues"]}
    assert "ai-phrase" in kinds
    assert "no-numbers" in kinds


def test_deai_human_copy_scores_higher():
    result = deai.critique_text(
        "华辰制造的王工周三下午来电话，问 8 万那套巡检设备能不能 30 天交货。"
        "我说能，但要先签合同。他回了一句：周四给你答复。"
    )
    assert result["score"] >= 60
    assert result["stats"]["numbers"] >= 2


def test_shots_library_search():
    overview = shots.list_shots()
    assert overview["total"] >= 100
    assert overview["categories"]
    found = shots.list_shots(query="标题", limit=5)
    assert found["items"]
    first = found["items"][0]
    assert {"name", "summary", "use", "duration", "category"} <= set(first)


def test_topics_parses_radar_files(tmp_path, monkeypatch, temp_db):
    out = tmp_path / "out"
    out.mkdir()
    (out / "topics_latest.md").write_text(
        "## 今日 Top2 选题\n\n"
        "### 1. AI 员工帮你回私信\n"
        "- 素材来源: @demo — 一条推文\n"
        "- 选题角度: 私信回复慢会丢单\n"
        "- 建议形式: **短视频口播** — 30 秒演示\n\n"
        "### 2. 县城老板的报价单\n"
        "- 素材来源: @demo2 — 另一条推文\n"
        "- 选题角度: 报价不透明\n"
        "- 建议形式: **小红书图文** — 清单\n",
        encoding="utf-8")
    (out / "report_latest.md").write_text(
        "# 日报\n\n| # | 热度 | 新 | 类目 | 作者 | 推文 | 互动 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | 12,345 |  |  | @demo | AI 员工帮你回私信 | 👁1 ❤1 |\n",
        encoding="utf-8")
    monkeypatch.setattr(topics, "RADAR_DIR", tmp_path)
    result = topics.list_topic_candidates(days=1, limit=5)
    assert len(result["candidates"]) == 2
    assert result["candidates"][0]["title"] == "AI 员工帮你回私信"
    assert result["candidates"][0]["format"].startswith("短视频口播")


def test_extract_rejects_private_hosts():
    with pytest.raises(ValueError):
        extract.extract_article_assets("http://127.0.0.1/secret")
    with pytest.raises(ValueError):
        extract.extract_article_assets("file:///C:/Windows/win.ini")


def test_customers_list_returns_shape(temp_db):
    result = customers.list_customers()
    assert "items" in result and "total" in result


@pytest.mark.skipif(cover.chrome_exe() is None, reason="需要 Chrome/Edge")
def test_cover_renders_png(tmp_path, monkeypatch):
    monkeypatch.setattr(cover, "FUSION_DIR", tmp_path)
    result = cover.render_cover("不要工资的员工", "县城老板 × AI 新媒体员工",
                                style="nothing", size="1080x1080")
    assert result["path"].endswith(".png")
    assert result["bytes"] > 5000


def test_packaging_renders_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(packaging, "FUSION_DIR", tmp_path)
    result = packaging.render_packaging(["不要工资的员工", "一个月省 4 万"],
                                        style="kinetic", duration=1.0, aspect="1:1", fps=30)
    assert result["frames"] == 30
    assert result["bytes"] > 3000


def test_workflow_render_cover_requires_approval_and_writes_path(temp_db, tmp_path, monkeypatch):
    from app.agent.workflows import WorkflowPlan, apply_plan, save_plan, validate_plan
    monkeypatch.setattr(cover, "FUSION_DIR", tmp_path)
    plan = validate_plan({"title": "封面计划", "steps": [{
        "kind": "render_cover", "title": "测试封面", "subtitle": "副标题",
        "cover_style": "nothing", "size": "1080x1080"}]})
    assert plan.steps[0].payload()["title"] == "测试封面"
    saved = save_plan("run-fusion-cover", plan)

    async def executor(payload, request):
        result = cover.render_cover(payload["title"], payload.get("subtitle", ""),
                                    payload["style"], payload.get("size", "1080x1080"))
        return {"id": result["path"], "status": "created", "path": result["path"]}

    applied = asyncio.run(apply_plan(saved["id"], saved["plan_hash"], {"render_cover": executor}))
    assert applied["status"] == "applied"
    assert applied["results"][0]["path"].endswith(".png")


def test_workflow_render_packaging_payload():
    from app.agent.workflows import validate_plan
    plan = validate_plan({"title": "包装计划", "steps": [{
        "kind": "render_packaging", "lines": ["第一行", "第二行"],
        "packaging_style": "data-card", "duration": 2, "aspect": "1:1"}]})
    payload = plan.steps[0].payload()
    assert payload["lines"] == ["第一行", "第二行"]
    assert payload["style"] == "data-card"


def test_workflow_render_rejects_empty_lines():
    from app.agent.workflows import validate_plan
    with pytest.raises(ValueError):
        validate_plan({"title": "空计划", "steps": [{"kind": "render_packaging", "lines": ["  "]}]})


def test_explainer_renders_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(explainer, "FUSION_DIR", tmp_path)
    result = explainer.render_explainer(
        "华辰制造要 8 万的巡检设备，30 天交货。王工说周四给答复。",
        title="一张报价单怎么谈", aspect="9:16", scene_seconds=2.0, fps=30)
    assert result["duration"] >= 2
    assert result["bytes"] > 3000
    assert result["scenes"]
