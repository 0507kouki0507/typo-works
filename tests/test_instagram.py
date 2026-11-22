import logging

import httpx
import pytest

from typo.instagram import API_VERSION, Instagram, InstagramError

TOKEN = "IGTEST-secret-token"
USER = "1789"
BASE = f"https://graph.instagram.com/{API_VERSION}"
IMAGE = "https://example.com/a.jpg"


class FakeTime:
    """Deterministic clock: sleeping advances it, nothing really waits."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(handler, fake: FakeTime | None = None) -> Instagram:  # type: ignore[no-untyped-def]
    fake = fake or FakeTime()
    return Instagram(
        USER,
        TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=fake.sleep,
        clock=fake.clock,
    )


def graph_error(code: int, status: int = 400) -> httpx.Response:
    body = {"error": {"message": f"nope {TOKEN}", "type": "OAuthException", "code": code}}
    return httpx.Response(status, json=body)


def test_publish_happy_path() -> None:
    seen: list[httpx.Request] = []
    statuses = iter(["IN_PROGRESS", "FINISHED"])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/media"):
            return httpx.Response(200, json={"id": "C1"})
        if path.endswith("/C1"):
            return httpx.Response(200, json={"status_code": next(statuses), "id": "C1"})
        return httpx.Response(200, json={"id": "M9"})

    fake = FakeTime()
    assert make(handler, fake).publish_image(IMAGE, "cap", poll_seconds=5) == "M9"
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", f"/{API_VERSION}/{USER}/media"),
        ("GET", f"/{API_VERSION}/C1"),
        ("GET", f"/{API_VERSION}/C1"),
        ("POST", f"/{API_VERSION}/{USER}/media_publish"),
    ]
    assert fake.sleeps == [5]
    assert b"image_url=" in seen[0].content and b"caption=cap" in seen[0].content
    assert seen[1].url.params["fields"] == "status_code"
    assert b"creation_id=C1" in seen[3].content
    for request in seen:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
def test_container_error_raises(status: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "C1"})
        return httpx.Response(200, json={"status_code": status})

    with pytest.raises(InstagramError) as info:
        make(handler).publish_image(IMAGE, "c")
    assert status in str(info.value)
    assert info.value.maybe_published is False


def test_poll_timeout_raises() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "C1"})
        return httpx.Response(200, json={"status_code": "IN_PROGRESS"})

    with pytest.raises(InstagramError) as info:
        make(handler).publish_image(IMAGE, "c", poll_seconds=1, timeout=0.01)
    assert "timed out" in str(info.value)
    assert info.value.maybe_published is False
    assert not any(p.endswith("media_publish") for p in paths)


def test_expired_token_message() -> None:
    with pytest.raises(InstagramError) as info:
        make(lambda r: graph_error(190)).publish_image(IMAGE, "c")
    text = str(info.value)
    assert "token" in text and "190" in text
    assert TOKEN not in text
    assert info.value.code == 190
    assert info.value.maybe_published is False


def test_refresh_returns_new_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"access_token": "NEW-token", "token_type": "bearer", "expires_in": 5184000}
        )

    assert make(handler).refresh_token() == "NEW-token"
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == f"/{API_VERSION}/refresh_access_token"
    assert request.url.params["grant_type"] == "ig_refresh_token"
    assert request.url.params["access_token"] == TOKEN


def assert_no_token_anywhere(error: BaseException) -> None:
    seen: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in seen:
        seen.append(current)
        assert TOKEN not in str(current) and TOKEN not in repr(current)
        current = current.__cause__ or current.__context__
    assert error.__cause__ is None and error.__context__ is None


def raising(exc_type: type[httpx.HTTPError]):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type(f"cannot reach {request.url}", request=request)

    return handler


@pytest.mark.parametrize(
    "handler",
    [
        raising(httpx.ConnectError),
        raising(httpx.ReadTimeout),
        lambda r: graph_error(190),
        lambda r: httpx.Response(503, text=str(r.url)),
    ],
)
def test_refresh_failure_does_not_leak_token(handler) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(InstagramError) as info:
        make(handler).refresh_token()
    assert_no_token_anywhere(info.value)
    if info.value.code is not None:
        assert info.value.code == 190


def test_httpx_logger_cannot_log_token_url() -> None:
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_publish_timeout_is_maybe_published() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("media_publish"):
            raise httpx.ReadTimeout("slow", request=request)
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "C1"})
        return httpx.Response(200, json={"status_code": "FINISHED"})

    with pytest.raises(InstagramError) as info:
        make(handler).publish_image(IMAGE, "c")
    assert info.value.maybe_published is True
    assert_no_token_anywhere(info.value)


def publish_with(final: httpx.Response) -> InstagramError:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("media_publish"):
            return final
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "C1"})
        return httpx.Response(200, json={"status_code": "FINISHED"})

    with pytest.raises(InstagramError) as info:
        make(handler).publish_image(IMAGE, "c")
    return info.value


def test_publish_5xx_maybe_published_4xx_not() -> None:
    assert publish_with(httpx.Response(503)).maybe_published is True
    four = publish_with(graph_error(100))
    assert four.maybe_published is False
    assert four.code == 100


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="<html>"), httpx.Response(200, json={"nope": 1}), httpx.Response(200, json=["x"])],
)
def test_publish_2xx_unparseable_is_maybe_published(response: httpx.Response) -> None:
    assert publish_with(response).maybe_published is True


def test_container_creation_failures_are_not_maybe_published() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    for handler in (boom, lambda r: httpx.Response(500), lambda r: httpx.Response(200, json={})):
        with pytest.raises(InstagramError) as info:
            make(handler).publish_image(IMAGE, "c")
        assert info.value.maybe_published is False


def test_account_stats() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"followers_count": 12, "media_count": 3, "id": USER})

    assert make(handler).account_stats() == {"followers_count": 12, "media_count": 3}
    assert seen[0].url.path == f"/{API_VERSION}/{USER}"
    assert seen[0].url.params["fields"] == "followers_count,media_count"


def test_media_insights() -> None:
    payload = {
        "data": [
            {"name": "reach", "values": [{"value": 100}]},
            {"name": "saved", "values": [{"value": 7}]},
        ]
    }
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    assert make(handler).media_insights("M9") == {"reach": 100, "saved": 7}
    assert seen[0].url.path == f"/{API_VERSION}/M9/insights"
    assert seen[0].url.params["metric"] == "reach,saved"


@pytest.mark.parametrize("payload", [{"followers_count": 1}, ["x"], {"data": "x"}])
def test_malformed_stats_raise(payload: object) -> None:
    client = make(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(InstagramError):
        client.account_stats()
    with pytest.raises(InstagramError):
        client.media_insights("M9")


@pytest.mark.parametrize("bad", [None, "", 0, True, [], {"a": 1}])
def test_publish_rejects_invalid_id(bad: object) -> None:
    error = publish_with(httpx.Response(200, json={"id": bad}))
    assert error.maybe_published is True


@pytest.mark.parametrize("bad", [None, "", []])
def test_container_rejects_invalid_id(bad: object) -> None:
    with pytest.raises(InstagramError) as info:
        make(lambda r: httpx.Response(200, json={"id": bad})).publish_image(IMAGE, "c")
    assert info.value.maybe_published is False


def test_integer_id_is_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("media_publish"):
            return httpx.Response(200, json={"id": 123})
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "C1"})
        return httpx.Response(200, json={"status_code": "FINISHED"})

    assert make(handler).publish_image(IMAGE, "c") == "123"
