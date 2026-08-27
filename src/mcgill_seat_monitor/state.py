from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .models import Observation, SeatState


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transition:
    previous: SeatState | None
    current: SeatState
    notify: bool


class StateStore:
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
