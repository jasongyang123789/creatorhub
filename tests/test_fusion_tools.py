"""融合层离线回归：选题解析、去 AI 味、镜头库、封面/包装/讲解片/架构图/品牌包/预览真实渲染、
日报聚合、OneID 客户归并、发布草稿与工作流校验、技能包、爆文转贴图、Blender 3D 片头。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pytest

import app.db as db
import app.main as main  # noqa: F401
from app.fusion import (blender3d, brandkit, cover, customers, daily, deai, diagram,
                        explainer, extract, packaging, preview, publish, shots,
                        skillpack, tucard, topics)
from app.models import (CommentRecord, DmConversation, DmMessage, DouyinAccount,
                        LiveLead, PublishTask)


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


# ── 架构图 / 品牌包 / 预览 / 日报 ──

@pytest.mark.skipif(diagram.chrome_exe() is None, reason="需要 Chrome/Edge")
def test_diagram_renders_png(tmp_path, monkeypatch):
    monkeypatch.setattr(diagram, "FUSION_DIR", tmp_path)
    result = diagram.render_diagram(
        "CreatorHub 融合层",
        [{"id": "api", "label": "fusion API", "type": "backend", "sub": "FastAPI"},
         {"id": "db", "label": "SQLite", "type": "database"},
         {"id": "ui", "label": "创作台 UI", "type": "frontend"}],
        [{"from": "ui", "to": "api", "label": "fetch"}])
    assert result["path"].endswith(".png")
    assert result["bytes"] > 3000
    assert result["nodes"] == 3 and result["edges"] == 1


@pytest.mark.skipif(brandkit.chrome_exe() is None, reason="需要 Chrome/Edge")
def test_brandkit_renders_board(tmp_path, monkeypatch):
    monkeypatch.setattr(brandkit, "FUSION_DIR", tmp_path)
    result = brandkit.render_brandkit("华辰制造", "克制 几何 工业", accent="ikb")
    assert result["board"].endswith("board.png")
    assert result["bytes"] > 5000
    assert len(result["concepts"]) == 3


@pytest.mark.skipif(not preview.smyw_available(), reason="需要 Node 与 smyw skill 仓库")
def test_preview_builds_html(tmp_path, monkeypatch):
    example = Path("G:/VerifiedRepos/agent-skills/show-me-you-want-skill/"
                   "skills/smyw/examples/task-tags/spec.json")
    spec = json.loads(example.read_text(encoding="utf-8"))
    monkeypatch.setattr(preview, "FUSION_DIR", tmp_path)
    result = preview.render_preview(spec, name="task-tags")
    assert result["path"].endswith("preview.html")
    assert result["bytes"] > 0


def test_daily_build_sections(temp_db, tmp_path, monkeypatch):
    folder = tmp_path / "covers"
    folder.mkdir()
    png = folder / "cover-demo.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    now = time.time()
    os.utime(png, (now, now))
    monkeypatch.setattr(daily, "FUSION_DIR", tmp_path)
    result = daily.build_daily()
    assert set(result["sections"]) == {"topics", "benchmark", "customers", "assets"}
    assets = result["sections"]["assets"]["items"]
    assert any(item["name"] == "cover-demo.png" and item["kind"] == "cover" for item in assets)


# ── OneID 客户 ──

def test_customers_oneid_sync_360_merge(temp_db):
    now = int(time.time())
    with db.get_session() as session:
        session.add(DmConversation(platform="douyin", account_id=1, conv_id="c1",
                                   peer_uid="u1", peer_nickname="王工", last_time=now))
        session.add(DmMessage(platform="douyin", account_id=1, conv_id="c1", msg_id="m1",
                              direction="in", text="在吗", create_time=now))
        session.add(LiveLead(platform="douyin", account_id=1, user_id="u1", nickname="王工",
                             status="new", score=80,
                             updated_at=datetime.utcfromtimestamp(now)))
        session.add(CommentRecord(platform="douyin", aweme_id="a1", comment_id="c01",
                                  user_nickname="王工", user_sec_uid="sec1",
                                  create_time=now, text="多少钱"))
        session.commit()

    result = customers.list_customers()
    assert result["total"] >= 1
    first = result["items"][0]
    assert first["display_name"] == "王工"
    assert {"id", "display_name", "platform", "stage", "score",
            "identities", "last_seen", "note"} <= set(first)

    card = customers.get_customer_360(first["id"])
    assert card["messages"] and card["leads"] and card["comments"]
    assert card["suggestions"]

    # 第二个客户（李总）→ 归并到第一个客户后身份 >=3，b 行删除
    with db.get_session() as session:
        session.add(DmConversation(platform="douyin", account_id=1, conv_id="c2",
                                   peer_uid="u2", peer_nickname="李总", last_time=now))
        session.commit()
    customers.list_customers()
    merged = customers.merge_customers(first["id"], first["id"] + 1)
    assert len(merged["identities"]) >= 3
    with pytest.raises(ValueError):
        customers.get_customer_360(first["id"] + 1)


def test_customers_update_and_identity_lookup(temp_db):
    now = int(time.time())
    with db.get_session() as session:
        session.add(DmConversation(platform="douyin", account_id=1,
                                   peer_uid="u9", peer_nickname="赵姐", last_time=now))
        session.commit()
    customers.list_customers()
    # "platform:uid" 形态的身份串可定位到同一客户
    card = customers.get_customer_360("douyin:u9")
    assert card["nickname"] == "赵姐"
    updated = customers.update_customer(card["id"], stage="following", note="周三回访")
    assert updated["stage"] == "following"
    with pytest.raises(ValueError):
        customers.update_customer(card["id"], stage="成交")
    with pytest.raises(ValueError):
        customers.get_customer_360("douyin:none")


# ── 发布草稿 ──

def test_publish_task_draft_and_guards(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "FUSION_DIR", tmp_path)
    media = tmp_path / "covers"
    media.mkdir()
    video = media / "demo.mp4"
    video.write_bytes(b"0" * 2048)
    with db.get_session() as session:
        session.add(DouyinAccount(nickname="测试号"))  # platform=douyin / status=active 默认
        session.commit()

    result = publish.create_publish_task(str(video), "douyin", 1,
                                         title="这个标题一定超过二十个字因为重复了很多遍哦",
                                         desc="正文", scheduled_at="2026-11-18T09:30:00+08:00")
    assert result["id"] and result["status"] == "draft"
    with db.get_session() as session:
        row = session.get(PublishTask, result["id"])
        assert row.status == "draft"
        assert row.media_type == "video"
        assert len(row.title) <= 20
        assert row.scheduled_at_is_utc is True
        assert row.scheduled_at == datetime(2026, 11, 18, 1, 30)  # +08:00 → UTC

    with pytest.raises(ValueError):  # FUSION_DIR 外的路径
        publish.create_publish_task(str(tmp_path.parent / "outside.mp4"), "douyin", 1)
    with pytest.raises(ValueError):  # 平台与账号不匹配
        publish.create_publish_task(str(video), "xhs", 1)
    with pytest.raises(ValueError):  # 预约时间格式
        publish.create_publish_task(str(video), "douyin", 1, scheduled_at="明天下午")


# ── 工作流：recipe 与 schedule_publish ──

def test_workflow_packaging_recipe_payload(temp_db):
    from app.agent.workflows import validate_plan
    card = shots.list_shots(query="标题", limit=1)["items"][0]
    plan = validate_plan({"title": "包装计划", "steps": [{
        "kind": "render_packaging", "lines": ["第一行"], "recipe": card["name"]}]})
    assert plan.steps[0].payload()["recipe"] == card["name"]
    with pytest.raises(ValueError):
        validate_plan({"title": "包装计划", "steps": [{
            "kind": "render_packaging", "lines": ["第一行"], "recipe": "不存在的镜头卡"}]})


def test_workflow_schedule_publish_validation():
    from app.agent.workflows import WorkflowPlan
    with pytest.raises(ValueError):  # 缺 asset_path
        WorkflowPlan.model_validate({"title": "发布计划", "steps": [{
            "kind": "schedule_publish", "platform": "douyin", "account_id": 1}]})
    with pytest.raises(ValueError):  # scheduled_at 非 ISO
        WorkflowPlan.model_validate({"title": "发布计划", "steps": [{
            "kind": "schedule_publish", "platform": "douyin", "account_id": 1,
            "asset_path": "G:/x/demo.mp4", "scheduled_at": "明天"}]})
    step = WorkflowPlan.model_validate({"title": "发布计划", "steps": [{
        "kind": "schedule_publish", "platform": "douyin", "account_id": 1,
        "asset_path": "G:/x/demo.mp4", "publish_title": "标题", "publish_desc": "正文",
        "publish_topics": "话题", "scheduled_at": "2026-11-18T09:30:00"}]}).steps[0]
    payload = step.payload()
    assert payload["asset_path"] == "G:/x/demo.mp4"
    assert payload["platform"] == "douyin" and payload["account_id"] == 1
    assert payload["title"] == "标题" and payload["desc"] == "正文"
    assert payload["topics"] == "话题"
    assert payload["scheduled_at"] == "2026-11-18T09:30:00"
    empty = WorkflowPlan.model_validate({"title": "发布计划", "steps": [{
        "kind": "schedule_publish", "platform": "douyin", "account_id": 1,
        "asset_path": "G:/x/demo.mp4"}]}).steps[0].payload()
    assert empty["scheduled_at"] is None and empty["title"] == ""


# ── 技能包（anthropics-skills 19 技能，真实 G 盘目录）──

def test_skillpack_list_and_get():
    skills = skillpack.list_skills()
    assert skills["total"] >= 1
    names = {item["name"] for item in skills["items"]}
    assert "docx" in names
    first = skills["items"][0]
    assert {"name", "description", "has_refs", "dir_kb"} <= set(first)
    assert first["dir_kb"] >= 1
    card = skillpack.get_skill("docx")
    assert card["path"].endswith("SKILL.md")
    assert card["content"].strip()
    with pytest.raises(ValueError):
        skillpack.get_skill("no-such-skill")
    with pytest.raises(ValueError):  # 非法名字（防目录穿越）
        skillpack.get_skill("../escape")


def test_agent_load_skill_prompt_tool():
    from app.agent.tools import READ_TOOLS, read_tool
    assert "load_skill_prompt" in READ_TOOLS
    result = read_tool("load_skill_prompt", {"name": "docx"})
    assert "content" in result and result["name"] == "docx"
    with pytest.raises(ValueError):
        read_tool("load_skill_prompt", {"name": "no-such-skill"})


# ── 包装 recipe（镜头卡 → 时长均值覆盖 + 不存在报 400 文案）──

def test_packaging_recipe_endpoint_logic(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.fusion.api import create_router

    calls = {}

    def fake_render(lines, style, duration, aspect, fps, index):
        calls["duration"] = duration
        return {"path": "x.mp4", "frames": 1}

    monkeypatch.setattr(packaging, "render_packaging", fake_render)

    real_get_shot = shots.get_shot

    def fake_get_shot(name):
        # 假卡验证覆盖逻辑；其余名字回落真实镜头库（真实卡名也能走通）
        if name == "已知卡":
            return {"name": name, "duration": "4-8s", "summary": "开场大字砸标题"}
        return real_get_shot(name)

    monkeypatch.setattr(shots, "get_shot", fake_get_shot)
    client = TestClient(FastAPI())
    client.app.include_router(create_router())

    result = client.post("/api/fusion/packaging",
                         json={"lines": ["第一行"], "recipe": "已知卡"})
    assert result.status_code == 200
    body = result.json()
    assert body["recipe"] == "已知卡"
    assert body["recipe_note"] == "开场大字砸标题"
    assert calls["duration"] == pytest.approx(6.0)  # "4-8s" 数字均值覆盖时长

    missing = client.post("/api/fusion/packaging",
                          json={"lines": ["第一行"], "recipe": "不存在的卡"})
    assert missing.status_code == 400
    assert "镜头卡不存在" in missing.json()["detail"]

    # 真实镜头库：端点逻辑对真实卡名同样成立（时长均值 = duration 字段数字均值）
    real = shots.list_shots(query="标题", limit=1)["items"][0]
    numbers = [int(n) for n in re.findall(r"\d+", real.get("duration", ""))]
    expected = sum(numbers) / len(numbers) if numbers else 3.0
    ok = client.post("/api/fusion/packaging", json={"lines": ["第一行"], "recipe": real["name"]})
    assert ok.status_code == 200
    if numbers:
        assert calls["duration"] == pytest.approx(expected)
    assert ok.json()["recipe"] == real["name"]


def test_packaging_hero_renders_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(packaging, "FUSION_DIR", tmp_path)
    result = packaging.render_packaging(["不要工资的员工", "一个月省 4 万"],
                                        style="hero", duration=1.0, aspect="1:1", fps=30)
    assert result["frames"] == 30
    assert result["bytes"] > 3000


# ── 爆文转贴图（真实渲染，产物落临时 FUSION_DIR）──

@pytest.mark.skipif(cover.chrome_exe() is None, reason="需要 Chrome/Edge")
def test_tucard_renders_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(tucard, "FUSION_DIR", tmp_path)
    long_para = "这条报价单只写三行：设备、交期、售后。" * 10  # >120 字，验证截断
    result = tucard.render_cards(
        "一张报价单怎么谈",
        ["华辰制造要 8 万的巡检设备，30 天交货，王工说周四给答复。", long_para],
        style="huashu", size="1080x1080")
    assert result["count"] == 3  # 封面卡 + 两张正文卡
    assert all(path.endswith(".png") for path in result["cards"])
    assert result["bytes"] > 9000
    for path in result["cards"][1:]:
        assert Path(path).is_file()
    with pytest.raises(ValueError):  # 标题超 60 字
        tucard.render_cards("标" * 61, ["第一段足够长的话"])
    with pytest.raises(ValueError):  # 段落 <5 字
        tucard.render_cards("标题", ["短"])
    with pytest.raises(ValueError):  # 段落 >200 字
        tucard.render_cards("标题", ["段" * 201])
    with pytest.raises(ValueError):  # 段落 >10 段
        tucard.render_cards("标题", ["第一段足够长的话"] * 11)
    with pytest.raises(ValueError):  # 风格不支持
        tucard.render_cards("标题", ["第一段足够长的话"], style="kinetic")


# ── Blender 3D 片头（探测式：无 Blender 报错文案 + 脚本可编译）──

def test_blender3d_missing_blender_message(monkeypatch):
    monkeypatch.setattr(blender3d, "blender_exe", lambda: None)
    with pytest.raises(RuntimeError) as exc:
        blender3d.render_intro("不要工资的员工", seconds=1.0)
    assert "Blender" in str(exc.value)
    assert "无需改代码" in str(exc.value)


def test_blender3d_script_template_compiles():
    for aspect, size in (("9:16", (1080, 1920)), ("16:9", (1920, 1080))):
        src = blender3d.build_blender_script("报价单", size[0], size[1], 24, 2.0)
        compile(src, "intro.py", "exec")  # py_compile 同等校验
        assert "报价单" in src and "TRACK_TO" in src and "EEVEE" in src
    with pytest.raises(ValueError):  # 非法画幅在渲染前就被拦下
        blender3d.render_intro("标题", aspect="1:1")
