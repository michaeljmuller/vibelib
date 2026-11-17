"""Pure helpers: naming, retention and the schedule. No I/O, so they are testable."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PREFIX = "vibelib-"
SUFFIX = ".dump"


def dump_name(now: datetime) -> str:
    """Timestamped and lexically sortable, so retention can order by name alone."""
    return f"{PREFIX}{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}{SUFFIX}"


def is_ours(key: str) -> bool:
    return key.startswith(PREFIX) and key.endswith(SUFFIX)


def select_prunable(keys: list[str], retain: int) -> list[str]:
    """Keys to delete, keeping the `retain` newest dumps.

    Only keys with our own name pattern are considered, so nothing else in the
    bucket is ever deleted.
    """
    if retain < 1:
        raise ValueError("BACKUP_RETAIN must be at least 1")
    ours = sorted(k for k in keys if is_ours(k))
    return ours[:-retain]


def schedule_disabled(run_at: str) -> bool:
    """Only the word "never" turns the nightly run off.

    An empty RUN_AT keeps the default time: a new .env on the server looks the
    same as a forgotten setting, and a server that quietly stops backing up is
    the worse outcome.
    """
    return run_at.strip().lower() == "never"


def parse_hhmm(text: str) -> tuple[int, int]:
    parts = text.strip().split(":")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise ValueError(f'RUN_AT must be HH:MM or "never", not {text!r}')
    hour, minute = (int(p) for p in parts)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"RUN_AT must be a real time of day, not {text!r}")
    return hour, minute


def seconds_until(run_at: str, now: datetime) -> float:
    """Seconds from `now` to the next HH:MM, local time."""
    hour, minute = parse_hhmm(run_at)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()
