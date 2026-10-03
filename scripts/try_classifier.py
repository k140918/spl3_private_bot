"""Gemini 判定を Discord なしで試すスクリプト（実 API を呼ぶので少額の料金が発生しうる）。

使い方:
    python -m scripts.try_classifier            # 組み込みの例文を判定
    python -m scripts.try_classifier "21時ならやる"   # 任意の文を判定
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from bot.config import DEFAULT_PROMPT_PATH, PROJECT_ROOT
from bot.services.classifier.base import (
    ClassificationRequest,
    ContextMessage,
    RecruitmentInfo,
    decide_action,
)
from bot.services.classifier.gemini_classifier import GeminiClassifier

RECRUITMENT = RecruitmentInfo(
    title="プラベ募集", start_time="21:00", capacity=8, joined_count=3, target_already_joined=False
)

# (発言, 期待, 文脈)
SAMPLES: list[tuple[str, str, tuple[ContextMessage, ...]]] = [
    ("ノ", "JOIN", ()),
    ("いきたい", "JOIN", ()),
    ("まだ空いてたら俺も行こうかな", "JOIN", ()),
    ("21時ならやる", "JOIN or NONE", ()),
    ("行きたいけど今日は無理", "NONE", ()),
    ("あと何人？", "NONE", ()),
    ("○○さん行くの？", "NONE", ()),
    ("なら行く", "JOIN", (ContextMessage("TARGET", "何時から？"), ContextMessage("U1", "21時"))),
]


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        sys.exit("GEMINI_API_KEY が設定されていません（.env を確認）")
    threshold = float(os.environ.get("AI_CONFIDENCE_THRESHOLD") or 0.85)
    classifier = GeminiClassifier.from_prompt_file(
        DEFAULT_PROMPT_PATH,
        api_key=api_key,
        model=os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash-lite",
        thinking_level=(os.environ.get("GEMINI_THINKING_LEVEL") or "").strip().lower(),
    )

    samples = [(t, "-", ()) for t in sys.argv[1:]] or SAMPLES
    for text, expected, context in samples:
        result = await classifier.classify(ClassificationRequest(text, RECRUITMENT, context))
        decided = decide_action(result, threshold)
        print(
            f"{text!r:<32} expected={expected:<12} raw={result.action.value}"
            f"({result.confidence:.2f}) -> {decided.value}"
            f"{'  [ERROR]' if result.error else ''}  reason={result.reason}"
        )


if __name__ == "__main__":
    asyncio.run(main())
