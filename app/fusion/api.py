"""融合层 HTTP 端点：只读查询 + 显式渲染任务。

只读：选题、对标、镜头库、去 AI 味、客户 360、日报。
写文件：提取素材、封面、包装镜头、讲解片、混剪、架构图、品牌包、预览——均需显式 POST，
产物落 storage/fusion/。发布草稿（publish-task）只建 draft，不自动发布。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import (blender3d, brandkit, cover, customers, daily, deai, diagram, explainer,
               extract, mpt, packaging, preview, publish, shots, skillpack, tucard,
               topics)
from .paths import FUSION_DIR, MPT_DIR, RADAR_DIR, SHOT_LIBRARY, chrome_exe, ffmpeg_exe


class ExtractIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=8, max_length=2000)
    limit: int = Field(default=30, ge=1, le=60)
    download: bool = True


class CoverIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=60)
    subtitle: str = Field(default="", max_length=120)
    style: Literal["hallmark", "nothing", "huashu", "guizang"] = "hallmark"
    size: Literal["1080x1440", "1080x1920", "1920x1080", "1080x1080"] = "1080x1440"
    badge: str = Field(default="CREATORHUB", max_length=30)
    index: int = Field(default=1, ge=1, le=999)


class PackagingIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lines: list[str] = Field(min_length=1, max_length=6)
    style: Literal["kinetic", "data-card", "chapter", "hero"] = "kinetic"
    duration: float = Field(default=3.0, ge=1, le=15)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    fps: int = Field(default=30, ge=15, le=60)
    index: int = Field(default=1, ge=1, le=99)
    recipe: str = Field(default="", max_length=80)


class ExplainerIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=20, max_length=20000)
    title: str = Field(default="", max_length=60)
    aspect: Literal["9:16", "16:9"] = "9:16"
    scene_seconds: float = Field(default=4.0, ge=2, le=8)
    voice: str = Field(default="zh-CN-YunxiNeural", max_length=60)
    fps: int = Field(default=30, ge=15, le=60)


class ShortIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject: str = Field(min_length=1, max_length=60)
    script: str = Field(min_length=10, max_length=5000)
    materials: list[str] = Field(min_length=1, max_length=20)
    language: str = Field(default="zh-CN", max_length=20)


class DeaiIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=5, max_length=20000)


class DiagramIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=60)
    nodes: list[dict] = Field(min_length=1, max_length=14)
    edges: list[dict] = Field(default_factory=list, max_length=18)
    size: Literal["1600x1000", "1920x1080", "1080x1080"] = "1600x1000"


class BrandkitIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=30)
    adjectives: str = Field(default="", max_length=60)
    accent: Literal["blue", "ikb", "orange", "green", "red", "black"] = "blue"


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spec: dict
    name: str = Field(default="", max_length=40)


class PublishTaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    asset_path: str = Field(min_length=3, max_length=500)
    platform: str
    account_id: int = Field(ge=1)
    title: str = Field(default="", max_length=60)
    desc: str = Field(default="", max_length=1000)
    topics: str = Field(default="", max_length=100)
    scheduled_at: str | None = None


class CustomersMergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    a: int = Field(ge=1)
    b: int = Field(ge=1)


class TucardIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=60)
    paragraphs: list[str] = Field(min_length=1, max_length=10)
    style: Literal["hallmark", "nothing", "huashu", "guizang"] = "huashu"
    size: Literal["1080x1440", "1080x1920", "1920x1080", "1080x1080"] = "1080x1440"


class Intro3dIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=40)
    seconds: float = Field(default=2.0, ge=1, le=5)
    aspect: Literal["9:16", "16:9"] = "9:16"
    fps: Literal[24, 30] = 24


class IntroV2In(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(default="创作工作台", min_length=1, max_length=16)
    hook: str = Field(default="把想法", min_length=1, max_length=12)
    message: str = Field(default="做成内容", min_length=1, max_length=12)
    tagline: str = Field(default="让每个想法落地", min_length=1, max_length=24)
    brand: str = Field(default="CREATORHUB", min_length=1, max_length=24)

    @field_validator("title", "hook", "message", "tagline", "brand")
    @classmethod
    def visible_single_line(cls, value: str) -> str:
        if not value.strip() or not value.isprintable():
            raise ValueError("请填写可见的单行文字，不得含换行或控制字符")
        return value.strip()


# 创作日报 Service Worker：缓存优先 + 后台更新（同源 GET）。
SW_SOURCE = """// CreatorHub 创作日报 Service Worker：缓存优先，后台静默更新
const CACHE = "daily-cache-v1";
const PRECACHE = ["/api/fusion/daily/ui", "/api/fusion/daily"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PRECACHE))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(clients.claim());
});

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin || event.request.method !== "GET") return;
  event.respondWith(caches.open(CACHE).then(async cache => {
    const cached = await cache.match(event.request);
    const updating = fetch(event.request).then(response => {
      if (response && response.ok) cache.put(event.request, response.clone());
      return response;
    }).catch(() => cached);
    return cached || updating;
  }));
});
"""


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/fusion", tags=["fusion"])

    @router.get("/topics")
    def get_topics(days: int = Query(default=1, ge=1, le=7),
                   limit: int = Query(default=8, ge=1, le=20)):
        return topics.list_topic_candidates(days=days, limit=limit)

    @router.get("/benchmark")
    def get_benchmark(keyword: str = Query(default="", max_length=40),
                      limit: int = Query(default=10, ge=1, le=20)):
        return topics.get_benchmark(keyword=keyword, limit=limit)

    @router.post("/extract")
    def post_extract(body: ExtractIn):
        return extract.extract_article_assets(body.url, limit=body.limit, download=body.download)

    @router.post("/cover")
    def post_cover(body: CoverIn):
        return cover.render_cover(body.title, body.subtitle, body.style, body.size,
                                  body.badge, body.index)

    @router.post("/packaging")
    def post_packaging(body: PackagingIn):
        duration = body.duration
        recipe = body.recipe.strip()
        recipe_note = ""
        if recipe:
            try:
                card = shots.get_shot(recipe)
            except ValueError:
                raise HTTPException(400, f"镜头卡不存在：{recipe}（先到镜头库检索）")
            numbers = re.findall(r"\d+", card.get("duration", ""))
            if numbers:
                duration = sum(int(n) for n in numbers) / len(numbers)
            recipe_note = (card.get("summary") or "")[:80]
        result = packaging.render_packaging(body.lines, body.style, duration,
                                            body.aspect, body.fps, body.index)
        result["recipe"] = recipe
        result["recipe_note"] = recipe_note
        return result

    @router.post("/explainer")
    def post_explainer(body: ExplainerIn):
        return explainer.render_explainer(body.text, body.title, body.aspect,
                                          body.scene_seconds, body.voice, body.fps)

    @router.post("/short")
    def post_short(body: ShortIn):
        return mpt.render_short(body.subject, body.script, body.materials, body.language)

    @router.post("/deai")
    def post_deai(body: DeaiIn):
        return deai.critique_text(body.text)

    @router.post("/diagram")
    def post_diagram(body: DiagramIn):
        try:
            return diagram.render_diagram(body.title, body.nodes, body.edges, body.size)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/brandkit")
    def post_brandkit(body: BrandkitIn):
        try:
            return brandkit.render_brandkit(body.name, body.adjectives, body.accent)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/preview")
    def post_preview(body: PreviewIn):
        try:
            return preview.render_preview(body.spec, body.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.get("/daily")
    def get_daily(date: str = Query(default="", max_length=10)):
        return daily.build_daily(date)

    @router.get("/daily/ui")
    def get_daily_ui():
        page = Path(__file__).resolve().parents[1] / "web" / "daily.html"
        if not page.is_file():
            raise HTTPException(404, "日报页面缺失")
        return FileResponse(page, media_type="text/html; charset=utf-8")

    @router.get("/daily/manifest.webmanifest")
    def get_daily_manifest():
        """日报 PWA manifest：手机加到主屏幕后作为独立 App 打开。"""
        return JSONResponse({
            "name": "创作日报", "short_name": "日报",
            "start_url": "/api/fusion/daily/ui", "display": "standalone",
            "background_color": "#0f1420", "theme_color": "#0f1420",
            "icons": [],
        }, media_type="application/manifest+json")

    @router.get("/sw.js")
    def get_service_worker():
        return Response(content=SW_SOURCE, media_type="application/javascript")

    @router.post("/publish-task")
    def post_publish_task(body: PublishTaskIn):
        try:
            return publish.create_publish_task(
                body.asset_path, body.platform, body.account_id,
                body.title, body.desc, body.topics, body.scheduled_at)
        except ValueError as except_value_error:
            raise HTTPException(400, str(except_value_error))

    @router.get("/shots")
    def get_shots(query: str = Query(default="", max_length=60),
                  category: str = Query(default="", max_length=30),
                  limit: int = Query(default=10, ge=1, le=30)):
        return shots.list_shots(query=query, category=category, limit=limit)

    @router.get("/shots/{name}")
    def get_shot(name: str):
        return shots.get_shot(name)

    @router.get("/customers")
    def get_customers(platform: str = Query(default="", max_length=20),
                      query: str = Query(default="", max_length=40),
                      limit: int = Query(default=20, ge=1, le=50)):
        return customers.list_customers(platform=platform, query=query, limit=limit)

    @router.post("/customers/merge")
    def post_customers_merge(body: CustomersMergeIn):
        return customers.merge_customers(body.a, body.b)

    @router.post("/tucard")
    def post_tucard(body: TucardIn):
        try:
            return tucard.render_cards(body.title, body.paragraphs, body.style, body.size)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/intro-v2")
    def post_intro_v2(body: IntroV2In, request: Request = None):
        from .intro_v2 import render_intro_v2

        request_key = request.headers.get("Idempotency-Key", "") if request is not None else ""
        try:
            return render_intro_v2(**body.model_dump(), request_key=request_key)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/intro3d")
    def post_intro3d(body: Intro3dIn):
        # Blender 缺失报 RuntimeError，与参数错误一样回 400，前端直接显示提示文案
        try:
            return blender3d.render_intro(body.title, body.seconds, body.aspect, body.fps)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc))

    @router.get("/skills")
    def get_skills():
        return skillpack.list_skills()

    @router.get("/skills/{name}")
    def get_skill(name: str):
        try:
            return skillpack.get_skill(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.get("/customers/{customer_id}")
    def get_customer(customer_id: str):
        # 路径段是 str；函数内部兼容整数、整数字符串与 "platform:uid" 三种形态
        return customers.get_customer_360(customer_id)

    @router.get("/status")
    def get_status():
        """融合层自检：数据源与渲染工具是否就位。"""
        checks = {
            "radar": (RADAR_DIR / "out" / "topics_latest.md").is_file(),
            "shot_library": SHOT_LIBRARY.is_file(),
            "chrome": chrome_exe() is not None,
            "ffmpeg": True,
            "moneyprinterturbo": (MPT_DIR / "cli.py").is_file() and (MPT_DIR / ".deps").is_dir(),
            "smyw": preview.smyw_available(),
            "skills": skillpack.SKILLS_DIR.is_dir(),
            "blender": blender3d.blender_exe() is not None,
        }
        try:
            checks["ffmpeg"] = bool(ffmpeg_exe())
        except RuntimeError:
            checks["ffmpeg"] = False
        return {"checks": checks, "fusion_dir": str(FUSION_DIR),
                "note": "radar 缺失只影响选题；chrome 缺失只影响封面/架构图/品牌包/贴图卡；"
                        "MPT 缺失只影响混剪；smyw 缺失只影响变更预览；"
                        "skills 目录缺失只影响技能包；Blender 缺失只影响 3D 片头。"}

    @router.get("/ui")
    def get_ui():
        page = Path(__file__).resolve().parents[1] / "web" / "fusion.html"
        if not page.is_file():
            raise HTTPException(404, "创作台页面缺失")
        return FileResponse(page, media_type="text/html; charset=utf-8")

    @router.get("/file")
    def get_file(path: str = Query(min_length=3, max_length=2000)):
        """只允许读取 storage/fusion 下的产物，阻止路径穿越。"""
        root = FUSION_DIR.resolve()
        target = Path(path).resolve()
        if root != target and root not in target.parents:
            raise HTTPException(403, "只能读取融合层产物")
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        return FileResponse(target)

    @router.get("/assets")
    def get_assets(kind: str = Query(default="", max_length=20),
                   limit: int = Query(default=50, ge=1, le=200)):
        """列出融合层已生成的产物。"""
        folders = daily.FOLDERS
        if kind and kind not in folders:
            raise HTTPException(422, "不支持该资产类型")
        items = []
        for key, name in folders.items():
            if kind and key != kind:
                continue
            folder = FUSION_DIR / name
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".png", ".mp4", ".jpg", ".html", ".svg"}:
                    items.append({"kind": key, "name": path.name, "path": str(path),
                                  "bytes": path.stat().st_size,
                                  "mtime": path.stat().st_mtime})
        items.sort(key=lambda row: row["mtime"], reverse=True)
        for row in items:
            row.pop("mtime", None)
        return {"total": len(items), "items": items[:limit]}

    return router
