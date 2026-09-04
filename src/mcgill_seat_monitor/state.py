from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Observation, SeatState


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transition:
    previous: SeatState | None
    current: SeatState
    notify: bool


class StateStore:
    # Keep service-level counters beside target states in the same atomic file.
    # This reserved key cannot collide with a target_id, which always contains
    # a term and course key separated by colons.
    META_KEY = "__monitor__"

    def __init__(self, path: Path) -> None:
        self.path = path
        self._states = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("could not read state file %s; starting without saved state: %s", self.path, exc)
            return {}

    def previous(self, target_id: str) -> SeatState | None:
        value = self._states.get(target_id, {}).get("state")
        try:
            return SeatState(str(value)) if value else None
        except ValueError:
            return None

    def transition(self, observation: Observation) -> Transition:
        previous = self.previous(observation.target.target_id)
        current = observation.state
        notify = current == SeatState.AVAILABLE and previous != SeatState.AVAILABLE
        return Transition(previous, current, notify)

    def save(self, observation: Observation) -> None:
        current = observation.state
        if current != SeatState.ERROR:
            self._states[observation.target.target_id] = {
                "state": current.value,
                "open_seats": observation.open_seats,
                "crn": observation.crn,
                "observed_at": observation.observed_at.isoformat(),
            }
            self._save()

    def record_successful_check(self, checked_at: datetime) -> int:
        """Persist one successful poll cycle and return the current weekly count."""
        meta = self._metadata()
        count = self.checks_since_report + 1
        meta["checks_since_report"] = count
        meta.setdefault("report_period_started_at", checked_at.astimezone(timezone.utc).isoformat())
        self._save()
        return count

    @property
    def checks_since_report(self) -> int:
        value = self._metadata().get("checks_since_report", 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def weekly_report_due(
        self,
        now: datetime,
        timezone_name: str,
        weekday: int,
        hour: int,
        minute: int,
    ) -> bool:
        """Return whether the most recent local weekly boundary is unsent."""
        meta = self._metadata()
        started_at = self._timestamp(meta.get("report_period_started_at"))
        if started_at is None or self.checks_since_report == 0:
            return False
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            LOG.error("unknown weekly report timezone: %s", timezone_name)
            return False
        local_now = now.astimezone(zone)
        # Construct the boundary in local civil time first so ZoneInfo applies
        # Montreal's daylight-saving offset for that specific Sunday.
        scheduled_date = local_now.date() - timedelta(days=(local_now.weekday() - weekday) % 7)
        scheduled_local = datetime.combine(scheduled_date, time(hour, minute), tzinfo=zone)
        if local_now < scheduled_local:
            scheduled_local -= timedelta(days=7)
        scheduled_at = scheduled_local.astimezone(timezone.utc)
        last_report_at = self._timestamp(meta.get("last_weekly_report_at"))
        return started_at <= scheduled_at and (last_report_at is None or last_report_at < scheduled_at)

    def mark_weekly_report_sent(self, sent_at: datetime) -> None:
        """Start a new counting period only after SMTP delivery succeeds."""
        meta = self._metadata()
        timestamp = sent_at.astimezone(timezone.utc).isoformat()
        meta["checks_since_report"] = 0
        meta["last_weekly_report_at"] = timestamp
        meta["report_period_started_at"] = timestamp
        self._save()

    def _metadata(self) -> dict[str, object]:
        value = self._states.get(self.META_KEY)
        if not isinstance(value, dict):
            value = {}
            self._states[self.META_KEY] = value
        return value

    @staticmethod
    def _timestamp(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._states, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
