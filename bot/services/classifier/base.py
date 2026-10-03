"""参加意思分類器のインターフェース。

Discord 側はこのモジュールだけに依存し、Gemini 等の実装詳細は知らない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class IntentAction(str, Enum):
    JOIN = "JOIN"
    LEAVE = "LEAVE"
    NONE = "NONE"


@dataclass(frozen=True)
class ClassificationResult:
    action: IntentAction
    confidence: float
    reason: str = field(default="", repr=False)  # デバッグ用。Discordには出さない
    error: bool = False  # API障害・不正レスポンスで NONE に倒した場合 True

    @classmethod
    def failed(cls) -> ClassificationResult:
        return cls(IntentAction.NONE, 0.0, error=True)


@dataclass(frozen=True)
class ContextMessage:
    speaker: str  # "TARGET" または "U1", "U2"...（実名・IDは送らない）
    text: str


@dataclass(frozen=True)
class RecruitmentInfo:
    title: str
    start_time: str | None
    capacity: int
    joined_count: int
    target_already_joined: bool


@dataclass(frozen=True)
class ClassificationRequest:
    target_text: str
    recruitment: RecruitmentInfo
    context: tuple[ContextMessage, ...] = ()


class IntentClassifier(Protocol):
    async def classify(self, request: ClassificationRequest) -> ClassificationResult:
        """例外を投げず、失敗時は ClassificationResult.failed() を返すこと。"""
        ...


# ---- LLM 出力の検証（どの実装でも共通で使う） ----


class _ClassificationSchema(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    action: Literal["JOIN", "LEAVE", "NONE"]
    confidence: float = Field(ge=0.0, le=1.0, strict=True)  # "0.9" や true は不正扱い
    reason: str = Field(default="", max_length=1000)


def parse_classification(raw: str | bytes | dict[str, Any] | None) -> ClassificationResult:
    """LLM の出力を検証する。不正なら ClassificationResult.failed()。"""
    if raw is None:
        return ClassificationResult.failed()
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if not isinstance(data, dict):
            return ClassificationResult.failed()
        parsed = _ClassificationSchema.model_validate(data)
    except (ValueError, ValidationError, TypeError):
        return ClassificationResult.failed()
    return ClassificationResult(IntentAction(parsed.action), parsed.confidence, parsed.reason)


def decide_action(result: ClassificationResult, threshold: float) -> IntentAction:
    """しきい値未満・エラーは NONE（誤登録を避ける側に倒す）。"""
    if result.error or result.confidence < threshold:
        return IntentAction.NONE
    return result.action
