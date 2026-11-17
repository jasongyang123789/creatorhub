"""smyw（show me you want）变更预览：spec JSON → build.mjs → 单文件 HTML。

skill 仓库：G:/VerifiedRepos/agent-skills/show-me-you-want-skill（MIT）。
spec 校验由 skill 自带的 build.mjs 完成，这里只负责落盘、调用、收产物。
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .paths import FUSION_DIR, ensure_dir, slugify

SMYW_SCRIPT = Path(os.environ["CREATORHUB_SMYW_SCRIPT"]) if os.environ.get("CREATORHUB_SMYW_SCRIPT") else Path(
    r"G:/VerifiedRepos/agent-skills/show-me-you-want-skill/skills/smyw/scripts/build.mjs")


def smyw_available() -> bool:
    return SMYW_SCRIPT.is_file() and shutil.which("node") is not None


def render_preview(spec: dict, name: str = "") -> dict:
    """把一份 smyw spec JSON 交给 build.mjs，产出可交互预览 HTML。"""
    if not isinstance(spec, dict):
        raise ValueError("spec 必须是 JSON 对象（参考 smyw spec-format）")
    if not smyw_available():
        raise RuntimeError("smyw 不可用：需要 Node ≥ 18 与 skill 仓库的 build.mjs")
    folder = ensure_dir(FUSION_DIR / "preview" / f"{datetime.utcnow():%Y%m%d-%H%M%S}-{slugify(name or 'spec', limit=20)}")
    spec_path = folder / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    from .paths import run
    proc = run(["node", str(SMYW_SCRIPT), str(spec_path)], cwd=str(folder), timeout=120)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "")[-500:]
        raise RuntimeError(f"smyw build 失败：{detail}")
    page = folder / "preview.html"
    if not page.is_file():
        raise RuntimeError("smyw build 未产出 preview.html")
    return {"path": str(page), "spec": str(spec_path), "dir": str(folder),
            "bytes": page.stat().st_size,
            "note": "打开 preview.html 可拖动/批注；反馈粘回对话即可应用下一版。"}
