from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class SeatState(StrEnum):
    AVAILABLE = "AVAILABLE"
    FULL = "FULL"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CourseTarget:
    term: str
    subject: str
    number: str
    section: str
    activity: str | None = None
    crn: str | None = None

    @property
    def course_key(self) -> str:
        return f"{self.subject.upper()}-{self.number.upper()}"

    @property
    def target_id(self) -> str:
        selector = f"crn-{self.crn}" if self.crn else "-".join(
            part for part in (self.activity, self.section) if part
        )
        return f"{self.term}:{self.course_key}:{selector}"

    @property
    def display_name(self) -> str:
        parts = [self.subject.upper(), self.number.upper()]
        if self.activity:
            parts.append(self.activity)
        parts.append(self.section)
        if self.crn:
            parts.append(f"(CRN {self.crn})")
        return " ".join(parts)


@dataclass(frozen=True)
class Observation:
    target: CourseTarget
    state: SeatState
    open_seats: int | None
    status: str | None
    crn: str | None
    observed_at: datetime
    detail: str | None = None

    @classmethod
    def error(cls, target: CourseTarget, detail: str) -> "Observation":
        return cls(
            target=target,
            state=SeatState.ERROR,
            open_seats=None,
            status=None,
            crn=target.crn,
            observed_at=datetime.now(timezone.utc),
            detail=detail,
        )
