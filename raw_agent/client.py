"""A minimal, hand-written client for the Anthropic Messages API.

This is the layer an SDK normally provides. Writing it by hand makes explicit
what that involves:

* authentication and versioning headers,
* an error taxonomy that separates retryable from fatal failures,
* retries with exponential backoff and full jitter, honoring `retry-after`
  and `x-should-retry`,
* never retrying a spend-cap 429, which would only burn time.

The API key is held only in request headers and never logged.
"""

# 📘 [בלי SDK] זה החלק הגדול ביותר שנאלצנו לכתוב ביד. כאן מדברים עם השרת של Anthropic ישירות דרך
# 📘 HTTP: בונים כותרות, שולחים JSON, מפענחים שגיאות ומנסים שוב כשצריך.
# 📘 [עם SDK] את כל הקובץ הזה ה-SDK עושה בשבילנו מאחורי הקלעים, ולכן בגרסה השנייה אין לו מקבילה
# 📘 בכלל.

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from typing import Any, Self

import httpx

from core.config import Settings

# 📘 קודי HTTP שמשמעותם "תקלה זמנית, כדאי לנסות שוב": עומס (529), יותר מדי בקשות (429) ותקלות שרת
# 📘 (5xx).
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_SPEND_CAP_CODE = "enforced_spend_limit_reached"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


# 📘 היררכיית שגיאות: לכל סוג תקלה מחלקה משלה. כך הקוד שמעלינו יודע להבדיל בין "נסה שוב" לבין
# 📘 "הבקשה שגויה, אין טעם לנסות שוב".
class APIError(Exception):
    """Base class for every failure talking to the API."""

    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error_type: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.request_id = request_id

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.error_type:
            parts.append(f"type={self.error_type}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " | ".join(parts)


class APIConnectionError(APIError):
    retryable = True


class APITimeoutError(APIConnectionError):
    pass


class InvalidRequestError(APIError):
    """400, 413, 422: the request itself is wrong. Never retried."""


class AuthenticationError(APIError):
    """401: missing or invalid API key."""


class PermissionDeniedError(APIError):
    """402 / 403: billing problem or a credential not allowed to do this."""


class NotFoundError(APIError):
    """404: unknown endpoint or a model this account cannot use."""


class RateLimitError(APIError):
    retryable = True


# 📘 מקרה מיוחד: 429 שנגרם כי נגמר התקציב בחשבון. הוא נראה כמו עומס, אבל ניסיון חוזר לא יעזור,
# 📘 ולכן לא מנסים.
class SpendLimitError(RateLimitError):
    """A 429 caused by the account's spend cap. Retrying cannot succeed."""

    retryable = False


class OverloadedError(APIError):
    retryable = True


class ServerError(APIError):
    retryable = True


# 📘 מתרגמים תשובת שגיאה מהשרת (קוד HTTP ו-JSON) לאחת המחלקות שלמעלה.
def _error_for(response: httpx.Response) -> APIError:
    """Map an HTTP error response onto the taxonomy above."""
    status = response.status_code
    request_id = response.headers.get("request-id")
    error_type: str | None = None
    message = response.text[:500] or response.reason_phrase
    details_code: str | None = None
    try:
        body = response.json()
        error = body.get("error", {})
        error_type = error.get("type")
        message = error.get("message", message)
        details = error.get("details") or {}
        details_code = details.get("error_code") if isinstance(details, dict) else None
    except ValueError:
        pass

    kwargs: dict[str, Any] = {"status": status, "error_type": error_type, "request_id": request_id}
    if status == 429:
        if details_code == _SPEND_CAP_CODE:
            return SpendLimitError(message, **kwargs)
        return RateLimitError(message, **kwargs)
    if status in (400, 413, 422):
        return InvalidRequestError(message, **kwargs)
    if status == 401:
        return AuthenticationError(message, **kwargs)
    if status in (402, 403):
        return PermissionDeniedError(message, **kwargs)
    if status == 404:
        return NotFoundError(message, **kwargs)
    if status == 529:
        return OverloadedError(message, **kwargs)
    if status >= 500 or status in _RETRYABLE_STATUS:
        return ServerError(message, **kwargs)
    return APIError(message, **kwargs)


class MessagesClient:
    """POST /v1/messages with retries. One instance per run or per benchmark."""

    def __init__(
        self,
        settings: Settings,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise AuthenticationError("ANTHROPIC_API_KEY is not set")
        self._settings = settings
        self._sleep = sleep
        self._rng = rng or random.Random()
        # 📘 שלוש כותרות חובה בכל בקשה: מפתח ה-API, גרסת ה-API וסוג התוכן. המפתח נמצא רק כאן ולא
        # 📘 נכתב ללוג אף פעם.
        headers = {
            "x-api-key": key,
            "anthropic-version": settings.api_version,
            "content-type": "application/json",
        }
        if settings.refusal_fallback:
            headers["anthropic-beta"] = _FALLBACK_BETA
        self._http = httpx.Client(
            base_url=settings.api_base_url,
            headers=headers,
            timeout=httpx.Timeout(settings.api_timeout_s, connect=10.0),
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # 📘 הפונקציה המרכזית: שולחת בקשה אחת. אם היא נכשלה בגלל תקלה זמנית, מחכים ומנסים שוב, עד
    # 📘 מספר ניסיונות מוגבל.
    def create_message(self, body: dict[str, Any]) -> dict[str, Any]:
        """Send one Messages request, retrying transient failures."""
        attempts = self._settings.api_max_retries + 1
        last_error: APIError | None = None
        for attempt in range(attempts):
            try:
                response = self._http.post("/v1/messages", json=body)
            except httpx.TimeoutException as exc:
                last_error = APITimeoutError(f"request timed out: {exc}")
                retry_after: float | None = None
            except httpx.TransportError as exc:
                last_error = APIConnectionError(f"connection failed: {exc}")
                retry_after = None
            else:
                if response.is_success:
                    data: dict[str, Any] = response.json()
                    return data
                last_error = _error_for(response)
                if not self._should_retry(last_error, response):
                    raise last_error
                retry_after = _parse_retry_after(response)

            if attempt == attempts - 1 or not last_error.retryable:
                break
            self._sleep(self._backoff(attempt, retry_after))

        assert last_error is not None
        raise last_error

    # 📘 השרת יכול לומר לנו במפורש אם לנסות שוב, בכותרת x-should-retry. אם הוא אמר, מקשיבים לו.
    @staticmethod
    def _should_retry(error: APIError, response: httpx.Response) -> bool:
        override = response.headers.get("x-should-retry")
        if override == "true":
            return not isinstance(error, SpendLimitError)
        if override == "false":
            return False
        return error.retryable

    # 📘 Exponential backoff עם jitter: כל ניסיון מחכה בערך פי 2 מהקודם, עם רכיב אקראי, כדי שהרבה
    # 📘 לקוחות לא יחזרו לשרת באותה שנייה. אם השרת ביקש זמן המתנה מסוים (retry-after), מכבדים
    # 📘 אותו.
    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        """Exponential backoff with full jitter, or the server's `retry-after` if larger."""
        ceiling = min(
            self._settings.retry_max_delay_s, self._settings.retry_base_delay_s * 2**attempt
        )
        delay = self._rng.uniform(0, ceiling)
        if retry_after is not None:
            delay = max(delay, min(retry_after, self._settings.retry_max_delay_s))
        return delay


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
