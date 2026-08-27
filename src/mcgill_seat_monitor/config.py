from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from .models import CourseTarget


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str
    username_env: str
    password_env: str
    from_env: str
    to_env: str


@dataclass(frozen=True)
class AppConfig:
    courses: tuple[CourseTarget, ...]
    notification: SmtpConfig
    poll_interval_seconds: int = 300
    request_timeout_seconds: int = 20
    request_retries: int = 3
    max_backoff_seconds: int = 3600
    state_file: Path = Path("state.json")
    log_level: str = "INFO"


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a TOML table")
    return value


def _required(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if value is None or str(value).strip() == "":
        raise ConfigError(f"{context}.{key} is required")
    return str(value).strip()


def load_config(path: Path) -> AppConfig:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    data = _mapping(raw, "configuration")
    raw_courses = data.get("courses")
    if not isinstance(raw_courses, list) or not raw_courses:
        raise ConfigError("courses must contain at least one [[courses]] table")

    courses: list[CourseTarget] = []
    for index, raw_course in enumerate(raw_courses):
        item = _mapping(raw_course, f"courses[{index}]")
        target = CourseTarget(
            term=_required(item, "term", f"courses[{index}]"),
            subject=_required(item, "subject", f"courses[{index}]").upper(),
            number=_required(item, "number", f"courses[{index}]").upper(),
            section=_required(item, "section", f"courses[{index}]"),
            activity=str(item["activity"]).strip() if item.get("activity") else None,
            crn=str(item["crn"]).strip() if item.get("crn") else None,
        )
        courses.append(target)

    ids = [course.target_id for course in courses]
    if len(ids) != len(set(ids)):
        raise ConfigError("courses contains duplicate targets")

    notification = _mapping(data.get("notification"), "notification")
    if notification.get("type", "smtp") != "smtp":
        raise ConfigError("version 1 supports only notification.type: smtp")
    security = str(notification.get("security", "ssl")).lower()
    if security not in {"ssl", "starttls"}:
        raise ConfigError("notification.security must be ssl or starttls")

    smtp = SmtpConfig(
        host=_required(notification, "host", "notification"),
        port=int(notification.get("port", 465 if security == "ssl" else 587)),
        security=security,
        username_env=str(notification.get("username_env", "MSM_SMTP_USERNAME")),
        password_env=str(notification.get("password_env", "MSM_SMTP_PASSWORD")),
        from_env=str(notification.get("from_env", "MSM_EMAIL_FROM")),
        to_env=str(notification.get("to_env", "MSM_EMAIL_TO")),
    )

    poll_interval = int(data.get("poll_interval_seconds", 300))
    if poll_interval < 60:
        raise ConfigError("poll_interval_seconds must be at least 60")
    retries = int(data.get("request_retries", 3))
    if not 1 <= retries <= 8:
        raise ConfigError("request_retries must be between 1 and 8")

    state_path = Path(str(data.get("state_file", "state.json")))
    if not state_path.is_absolute():
        state_path = (path.parent / state_path).resolve()

    return AppConfig(
        courses=tuple(courses),
        notification=smtp,
        poll_interval_seconds=poll_interval,
        request_timeout_seconds=int(data.get("request_timeout_seconds", 20)),
        request_retries=retries,
        max_backoff_seconds=int(data.get("max_backoff_seconds", 3600)),
        state_file=state_path,
        log_level=str(data.get("log_level", "INFO")).upper(),
    )
