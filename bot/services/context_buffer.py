"""チャンネルごとの直近発言を保持するリングバッファ（メモリ上のみ）。

Discord API で履歴を取得しないので API 呼び出しが増えず、メモリも
「アクティブなメッセージ式募集のあるチャンネル数 × 最大件数」に収まる。
再起動後は空から始まる。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BufferedMessage:
    user_id: int
    message_id: int
    text: str
    at: float


class ContextBuffer:
    def __init__(
        self,
        max_messages: int = 5,
        max_age_sec: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_messages = max_messages
        self._max_age = max_age_sec
        self._clock = clock
        self._buffers: dict[int, deque[BufferedMessage]] = {}

    def add(self, channel_id: int, user_id: int, message_id: int, text: str) -> None:
        if self._max_messages <= 0:
            return
        buf = self._buffers.setdefault(channel_id, deque(maxlen=self._max_messages))
        buf.append(BufferedMessage(user_id, message_id, text, self._clock()))

    def recent(self, channel_id: int) -> list[BufferedMessage]:
        """古い順。max_age_sec より古いものは除外する。"""
        buf = self._buffers.get(channel_id)
        if not buf:
            return []
        threshold = self._clock() - self._max_age
        return [m for m in buf if m.at >= threshold]

    def clear(self, channel_id: int) -> None:
        self._buffers.pop(channel_id, None)
