"""一句话任务 — 老板说一句话，员工解析成结构化任务提案。

v1 是纯规则解析（不依赖 LLM、不直接创建任何真实任务）：
  "周五晚上8点发新款视频到抖音和小红书"
    → {action: publish, platforms: [douyin, xhs], when: 2026-11-13T20:00, ...}

安全边界：输出是"提案"（needs_confirm=True）。入队真实发布/采集必须由
调用方再确认；本模块绝不直接写 PublishTask / KeywordCollectionJob。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

PLATFORM_WORDS = {
    "抖音": "douyin", "小红书": "xhs", "红书": "xhs",
    "快手": "kuaishou", "视频号": "shipinhao", "微信视频号": "shipinhao",
    "tiktok": "tiktok", "TikTok": "tiktok", "国际版": "tiktok",
}
ACTION_WORDS = {
    "publish": ("发布", "发一条", "发个", "发表", "上传", "拍一条", "拍个", "更新作品", "发视频"),
    "collect": ("采集", "下载", "抓取", "保存"),
    "monitor": ("监控", "盯着", "盯住", "追踪"),
    "sync": ("同步", "刷新数据", "看数据"),
}
WEEKDAYS = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
            "礼拜一": 0, "礼拜二": 1, "礼拜三": 2, "礼拜四": 3, "礼拜五": 4,
            "礼拜六": 5, "礼拜日": 6}
HOUR_WORDS = {"早上": 8, "上午": 10, "中午": 12, "下午": 15, "傍晚": 18,
              "晚上": 20, "夜里": 22}


def _detect_platforms(text: str) -> list[str]:
    found = []
    for word, code in PLATFORM_WORDS.items():
        if word.lower() in text.lower() and code not in found:
            found.append(code)
    return found


def _detect_action(text: str) -> str:
    low = text.lower()
    for action, words in ACTION_WORDS.items():
        for w in words:
            if w.lower() in low:
                return action
    return "publish"  # 默认：新媒体员工的主要动作


def _detect_when(text: str, today: date) -> tuple[str | None, str | None]:
    """返回 (ISO 日期时间或None, 人类可读描述或None)。识别不了具体日子就不硬猜。"""
    base = None
    label = None

    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        try:
            base = date(today.year, int(m.group(1)), int(m.group(2)))
            label = m.group(0)
        except ValueError:
            base = None
    if base is None:
        for word, wd in WEEKDAYS.items():
            if word in text:
                delta = (wd - today.weekday()) % 7
                if delta == 0:
                    delta = 7  # "周五"在周五说，默认指下周
                if "下" + word in text or "下个" + word in text:
                    delta += 7 if delta <= 7 else 0
                base = today + timedelta(days=delta)
                label = word
                break
    if base is None:
        if "明天" in text:
            base, label = today + timedelta(days=1), "明天"
        elif "后天" in text:
            base, label = today + timedelta(days=2), "后天"
        elif "今天" in text:
            base, label = today, "今天"

    hour = None
    hm = re.search(r"([早上下午晚间夜里]{1,2})(\d{1,2})[点:：]?", text)
    if hm and hm.group(2).isdigit():
        hour = int(hm.group(2))
        if re.search("下午|晚上|夜里", hm.group(1)) and hour < 12:
            hour += 12
    else:
        for word, h in HOUR_WORDS.items():
            if word in text:
                hour = h
                break
        pm = re.search(r"(?:下午|晚上)\s*(\d{1,2})[点:：]?", text)
        if pm:
            hour = int(pm.group(1)) + (12 if int(pm.group(1)) < 12 else 0)

    if base is None:
        return None, None
    if hour is None:
        hour = 8  # 无时间词的日期任务默认早上8点(可编辑)
    return (f"{base.isoformat()}T{hour:02d}:00", f"{label or base.isoformat()} {hour:02d}:00")


def _summary(text: str) -> str:
    cleaned = text
    for w in sorted(PLATFORM_WORDS, key=len, reverse=True):
        cleaned = cleaned.replace(w, "")
    # 完整时间表达(词+时刻)整体移除, 避免留下"晚上"或啃掉内容字
    cleaned = re.sub(r"(?:早上|上午|中午|下午|傍晚|晚上|夜里)\s*\d{1,2}[点:：]?\d{0,2}", "", cleaned)
    for w in (*WEEKDAYS, "明天", "后天", "今天", "下周", "下个"):
        cleaned = cleaned.replace(w, "")
    cleaned = re.sub(r"(?:\d{1,2}月)?\d{1,2}[日号]\s*", "", cleaned)
    cleaned = re.sub(r"\d{1,2}[点:：]\d{0,2}", "", cleaned)
    cleaned = re.sub(r"\s(到|在|把|和|与|同步到|发布到)\s", " ", cleaned)
    cleaned = re.sub(r"[到在把]+(?=$|\s)", "", cleaned)
    cleaned = re.sub(r"[，,。！!？?\s]+", " ", cleaned).strip()
    return (cleaned.strip(" 的到与和") or text)[:60]


def parse_one_liner(text: str, today: date | None = None) -> dict:
    """解析一句话任务。永远返回提案，绝不直接创建任务。"""
    text = (text or "").strip()
    today = today or date.today()
    if not text:
        return {"ok": False, "error": "空内容", "needs_confirm": True}

    platforms = _detect_platforms(text)
    action = _detect_action(text)
    when_iso, when_label = _detect_when(text, today)
    summary = _summary(text)

    confidence = 0.4
    if platforms:
        confidence += 0.2
    if when_iso:
        confidence += 0.2
    if summary and summary != text[:60]:
        confidence += 0.1
    confidence = round(min(confidence, 1.0), 2)

    return {
        "ok": True,
        "needs_confirm": True,          # 安全边界：必须人工确认才入队
        "raw": text,
        "action": action,
        "action_label": {"publish": "发布", "collect": "采集",
                         "monitor": "监控", "sync": "同步数据"}[action],
        "platforms": platforms or ["douyin"],   # 解析不出时默认抖音,标注低置信
        "platforms_detected": bool(platforms),
        "when": when_iso,
        "when_label": when_label,
        "summary": summary,
        "confidence": confidence,
    }


def render_proposal(proposal: dict) -> str:
    """把提案渲染成确认卡片文本（API/CLI/微信消息共用）。"""
    if not proposal.get("ok"):
        return f"❌ {proposal.get('error', '解析失败')}"
    from .morning_report import PLATFORM_LABEL
    plats = "+".join(PLATFORM_LABEL.get(x, x) for x in proposal["platforms"])
    when = proposal.get("when_label") or "待定"
    return (f"📋 任务提案（请确认）\n"
            f"  动作：{proposal['action_label']}\n"
            f"  平台：{plats}\n"
            f"  时间：{when}\n"
            f"  内容：{proposal['summary']}\n"
            f"  置信：{int(proposal['confidence'] * 100)}%")
