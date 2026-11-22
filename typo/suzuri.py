"""Thin client for the SUZURI REST API."""

import base64

import httpx

BASE_URL = "https://suzuri.jp/api/v1"
TIMEOUT_SECONDS = 120.0


class SuzuriError(RuntimeError):
    """Raised for any failed or unexpected SUZURI response.

    maybe_created: a POST /materials may have succeeded server-side, so the
    caller must not blindly retry. material_id is set when it is known.
    """

    def __init__(
        self, message: str, material_id: int | None = None, maybe_created: bool = False
    ) -> None:
        super().__init__(message)
        self.material_id = material_id
        self.maybe_created = maybe_created


_SHAPE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


def _parse_items(data: dict) -> list[dict]:
    # Assumption: GET /items returns {"items": [{"id", "name"}, ...]}.
    return list(data["items"])


def _parse_item_id(items: list[dict], name: str) -> int | None:
    for item in items:
        if item["name"] == name:
            return int(item["id"])
    return None


def _parse_material(data: dict) -> tuple[int, list[str]]:
    # Assumptions: {"material": {"id", "violation", "products": [{"sampleUrl"}]}}.
    material = data["material"]
    material_id = int(material["id"])
    if material.get("violation"):
        raise SuzuriError(
            f"SUZURI flagged material {material_id} as a violation",
            material_id=material_id,
            maybe_created=True,
        )
    urls = [product["sampleUrl"] for product in material["products"]]
    return material_id, urls


def _salvage_material_id(data: object) -> int | None:
    try:
        return int(data["material"]["id"])  # type: ignore[index]
    except _SHAPE_ERRORS:
        return None


class Suzuri:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def _request(
        self, method: str, path: str, *, creates: bool = False, **kwargs: object
    ) -> dict:
        """Send a request. creates=True marks calls that may create a listing."""
        label = f"SUZURI {method} {path}"
        try:
            response = self._client.request(
                method,
                f"{BASE_URL}{path}",
                headers=self._headers,
                timeout=TIMEOUT_SECONDS,
                **kwargs,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise SuzuriError(
                f"{label} failed: HTTP {status}",
                maybe_created=creates and status >= 500,
            ) from None
        except httpx.HTTPError as error:
            raise SuzuriError(
                f"{label} failed: {type(error).__name__}", maybe_created=creates
            ) from None
        try:
            return response.json()
        except ValueError:
            raise SuzuriError(
                f"{label} returned a non-JSON body"
                + (" (created but response unparseable)" if creates else ""),
                maybe_created=creates,
            ) from None

    def items(self) -> list[dict]:
        data = self._request("GET", "/items")
        try:
            return _parse_items(data)
        except _SHAPE_ERRORS as error:
            raise SuzuriError(
                f"unexpected SUZURI items shape: {type(error).__name__}"
            ) from None

    def item_id(self, name: str) -> int:
        try:
            found = _parse_item_id(self.items(), name)
        except _SHAPE_ERRORS as error:
            raise SuzuriError(
                f"unexpected SUZURI items shape: {type(error).__name__}"
            ) from None
        if found is None:
            raise SuzuriError(f"SUZURI item not found: {name}")
        return found

    def create_material(
        self, title: str, png: bytes, price: int, item_id: int, published: bool = True
    ) -> tuple[int, list[str]]:
        texture = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        body = {
            "title": title,
            "price": price,
            "texture": texture,
            "products": [{"itemId": item_id, "published": published}],
        }
        data = self._request("POST", "/materials", creates=True, json=body)
        try:
            return _parse_material(data)
        except _SHAPE_ERRORS as error:
            raise SuzuriError(
                f"SUZURI material created but response unparseable: {type(error).__name__}",
                material_id=_salvage_material_id(data),
                maybe_created=True,
            ) from None
