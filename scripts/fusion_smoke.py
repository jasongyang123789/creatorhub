# -*- coding: utf-8 -*-
"""融合层冒烟：真实打 8001 端点 + 校验三条闭环 + 落 smoke.json。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

BASE = "http://127.0.0.1:8001"
OUT = ROOT / "docs" / "evidence" / "smoke.json"
out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": []}


def rec(name, ok, detail):
    out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + " :: " + str(detail)[:160])


c = httpx.Client(base_url=BASE, timeout=180)
with c:
    r = c.get("/api/fusion/status")
    rec("status", r.status_code == 200 and all(r.json()["checks"].values()), r.json()["checks"])

    r = c.post("/api/fusion/diagram", json={
        "title": "CreatorHub 融合层架构",
        "nodes": [
            {"id": "radar", "label": "radar 选题雷达", "type": "external", "sub": "G:/ai-verify/radar"},
            {"id": "api", "label": "fusion API", "type": "backend", "sub": "FastAPI"},
            {"id": "db", "label": "SQLite", "type": "database"},
            {"id": "ui", "label": "创作台 UI", "type": "frontend"},
            {"id": "bus", "label": "发布队列", "type": "bus", "sub": "PublishTask"},
            {"id": "mpt", "label": "MoneyPrinterTurbo", "type": "cloud"},
        ],
        "edges": [
            {"from": "radar", "to": "api", "label": "topics_latest.md"},
            {"from": "ui", "to": "api", "label": "fetch"},
            {"from": "api", "to": "db", "label": "SQLModel", "dashed": True},
            {"from": "api", "to": "bus", "label": "草稿"},
            {"from": "mpt", "to": "api"},
        ]})
    d = r.json()
    rec("POST /diagram", r.status_code == 200 and d["bytes"] > 3000, {"bytes": d["bytes"], "path": d["path"]})

    r = c.post("/api/fusion/brandkit", json={"name": "华辰制造", "adjectives": "可靠 精密", "accent": "ikb"})
    b = r.json()
    rec("POST /brandkit", r.status_code == 200 and b["bytes"] > 5000, {"bytes": b["bytes"], "concepts": [x["name"] for x in b["concepts"]]})

    spec = json.loads((Path(r"G:/VerifiedRepos/agent-skills/show-me-you-want-skill/skills/smyw/examples/task-tags/spec.json")).read_text(encoding="utf-8")) if Path(r"G:/VerifiedRepos/agent-skills/show-me-you-want-skill/skills/smyw/examples/task-tags/spec.json").is_file() else {"version":1,"summary":"smoke","pages":[]}
    r = c.post("/api/fusion/preview", json={"name": "冒烟", "spec": spec})
    p = r.json()
    rec("POST /preview", r.status_code == 200 and p["bytes"] > 50000, {"bytes": p["bytes"], "path": p["path"]})

    r = c.post("/api/fusion/publish-task", json={
        "asset_path": d["path"], "platform": "douyin", "account_id": 1,
        "title": "架构图草稿", "desc": "冒烟：图片类型发布草稿", "topics": "AI"})
    q = r.json()
    rec("POST /publish-task(图片)", r.status_code == 200 and q["status"] == "draft", {"id": q["id"], "media_type": q["media_type"]})

    e = c.get("/api/fusion/customers", params={"limit": 5}).json()
    rec("GET /customers", e["total"] > 0, {"total": e["total"], "first": (e["items"] or [{}])[0].get("display_name")})

    day = c.get("/api/fusion/daily").json()["sections"]
    rec("GET /daily", all(day[k] is not None for k in ("topics", "benchmark", "customers", "assets")),
        {k: len(day[k]["items"]) for k in day})

    r = c.get("/api/fusion/ui", params={"tab": "diagram"})
    rec("GET /ui?tab=diagram", r.status_code == 200 and "架构图" in r.text, "HTML 含架构图页签")
    r = c.get("/api/fusion/daily/ui")
    rec("GET /daily/ui", r.status_code == 200 and "创作日报" in r.text, "PWA 页面 200")
    r = c.get("/api/fusion/sw.js")
    rec("GET /sw.js", r.status_code == 200 and "caches" in r.text, "Service Worker 可读")
    r = c.get("/api/fusion/daily/manifest.webmanifest")
    rec("GET manifest", r.status_code == 200 and r.json().get("display") == "standalone", r.json()["name"])

    r = c.post("/api/fusion/publish-task", json={"asset_path": r"G:/outside.mp4", "platform": "douyin", "account_id": 1})
    rec("拒绝越界产物路径", r.status_code == 400, r.json()["detail"])
    r = c.post("/api/fusion/publish-task", json={"asset_path": d["path"], "platform": "douyin", "account_id": 999})
    rec("拒绝不存在账号", r.status_code == 400, r.json()["detail"])

# 三条闭环的工作流层证据
import app.db as db
db.init_db(str(ROOT / "data" / "creatorhub.db"))
from sqlmodel import select
from app.models import PublishTask
from app.agent.workflows import validate_plan

with db.get_session() as s:
    rows = s.exec(select(PublishTask).order_by(PublishTask.id.desc()).limit(3)).all()
    out["publish_rows"] = [{"id": x.id, "platform": x.platform, "status": x.status,
                            "media_type": x.media_type, "title": x.title} for x in rows]

shot_name = __import__("app.fusion.shots", fromlist=["list_shots"]).list_shots(query="标题", limit=1)["items"][0]["name"]
plan = validate_plan({"title": "闭环演示", "steps": [
    {"kind": "render_packaging", "lines": ["不要工资的员工", "一个月省 4 万"],
     "packaging_style": "kinetic", "duration": 2, "aspect": "9:16", "recipe": shot_name},
    {"kind": "schedule_publish", "asset_path": d["path"], "platform": "douyin", "account_id": 1,
     "publish_title": "架构图草稿", "publish_topics": "AI"},
]})
out["workflow_recipe_payload"] = plan.steps[0].payload()
out["workflow_publish_payload"] = plan.steps[1].payload()
print("PASS 闭环1 镜头卡 recipe ::", shot_name, "->", plan.steps[0].payload()["recipe"])
print("PASS 闭环2 schedule_publish payload ::", plan.steps[1].payload()["asset_path"][-30:])

OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("saved:", OUT, "| checks:", len(out["checks"]),
      "| all ok:", all(x["ok"] for x in out["checks"]))