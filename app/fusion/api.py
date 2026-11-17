"""融合层 HTTP 端点：只读查询 + 显式渲染任务。

只读：选题、对标、镜头库、去 AI 味、客户 360。
写文件：提取素材、封面、包装镜头、讲解片、混剪——均需显式 POST，产物落 storage/fusion/。
"""
from __future__ import annotations

from typing import Literal

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import cover, customers, deai, explainer, extract, mpt, packaging, shots, topics
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
    style: Literal["hallmark", "nothing", "huashu"] = "hallmark"
    size: Literal["1080x1440", "1080x1920", "1920x1080", "1080x1080"] = "1080x1440"
    badge: str = Field(default="CREATORHUB", max_length=30)
    index: int = Field(default=1, ge=1, le=999)


class PackagingIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lines: list[str] = Field(min_length=1, max_length=6)
    style: Literal["kinetic", "data-card", "chapter"] = "kinetic"
    duration: float = Field(default=3.0, ge=1, le=15)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    fps: int = Field(default=30, ge=15, le=60)
    index: int = Field(default=1, ge=1, le=99)


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
        return packaging.render_packaging(body.lines, body.style, body.duration,
                                          body.aspect, body.fps, body.index)

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

    @router.get("/customers/{customer_id}")
    def get_customer(customer_id: str):
        return customers.get_customer_360(customer_id)

    @router.get("/status")
    def get_status():
        """融合层自检：数据源与渲染工具是否就位。"""
        from pathlib import Path
        checks = {
            "radar": (RADAR_DIR / "out" / "topics_latest.md").is_file(),
            "shot_library": SHOT_LIBRARY.is_file(),
            "chrome": chrome_exe() is not None,
            "ffmpeg": True,
            "moneyprinterturbo": (MPT_DIR / "cli.py").is_file() and (MPT_DIR / ".deps").is_dir(),
        }
        try:
            checks["ffmpeg"] = bool(ffmpeg_exe())
        except RuntimeError:
            checks["ffmpeg"] = False
        return {"checks": checks, "fusion_dir": str(FUSION_DIR),
                "note": "radar 缺失只影响选题；chrome 缺失只影响封面；MPT 缺失只影响混剪。"}

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
        """列出融合层已生成的产物（封面/包装/讲解片/混剪/提取）。"""
        folders = {"cover": "covers", "packaging": "packaging", "explainer": "explainer",
                   "short": "shorts", "extract": "extract"}
        if kind and kind not in folders:
            raise ValueError("kind 只支持 cover/packaging/explainer/short/extract")
        items = []
        for key, name in folders.items():
            if kind and key != kind:
                continue
            folder = FUSION_DIR / name
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".png", ".mp4", ".jpg", ".html"}:
                    items.append({"kind": key, "name": path.name, "path": str(path),
                                  "bytes": path.stat().st_size,
                                  "mtime": path.stat().st_mtime})
        items.sort(key=lambda row: row["mtime"], reverse=True)
        for row in items:
            row.pop("mtime", None)
        return {"total": len(items), "items": items[:limit]}

    return router
