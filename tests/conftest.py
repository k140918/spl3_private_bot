from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from bot.models import Mode, Recruitment
from bot.repositories.db import connect
from bot.repositories.recruitment_repo import RecruitmentRepository
from bot.services.recruitment_service import RecruitmentService

GUILD = 1
CHANNEL = 100
CREATOR = 1000


@pytest.fixture
async def service() -> AsyncIterator[RecruitmentService]:
    conn = await connect(":memory:")
    try:
        yield RecruitmentService(RecruitmentRepository(conn))
    finally:
        await conn.close()


async def make_recruitment(
    service: RecruitmentService,
    *,
    mode: Mode = Mode.BOTH,
    capacity: int = 8,
    channel_id: int = CHANNEL,
    start_time: str | None = "21:00",
) -> Recruitment:
    return await service.create(
        guild_id=GUILD,
        channel_id=channel_id,
        creator_user_id=CREATOR,
        title="テスト募集",
        mode=mode,
        capacity=capacity,
        start_time=start_time,
    )
