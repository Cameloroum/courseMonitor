from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree

from .models import CourseTarget, Observation, SeatState


class ResponseError(ValueError):
    pass


def _bool(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes"}


def _integer(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_class_data(xml_text: str, targets: tuple[CourseTarget, ...]) -> dict[str, Observation]:
    if xml_text.lstrip().lower().startswith("<html"):
        raise ResponseError("VSB returned an HTML rejection/error page instead of class data")
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ResponseError(f"VSB returned malformed XML: {exc}") from exc

    errors = [text.strip() for text in root.itertext() if text.strip()] if root.tag == "error" else []
    errors.extend(
        (element.text or "").strip()
        for element in root.findall(".//errors/error")
        if (element.text or "").strip()
    )
    if errors:
        raise ResponseError("VSB error: " + "; ".join(dict.fromkeys(errors)))

    courses: dict[str, list[ElementTree.Element]] = {}
    for course in root.findall(".//course"):
        key = (course.get("key") or "").upper()
        courses.setdefault(key, []).extend(course.findall(".//block"))

    now = datetime.now(timezone.utc)
    observations: dict[str, Observation] = {}
    for target in targets:
        candidates = []
        for block in courses.get(target.course_key, []):
            if target.crn and block.get("key") != target.crn:
                continue
            if not target.crn and block.get("secNo") != target.section:
                continue
            if target.activity and (block.get("type") or "").casefold() != target.activity.casefold():
                continue
            candidates.append(block)

        if not candidates:
            raise ResponseError(f"no VSB class matched {target.display_name}")
        if len(candidates) > 1:
            raise ResponseError(
                f"multiple VSB classes matched {target.display_name}; add activity or crn"
            )

        block = candidates[0]
        unknown = _bool(block.get("u"))
        closed = _bool(block.get("c"))
        is_full = _integer(block.get("isFull"))
        open_seats = _integer(block.get("os"))
        status = block.get("status")

        if unknown:
            state = SeatState.UNKNOWN
        elif closed or (status not in {None, "", "A"}):
            state = SeatState.CLOSED
        elif is_full == 1:
            state = SeatState.FULL
        elif is_full == 0:
            state = SeatState.AVAILABLE
        elif open_seats is not None and open_seats > 0:
            state = SeatState.AVAILABLE
        else:
            state = SeatState.UNKNOWN

        observations[target.target_id] = Observation(
            target=target,
            state=state,
            open_seats=open_seats if open_seats is not None and open_seats >= 0 else None,
            status=status,
            crn=block.get("key"),
            observed_at=now,
        )

    return observations
