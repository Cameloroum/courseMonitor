from __future__ import annotations

import json
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import CourseTarget, Observation
from .parser import ResponseError, parse_class_data


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedCourse:
    course_key: str
    validation: str
    request_id: str


class VsbProvider:
    BASE_URL = "https://vsb.mcgill.ca"

    def __init__(self, timeout: int = 20, attempts: int = 3) -> None:
        self.timeout = timeout
        self.attempts = attempts
        # VSB's edge filter rejects some non-browser HTTP/1.1 User-Agent forms.
        # Keep the project identity while using its accepted compatibility form.
        self.user_agent = "Mozilla/5.0 (compatible; McGillSeatMonitor/0.1)"
        self._resolved: dict[tuple[str, str], ResolvedCourse] = {}

    def _request(self, path: str, data: dict[str, str] | None = None) -> str:
        encoded = urlencode(data or {}).encode() if data is not None else None
        request = Request(
            self.BASE_URL + path,
            data=encoded,
            # A narrow multi-value Accept header is rejected by VSB's edge
            # filter for this request; its own endpoints return their native
            # XML/JSON content without content negotiation.
            headers={"User-Agent": self.user_agent},
            method="POST" if data is not None else "GET",
        )
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return response.read().decode(charset)
            except HTTPError as exc:
                last_error = exc
                if exc.code != 429 and not 500 <= exc.code < 600:
                    break
                delay = _retry_delay(exc, attempt)
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                delay = min(2 ** (attempt - 1), 30)
            if attempt < self.attempts:
                time.sleep(delay)
        raise ProviderError(f"VSB request failed after {self.attempts} attempt(s): {last_error}")

    def resolve_course(self, target: CourseTarget) -> ResolvedCourse:
        cache_key = (target.term, target.course_key)
        if cache_key in self._resolved:
            return self._resolved[cache_key]
        course_text = f"{target.subject} {target.number}"
        body = self._request(
            "/api/string-to-filter",
            {
                "term": target.term,
                "validations": "",
                "itemnames": course_text,
                "input": course_text,
                "reason": "CODE_NUMBER",
                "current": "",
                "isimport": "0",
                "strict": "0",
                "extras": "[]",
            },
        )
        try:
            items = json.loads(body)
            item = next(item for item in items if item.get("cnKey") == target.course_key)
            validation = str(item["va"])
        except (json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
            raise ProviderError(f"unexpected course-resolution response for {course_text}") from exc
        result = ResolvedCourse(target.course_key, validation, str(item.get("reqId", "")))
        self._resolved[cache_key] = result
        return result

    def fetch(self, targets: tuple[CourseTarget, ...]) -> dict[str, Observation]:
        observations: dict[str, Observation] = {}
        by_term: dict[str, list[CourseTarget]] = {}
        for target in targets:
            by_term.setdefault(target.term, []).append(target)

        for term, term_targets in by_term.items():
            unique: dict[str, CourseTarget] = {}
            for target in term_targets:
                unique.setdefault(target.course_key, target)
            resolved = [self.resolve_course(target) for target in unique.values()]

            params: list[tuple[str, str]] = [("term", term)]
            for index, course in enumerate(resolved):
                params.extend(
                    [
                        (f"course_{index}_0", course.course_key),
                        (f"va_{index}_0", course.validation),
                        (f"rq_{index}_0", course.request_id),
                    ]
                )
            minute = int(time.time() // 60) % 1000
            entropy = minute % 3 + minute % 39 + minute % 42
            params.extend([("nouser", "1"), ("t", str(minute)), ("e", str(entropy))])
            try:
                body = self._request("/api/class-data?" + urlencode(params))
                observations.update(parse_class_data(body, tuple(term_targets)))
            except ResponseError as exc:
                for course_key in unique:
                    self._resolved.pop((term, course_key), None)
                raise ProviderError(str(exc)) from exc
        return observations


def _retry_delay(error: HTTPError, attempt: int) -> int:
    value = error.headers.get("Retry-After")
    if value:
        try:
            return max(1, min(int(value), 300))
        except ValueError:
            try:
                seconds = int((parsedate_to_datetime(value).timestamp() - time.time()))
                return max(1, min(seconds, 300))
            except (TypeError, ValueError):
                pass
    return min(2 ** (attempt - 1), 30)
