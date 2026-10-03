from __future__ import annotations

import logging

from bot.logging_setup import RedactingFormatter


def _format(msg: str, secrets: list[str], exc: BaseException | None = None) -> str:
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, msg, None,
                               (type(exc), exc, None) if exc else None)
    return RedactingFormatter("%(message)s", secrets).format(record)


def test_known_secrets_are_redacted_including_traceback():
    out = _format("token=supersecretvalue", ["supersecretvalue"],
                  exc=RuntimeError("key supersecretvalue leaked"))
    assert "supersecretvalue" not in out
    assert "***" in out


def test_secret_patterns_are_redacted_without_registration():
    fake_google_key = "AIza" + "A" * 35
    fake_discord_token = "M" * 26 + "." + "G" * 6 + "." + "x" * 38
    out = _format(f"{fake_google_key} {fake_discord_token}", [])
    assert fake_google_key not in out and fake_discord_token not in out
