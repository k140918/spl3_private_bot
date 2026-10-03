"""分類結果の検証と GeminiClassifier の異常系（実 API は呼ばない）。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from bot.services.classifier.base import (
    ClassificationRequest,
    ClassificationResult,
    IntentAction,
    RecruitmentInfo,
    decide_action,
    parse_classification,
)
from bot.services.classifier.gemini_classifier import GeminiClassifier, build_user_prompt

REQUEST = ClassificationRequest(
    target_text="ノ",
    recruitment=RecruitmentInfo("プラベ", "21:00", 8, 3, False),
)


# ---- parse_classification ----


def test_parse_valid():
    r = parse_classification('{"action": "JOIN", "confidence": 0.95, "reason": "明確"}')
    assert (r.action, r.confidence, r.error) == (IntentAction.JOIN, 0.95, False)


def test_parse_ignores_extra_fields_and_accepts_int_confidence():
    r = parse_classification({"action": "NONE", "confidence": 1, "extra": "x"})
    assert (r.action, r.confidence, r.error) == (IntentAction.NONE, 1.0, False)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not json",
        "参加します",
        "[]",
        '"JOIN"',
        '{"action": "JOIN"}',
        '{"confidence": 0.9}',
        '{"action": "join", "confidence": 0.9}',
        '{"action": "MAYBE", "confidence": 0.9}',
        '{"action": "JOIN", "confidence": 1.5}',
        '{"action": "JOIN", "confidence": -0.1}',
        '{"action": "JOIN", "confidence": "0.9"}',
        '{"action": "JOIN", "confidence": true}',
        '{"action": "JOIN", "confidence": NaN}',
        '{"action": "JOIN", "confidence": 0.9',
    ],
)
def test_parse_invalid_becomes_failed_none(raw):
    r = parse_classification(raw)
    assert r.error is True
    assert r.action is IntentAction.NONE


# ---- decide_action ----


def test_decide_applies_threshold():
    assert decide_action(ClassificationResult(IntentAction.JOIN, 0.9), 0.85) is IntentAction.JOIN
    assert decide_action(ClassificationResult(IntentAction.JOIN, 0.84), 0.85) is IntentAction.NONE
    assert decide_action(ClassificationResult(IntentAction.LEAVE, 0.85), 0.85) is IntentAction.LEAVE
    assert decide_action(ClassificationResult.failed(), 0.0) is IntentAction.NONE


# ---- GeminiClassifier（クライアントを差し替え） ----


class _FakeModels:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return await self.behavior()


def _classifier(behavior, timeout=1.0):
    models = _FakeModels(behavior)
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    c = GeminiClassifier(
        api_key="dummy", model="test-model", system_instruction="sys",
        timeout_sec=timeout, client=client,
    )
    return c, models


async def test_gemini_success_uses_structured_output_config():
    async def ok():
        return SimpleNamespace(text=json.dumps({"action": "JOIN", "confidence": 0.97}))

    c, models = _classifier(ok)
    r = await c.classify(REQUEST)
    assert (r.action, r.error) == (IntentAction.JOIN, False)
    call = models.calls[0]
    assert call["model"] == "test-model"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema["properties"]["action"]["enum"] == [
        "JOIN", "LEAVE", "NONE",
    ]


async def test_gemini_timeout_returns_none():
    async def slow():
        await asyncio.sleep(5)

    c, _ = _classifier(slow, timeout=1.0)
    r = await c.classify(REQUEST)
    assert (r.action, r.error) == (IntentAction.NONE, True)


async def test_gemini_api_error_returns_none():
    async def rate_limited():
        raise genai_errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})

    c, _ = _classifier(rate_limited)
    r = await c.classify(REQUEST)
    assert (r.action, r.error) == (IntentAction.NONE, True)


async def test_gemini_unexpected_exception_returns_none():
    async def boom():
        raise RuntimeError("network down")

    c, _ = _classifier(boom)
    assert (await c.classify(REQUEST)).error is True


async def test_gemini_empty_or_invalid_text_returns_none():
    async def empty():
        return SimpleNamespace(text=None)

    async def prose():
        return SimpleNamespace(text="この人は参加すると思います")

    for behavior in (empty, prose):
        c, _ = _classifier(behavior)
        r = await c.classify(REQUEST)
        assert (r.action, r.error) == (IntentAction.NONE, True)


def test_user_prompt_is_json_and_marks_data():
    prompt = build_user_prompt(REQUEST)
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["target_message"] == {"speaker": "TARGET", "text": "ノ"}
    assert payload["recruitment"]["start_time"] == "21:00"
