"""Thin client for the Instagram API with Instagram Login (publishing)."""

import logging
import time
from collections.abc import Callable

import httpx

HOST = "https://graph.instagram.com"
# Version string taken from the official content-publishing example
# (docs/findings.md, section B). Not independently confirmed as the latest.
API_VERSION = "v27.0"
TIMEOUT_SECONDS = 60.0

# httpx logs "HTTP Request: GET <full url>" at INFO. The refresh call carries
# the token in the query string, so keep that logger quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)


class InstagramError(RuntimeError):
    """Raised for any failed or unexpected Instagram response.

    maybe_published: media_publish may have succeeded server-side, so the
    caller must not blindly retry. code is the Graph error code, if any
    (190 means the access token is invalid or expired).
    """

    def __init__(
        self, message: str, code: int | None = None, maybe_published: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.maybe_published = maybe_published


_SHAPE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


def _error_code(response: httpx.Response) -> int | None:
    # Assumption: errors look like {"error": {"code": 190, ...}}.
    try:
        return int(response.json()["error"]["code"])
    except (*_SHAPE_ERRORS, httpx.DecodingError):
        return None


def _describe(label: str, status: int, code: int | None) -> str:
    # Never echo the Graph error message or URL: they can contain the token.
    text = f"{label} failed: HTTP {status}"
    if code is not None:
        text += f", Graph error code {code}"
    if code == 190:
        text += " (access token invalid or expired; refresh or reissue the token)"
    return text


def _parse_id(data: dict) -> str:
    # Assumption: container and publish calls return {"id": "<id>"}.
    value = data["id"]
    if isinstance(value, bool) or not isinstance(value, (str, int)) or value in ("", 0):
        raise ValueError("id is not a non-empty string or int")
    return str(value)


def _parse_status(data: dict) -> str:
    # Assumption: GET /{container}?fields=status_code -> {"status_code": "..."}.
    return str(data["status_code"])


def _parse_token(data: dict) -> str:
    # Assumption: refresh returns {"access_token": "...", "expires_in": ...}.
    return str(data["access_token"])


def _parse_stats(data: dict) -> dict:
    return {key: data[key] for key in ("followers_count", "media_count")}


def _parse_insights(data: dict) -> dict:
    # Assumption: {"data": [{"name": "reach", "values": [{"value": 1}]}, ...]}.
    result = {item["name"]: item["values"][0]["value"] for item in data["data"]}
    return {key: result[key] for key in ("reach", "saved")}


class Instagram:
    def __init__(
        self,
        user_id: str,
        token: str,
        client: httpx.Client | None = None,
        version: str = API_VERSION,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._user_id = user_id
        self._token = token
        self._base = f"{HOST}/{version}"
        self._sleep = sleep
        self._clock = clock

    def _request(
        self,
        method: str,
        path: str,
        *,
        publishes: bool = False,
        token_in_query: bool = False,
        params: dict | None = None,
        data: dict | None = None,
    ) -> dict:
        """Send a request. publishes=True marks the non-idempotent media_publish."""
        label = f"Instagram {method} {path}"
        headers = {}
        params = dict(params or {})
        if token_in_query:
            params["access_token"] = self._token
        else:
            headers["Authorization"] = f"Bearer {self._token}"
        failure: InstagramError | None = None
        try:
            response = self._client.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                params=params,
                data=data,
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            code = _error_code(error.response)
            failure = InstagramError(
                _describe(label, status, code),
                code=code,
                maybe_published=publishes and status >= 500,
            )
        except httpx.HTTPError as error:
            # type name only: the exception text can contain the request URL.
            failure = InstagramError(
                f"{label} failed: {type(error).__name__}", maybe_published=publishes
            )
        if failure is not None:
            # Raised outside the except block so __context__ stays None: the
            # httpx exception (and its request URL) must not be reachable.
            raise failure
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise InstagramError(
                f"{label} returned a non-JSON or unexpected body"
                + (" (publish may have succeeded)" if publishes else ""),
                maybe_published=publishes,
            )
        return body

    def _parsed(self, label: str, parse: Callable[[dict], object], data: dict, **kw: bool):  # type: ignore[no-untyped-def]
        try:
            return parse(data)
        except _SHAPE_ERRORS as error:
            raise InstagramError(
                f"unexpected Instagram {label} shape: {type(error).__name__}", **kw
            ) from None

    def _create_container(self, image_url: str, caption: str) -> str:
        data = self._request(
            "POST",
            f"/{self._user_id}/media",
            data={"image_url": image_url, "caption": caption},
        )
        return self._parsed("container", _parse_id, data)

    def _wait_until_finished(
        self, container_id: str, poll_seconds: float, timeout: float
    ) -> None:
        deadline = self._clock() + timeout
        while True:
            data = self._request(
                "GET", f"/{container_id}", params={"fields": "status_code"}
            )
            status = self._parsed("status", _parse_status, data)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Instagram container {container_id} status {status}")
            if self._clock() >= deadline:
                raise InstagramError(
                    f"Instagram container {container_id} timed out (last status {status})"
                )
            self._sleep(poll_seconds)

    def _publish_container(self, container_id: str) -> str:
        data = self._request(
            "POST",
            f"/{self._user_id}/media_publish",
            publishes=True,
            data={"creation_id": container_id},
        )
        return self._parsed("publish", _parse_id, data, maybe_published=True)

    def publish_image(
        self, image_url: str, caption: str, poll_seconds: float = 5, timeout: float = 120
    ) -> str:
        container_id = self._create_container(image_url, caption)
        self._wait_until_finished(container_id, poll_seconds, timeout)
        return self._publish_container(container_id)

    def refresh_token(self) -> str:
        # Documented with access_token as a query parameter; errors are built
        # without the URL, so it cannot leak through the exception.
        data = self._request(
            "GET",
            "/refresh_access_token",
            token_in_query=True,
            params={"grant_type": "ig_refresh_token"},
        )
        return self._parsed("refresh", _parse_token, data)

    def account_stats(self) -> dict:
        data = self._request(
            "GET",
            f"/{self._user_id}",
            params={"fields": "followers_count,media_count"},
        )
        return self._parsed("account stats", _parse_stats, data)

    def media_insights(self, media_id: str) -> dict:
        data = self._request(
            "GET", f"/{media_id}/insights", params={"metric": "reach,saved"}
        )
        return self._parsed("insights", _parse_insights, data)
