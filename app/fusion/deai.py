"""去 AI 味质检：把 impeccable 的 critique 思路落成可执行的规则清单。

不调用模型，纯规则打分；输出问题定位到原句，并给可直接替换的建议。
分数含义：100 = 最像人写的；低于 60 不建议直接发布。
"""
from __future__ import annotations

import re
from collections import Counter

AI_PHRASES = {
    "赋能": "改成具体动作：帮谁把什么事做完",
    "闭环": "说清从哪到哪：线索进来→报价→回款",
    "抓手": "直接说手段：先做哪一件事",
    "颗粒度": "说清是周计划还是日计划",
    "心智": "说人话：客户第一反应是什么",
    "链路": "说清步骤：先…再…最后…",
    "生态": "说清有哪些角色参与",
    "全方位": "删掉，改成具体三件事",
    "多维度": "删掉，列具体维度",
    "深度融合": "删掉，说怎么接的",
    "颠覆": "删掉，说原来怎么做、现在怎么做",
    "革命性": "删掉，给一个可验证的变化",
    "助力": "改成：帮 X 做到了 Y",
    "打造": "改成：做出来一个什么",
    "构建": "改成：搭了一个什么",
    "综上所述": "删掉，直接给结论",
    "总而言之": "删掉，直接给结论",
    "值得注意的是": "删掉，直接说事",
    "不难发现": "删掉，直接说发现",
    "在当今": "删掉时间状语，直接说现象",
    "随着": "改成具体时间点：今年 3 月",
    "让我们一起": "删掉，直接邀请对方做一件事",
    "接下来我们将": "改成：下面做三件事",
}
SEQUENCE_WORDS = ("首先", "其次", "再次", "最后", "第一，", "第二，", "第三，")
HEDGE_WORDS = ("一定程度上", "某种程度上", "相对来说", "总体而言", "一般来说")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def critique_text(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("待质检文本为空")
    sentences = _sentences(text)
    issues: list[dict] = []
    score = 100

    # 1) AI 腔词汇
    for phrase, fix in AI_PHRASES.items():
        count = text.count(phrase)
        if count:
            score -= min(12, 4 * count)
            quote = next((s for s in sentences if phrase in s), phrase)
            issues.append({"type": "ai-phrase", "severity": "high" if count > 1 else "medium",
                           "quote": quote[:80], "phrase": phrase, "fix": fix})

    # 2) 三段式套话
    hits = [word for word in SEQUENCE_WORDS if word in text]
    if len(hits) >= 3:
        score -= 10
        issues.append({"type": "template-structure", "severity": "medium",
                       "quote": "、".join(hits[:5]),
                       "fix": "打散编号：把最反常识的一条提到最前面，其余按因果串起来"})

    # 3) 模糊限定词
    hedge = [word for word in HEDGE_WORDS if word in text]
    if hedge:
        score -= min(9, 3 * len(hedge))
        issues.append({"type": "hedging", "severity": "low", "quote": "、".join(hedge),
                       "fix": "换成具体数字或直接删掉"})

    # 4) 句长：全是长句或全是短句都像机器
    lengths = [len(s) for s in sentences]
    if lengths:
        long_ratio = sum(1 for n in lengths if n > 45) / len(lengths)
        if long_ratio > 0.4:
            score -= 8
            issues.append({"type": "long-sentences", "severity": "medium",
                           "quote": f"超过 45 字的句子占 {long_ratio:.0%}",
                           "fix": "把长句拆成两句，一句一个动作"})
        if len(lengths) >= 4 and max(lengths) - min(lengths) <= 6:
            score -= 6
            issues.append({"type": "uniform-rhythm", "severity": "medium",
                           "quote": f"句长几乎一致（{min(lengths)}–{max(lengths)} 字）",
                           "fix": "刻意插一个 4–6 字的短句，制造停顿"})

    # 5) 标点与表情
    exclaim = text.count("！") + text.count("!")
    if exclaim >= 4:
        score -= 6
        issues.append({"type": "exclamation", "severity": "low", "quote": f"感叹号 {exclaim} 个",
                       "fix": "最多留 1 个；其余改句号"})
    emoji = len(re.findall(r"[\U0001F300-\U0001FAFF]", text))
    if emoji >= 6:
        score -= 5
        issues.append({"type": "emoji-overuse", "severity": "low", "quote": f"表情 {emoji} 个",
                       "fix": "每段最多 1 个，或全部删掉"})

    # 6) 具体性：数字与专有名词
    numbers = len(re.findall(r"\d+(?:\.\d+)?", text))
    if numbers == 0:
        score -= 10
        issues.append({"type": "no-numbers", "severity": "high", "quote": "全文没有数字",
                       "fix": "至少给一个可核对的数字（价格/时长/数量/比例）"})

    # 7) 重复词（同一实词出现过多）
    words = re.findall(r"[\u4e00-\u9fa5]{2,4}", text)
    common = [(word, count) for word, count in Counter(words).most_common(6)
              if count >= 4 and word not in {"我们", "他们", "可以", "这个", "那个", "就是"}]
    if common:
        score -= min(8, 2 * len(common))
        issues.append({"type": "repetition", "severity": "low",
                       "quote": "、".join(f"{w}×{c}" for w, c in common[:3]),
                       "fix": "换同义词，或删掉一半"})

    score = max(0, min(100, score))
    verdict = "可直接发布" if score >= 80 else "小改后发布" if score >= 60 else "建议重写"
    return {"score": score, "verdict": verdict, "issues": issues,
            "stats": {"chars": len(text), "sentences": len(sentences),
                      "avg_sentence": round(sum(lengths) / len(lengths), 1) if lengths else 0,
                      "numbers": numbers},
            "note": "规则只覆盖常见 AI 腔；最终以你自己读一遍顺不顺为准。"}
