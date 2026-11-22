import json

import httpx
import pytest

from typo.suzuri import Suzuri, SuzuriError

KEY = "test-key"
PNG = b"\x89PNG fake bytes"


def make_client(handler: httpx.MockTransport) -> Suzuri:
    return Suzuri(KEY, client=httpx.Client(transport=handler))


def ok_material(**material: object) -> dict:
    base = {
        "id": 42,
        "violation": False,
        "products": [
            {"sampleUrl": "https://suzuri.jp/example/p/1"},
            {"sampleUrl": "https://suzuri.jp/example/p/2"},
        ],
    }
    return {"material": {**base, **material}}


def test_create_material_sends_expected_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ok_material())

    make_client(httpx.MockTransport(handler)).create_material(
        "NO.001", PNG, 800, 7, published=False
    )
    request = seen[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert request.url == "https://suzuri.jp/api/v1/materials"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert body["title"] == "NO.001"
    assert body["price"] == 800
    assert body["texture"].startswith("data:image/png;base64,")
    assert body["products"][0]["itemId"] == 7
    assert body["products"][0]["published"] is False


def test_returns_id_and_urls() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=ok_material()))
    result = make_client(transport).create_material("t", PNG, 200, 1)
    assert result == (
        42,
        ["https://suzuri.jp/example/p/1", "https://suzuri.jp/example/p/2"],
    )


def test_http_error_raises_without_leaking_key() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": f"bad {KEY}"}, request=r)
    )
    with pytest.raises(SuzuriError) as info:
        make_client(transport).create_material("t", PNG, 200, 1)
    assert KEY not in str(info.value)
    assert "401" in str(info.value)


def test_violation_flag_raises() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json=ok_material(violation=True))
    )
    with pytest.raises(SuzuriError):
        make_client(transport).create_material("t", PNG, 200, 1)


def test_item_id_lookup() -> None:
    payload = {"items": [{"id": 1, "name": "sticker"}, {"id": 9, "name": "poster"}]}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    assert make_client(transport).item_id("poster") == 9


def test_item_id_unknown_raises() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"items": [{"id": 1, "name": "a"}]})
    )
    with pytest.raises(SuzuriError):
        make_client(transport).item_id("missing")


def post(handler) -> Suzuri:  # type: ignore[no-untyped-def]
    return make_client(httpx.MockTransport(handler))


def create(client: Suzuri) -> tuple[int, list[str]]:
    return client.create_material("t", PNG, 200, 1)


def test_violation_error_carries_id() -> None:
    client = post(lambda r: httpx.Response(200, json=ok_material(violation=True)))
    with pytest.raises(SuzuriError) as info:
        create(client)
    assert info.value.material_id == 42
    assert info.value.maybe_created is True
    assert "42" in str(info.value)


def test_malformed_shape_after_2xx_is_maybe_created() -> None:
    client = post(lambda r: httpx.Response(200, json={"material": {"id": 5}}))
    with pytest.raises(SuzuriError) as info:
        create(client)
    assert info.value.maybe_created is True
    assert info.value.material_id == 5
    assert "unparseable" in str(info.value)


def test_unsalvageable_shape_has_no_id() -> None:
    client = post(lambda r: httpx.Response(200, json=["nope"]))
    with pytest.raises(SuzuriError) as info:
        create(client)
    assert info.value.maybe_created is True
    assert info.value.material_id is None


def test_non_numeric_id_is_suzuri_error() -> None:
    client = post(lambda r: httpx.Response(200, json=ok_material(id="abc")))
    with pytest.raises(SuzuriError) as info:
        create(client)
    assert info.value.maybe_created is True
    assert info.value.material_id is None


def test_non_json_body_on_post_is_maybe_created() -> None:
    client = post(lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(SuzuriError) as info:
        create(client)
    assert info.value.maybe_created is True
    assert info.value.material_id is None


def test_timeout_on_post_is_maybe_created() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SuzuriError) as info:
        create(post(handler))
    assert info.value.maybe_created is True


def test_5xx_on_post_is_maybe_created_and_4xx_is_not() -> None:
    with pytest.raises(SuzuriError) as server:
        create(post(lambda r: httpx.Response(503)))
    with pytest.raises(SuzuriError) as client_err:
        create(post(lambda r: httpx.Response(422)))
    assert server.value.maybe_created is True
    assert client_err.value.maybe_created is False


def test_items_errors_are_never_maybe_created() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    for handler in (boom, lambda r: httpx.Response(500), lambda r: httpx.Response(200, text="x")):
        with pytest.raises(SuzuriError) as info:
            post(handler).items()
        assert info.value.maybe_created is False


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [{"name": "poster", "id": "abc"}]},
        {"items": ["not a dict"]},
        {"items": [{"id": 1}]},
        {"nothing": []},
    ],
)
def test_malformed_items_raise_suzuri_error(payload: dict) -> None:
    client = post(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(SuzuriError):
        client.item_id("poster")
