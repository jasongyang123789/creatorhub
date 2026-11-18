"""anthropics-skills 技能包：把 19 个文档技能（docx/pptx/pdf/xlsx/canvas-design 等）
变成融合层可列可取的提示词资源。

- list_skills()：扫描技能目录（有 SKILL.md 的子目录），返回名称、描述（frontmatter
  description 截 120 字）、是否带参考材料（目录大小 KB）。
- get_skill(name)：返回单个技能的 SKILL.md 全文，供 Agent 按需加载完整提示词。

技能目录默认指向本机 agent-skills 仓库，CREATORHUB_SKILLS_DIR 环境变量可覆盖；
目录缺失时 list_skills 返回空清单、get_skill 报 ValueError，不影响其他功能。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

SKILLS_DIR = Path(os.environ.get(
    "CREATORHUB_SKILLS_DIR",
    r"G:/VerifiedRepos/agent-skills/anthropics-skills/skills"))

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
# frontmatter 里的 description 字段（简单正则，够用：SKILL.md 头部是固定 YAML）
_DESC_RE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)


def _dir_kb(path: Path) -> int:
    """目录总大小（KB，向下取整）；读不到返回 0。"""
    total = 0
    try:
        for file in path.rglob("*"):
            if file.is_file():
                try:
                    total += file.stat().st_size
                except OSError:
                    pass
    except OSError:
        return 0
    return total // 1024


def _read_description(skill_md: Path) -> str:
    """从 SKILL.md frontmatter 提取 description，去掉引号后截 120 字。"""
    try:
        head = skill_md.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""
    match = _DESC_RE.search(head)
    if not match:
        return ""
    text = match.group(1).strip().strip('"').strip("'").strip()
    return text[:120]


def list_skills() -> dict:
    """列出全部技能：name / description / has_refs / dir_kb；无目录返回空清单。"""
    items = []
    if SKILLS_DIR.is_dir():
        for path in sorted(SKILLS_DIR.iterdir()):
            skill_md = path / "SKILL.md"
            if not path.is_dir() or not skill_md.is_file():
                continue
            kb = _dir_kb(path)
            has_refs = any(f.name != "SKILL.md" for f in path.iterdir() if f.is_file()) \
                or any(f.is_dir() for f in path.iterdir())
            items.append({"name": path.name, "description": _read_description(skill_md),
                          "has_refs": has_refs, "dir_kb": kb})
    return {"total": len(items), "skills_dir": str(SKILLS_DIR), "items": items,
            "note": "GET /api/fusion/skills/{name} 取单个技能的 SKILL.md 全文。"
                    "19 个技能含 docx/pptx/pdf/xlsx/canvas-design/webapp-testing 等。"}


def get_skill(name: str) -> dict:
    """取单个技能：SKILL.md 全文 + 描述 + 路径；找不到或名字非法都报 ValueError。"""
    name = (name or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"技能名只允许小写字母/数字/连字符：{name or '(空)'}"
                         "（GET /api/fusion/skills 先看清单）")
    skill_md = SKILLS_DIR / name / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"没有这个技能：{name}（GET /api/fusion/skills 先看清单）")
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"技能文件不可读：{name}（{exc}）") from exc
    return {"name": name, "description": _read_description(skill_md),
            "path": str(skill_md), "content": content,
            "note": "这是技能的完整提示词，交给 Agent 当作操作手册使用；"
                    "参考材料在技能目录下，用 path 定位。"}
