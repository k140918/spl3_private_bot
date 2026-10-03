"""テスト・オフライン動作確認用の分類器。API を呼ばない。"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from bot.services.classifier.base import (
    ClassificationRequest,
    ClassificationResult,
    IntentAction,
)


class FakeClassifier:
    """固定結果、または関数で結果を返す。呼び出された request を記録する。"""

    def __init__(
        self,
        result: ClassificationResult
        | Callable[[ClassificationRequest], ClassificationResult]
        | Callable[[ClassificationRequest], Awaitable[ClassificationResult]]
        | None = None,
    ) -> None:
        self._result = result or ClassificationResult(IntentAction.NONE, 1.0)
        self.requests: list[ClassificationRequest] = []

    async def classify(self, request: ClassificationRequest) -> ClassificationResult:
        self.requests.append(request)
        if callable(self._result):
            result = self._result(request)
            return await result if inspect.isawaitable(result) else result
        return self._result
