from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Any

from .ollama import OllamaClient, OllamaError

SINGLE_SYSTEM = (
    "你是答案评审员。只输出一个 JSON 对象，不要输出解释性文字或代码块。"
    "字段：score(0-10 数字)、reason(不超过 120 字的中文理由)。"
    "评分维度：相关性、正确性、完整性、指令遵循、表达清晰度。"
    "不要因为答案语气自信就提高分数。"
)

DIMENSIONS = ("relevance", "correctness", "completeness", "instruction", "clarity")

SYSTEM_PROMPT = (
    "你是答案评审员。只输出一个 JSON 对象，不要输出解释性文字或代码块。"
    "字段：same_question(布尔)、winner(\"A\"|\"B\"|\"tie\"|\"unknown\")、"
    "score_a(0-10 数字)、score_b(0-10 数字)、reason(不超过 120 字的中文理由)。"
    "评分维度：相关性、正确性、完整性、指令遵循、表达清晰度。"
    "若两份答案质量接近或你无法判断，winner 用 tie 或 unknown。"
)


@dataclass
class JudgeResult:
    same_question: bool | None
    winner: str
    score_a: float | None
    score_b: float | None
    reason: str
    raw: str = ""
    ok: bool = True
    new_label: str = "B"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def winner_side(self) -> str:
        """Return 'old', 'new', 'tie' or 'unknown'."""
        if self.winner == "A":
            return "old" if self.new_label == "B" else "new"
        if self.winner == "B":
            return "new" if self.new_label == "B" else "old"
        return "tie" if self.winner == "tie" else "unknown"

    @property
    def old_score(self) -> float | None:
        return self.score_a if self.new_label == "B" else self.score_b

    @property
    def new_score(self) -> float | None:
        return self.score_b if self.new_label == "B" else self.score_a


def _extract_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?|```$", "", stripped, flags=re.MULTILINE).strip()
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "是", "相同"):
            return True
        if lowered in ("false", "no", "否", "不同"):
            return False
    return None


def _as_score(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(10.0, float(value)))
    if isinstance(value, str):
        try:
            return max(0.0, min(10.0, float(value)))
        except ValueError:
            return None
    return None


def judge_answers(
    client: OllamaClient,
    model: str,
    question: str,
    old_answer: str,
    new_answer: str,
    rng: random.Random | None = None,
) -> JudgeResult:
    """Blindly compare two answers with the local judge model."""
    rng = rng or random.Random()
    new_first = rng.random() < 0.5
    first, second = (new_answer, old_answer) if new_first else (old_answer, new_answer)
    new_label = "A" if new_first else "B"
    prompt = (
        f"用户问题：\n{question[:2000]}\n\n"
        f"候选答案 A：\n{first[:6000]}\n\n"
        f"候选答案 B：\n{second[:6000]}\n\n"
        "请判断这两份答案是否在回答同一个问题，并比较质量。只输出 JSON。"
    )
    try:
        raw = client.complete(model, SYSTEM_PROMPT, prompt)
    except OllamaError as exc:
        return JudgeResult(
            same_question=None,
            winner="unknown",
            score_a=None,
            score_b=None,
            reason=f"裁判模型不可用：{exc}",
            ok=False,
            new_label=new_label,
        )

    data = _extract_json(raw)
    if data is None:
        return JudgeResult(
            same_question=None,
            winner="unknown",
            score_a=None,
            score_b=None,
            reason="裁判输出无法解析，已保留两份版本供你选择。",
            raw=raw[:1000],
            ok=False,
            new_label=new_label,
        )

    winner = str(data.get("winner", "unknown")).strip().upper()
    if winner in ("新版", "新答案", "NEW"):
        winner = "A" if new_label == "A" else "B"
    elif winner in ("旧版", "旧答案", "OLD"):
        winner = "A" if new_label == "B" else "B"
    if winner not in ("A", "B", "TIE", "UNKNOWN"):
        winner = "UNKNOWN"
    same = _as_bool(data.get("same_question"))
    return JudgeResult(
        same_question=same,
        winner=winner.lower() if winner in ("TIE", "UNKNOWN") else winner,
        score_a=_as_score(data.get("score_a")),
        score_b=_as_score(data.get("score_b")),
        reason=str(data.get("reason", ""))[:400],
        raw=raw[:1000],
        ok=True,
        new_label=new_label,
    )


@dataclass
class AnswerScore:
    score: float | None
    reason: str
    ok: bool = True
    raw: str = ""


def score_answer(
    client: OllamaClient,
    model: str,
    question: str,
    answer: str,
) -> AnswerScore:
    """Score a single answer when there is no previous version to compare against."""
    prompt = f"用户问题：\n{question[:2000]}\n\n候选答案：\n{answer[:6000]}\n\n请给这份答案打分。只输出 JSON。"
    try:
        raw = client.complete(model, SINGLE_SYSTEM, prompt)
    except OllamaError as exc:
        return AnswerScore(score=None, reason=f"裁判模型不可用：{exc}", ok=False)

    data = _extract_json(raw)
    if data is None:
        return AnswerScore(score=None, reason="裁判输出无法解析。", ok=False, raw=raw[:1000])
    return AnswerScore(
        score=_as_score(data.get("score")),
        reason=str(data.get("reason", ""))[:400],
        ok=True,
        raw=raw[:1000],
    )
