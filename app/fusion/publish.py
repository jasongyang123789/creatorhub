"""发布草稿：把融合层渲染产物登记为 PublishTask 草稿（绝不自动发布）。

安全约束与 /api/fusion/file 一致：产物必须落在 storage/fusion 内，阻止路径穿越。
发布仍需到发布页二次确认排队后才实际执行；这里只建 status="draft" 的任务。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..db import get_session
from ..models import DouyinAccount, PublishTask
from .paths import FUSION_DIR

PLATFORMS = ("xhs", "douyin", "kuaishou", "shipinhao", "tiktok")
MEDIA_TYPES = {".mp4": "video", ".png": "images", ".jpg": "images"}


def parse_scheduled_at(value: str | None) -> datetime | None:
    """预约时间字符串 → UTC naive datetime。

    接受 ISO 格式；带时区换算成 UTC，不带时区按 UTC 处理（不猜本地时区）。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("scheduled_at 需 ISO 格式") from exc
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def create_publish_task(asset_path: str, platform: str, account_id: int,
                        title: str = "", desc: str = "", topics: str = "",
                        scheduled_at: str | None = None) -> dict:
    """把 FUSION_DIR 内的渲染产物创建为发布草稿（status=draft，不排队）。"""
    root = FUSION_DIR.resolve()
    target = Path(asset_path).resolve()
    if root != target and root not in target.parents:
        raise ValueError("产物路径必须在 storage/fusion 内")
    if not target.is_file():
        raise ValueError("渲染产物不存在，先用渲染步骤生成")
    if platform not in PLATFORMS:
        raise ValueError(f"platform 只支持 {'/'.join(PLATFORMS)}")
    media_type = MEDIA_TYPES.get(target.suffix.lower())
    if media_type is None:
        raise ValueError("只支持 .mp4（视频）与 .png/.jpg（图文）产物")
    moment = parse_scheduled_at(scheduled_at)
    with get_session() as session:
        account = session.get(DouyinAccount, int(account_id))
        if account is None:
            raise ValueError("账号不存在，先到账号页登录")
        if account.status != "active":
            raise ValueError("账号非活跃状态，无法创建发布任务")
        if account.platform != platform:
            raise ValueError(f"账号平台是 {account.platform}，与发布平台 {platform} 不匹配")
        task = PublishTask(
            platform=platform, account_id=account.id, media_type=media_type,
            title=(title or "").strip()[:20], desc=desc or "", topics=topics or "",
            media_json=json.dumps([str(target)], ensure_ascii=False),
            scheduled_at=moment, scheduled_at_is_utc=True, status="draft")
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id
    return {"id": task_id, "status": "draft", "platform": platform,
            "media_type": media_type, "asset_path": str(target),
            "note": "已创建发布草稿（不会自动发布）；到发布页确认排队后才实际发布。"}
