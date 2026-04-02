"""
Async HTTP client for the Indico HTTP Export API and REST API.

Indico Export API pattern:  GET /export/{resource}.json?{params}
Indico REST API pattern:     GET /api/{path}?{params}  (write operations only)

Authentication: Authorization: Bearer <token>
  - Token must have the 'legacy_api' scope to access /export/ read endpoints.
  - Create tokens at: <indico-url>/user/tokens/
"""

import httpx

from .config import InstanceConfig


class IndicoError(Exception):
    """Raised when the Indico API returns an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class IndicoClient:
    def __init__(self, instance: InstanceConfig) -> None:
        self._base_url = instance.base_url
        headers: dict[str, str] = {"Accept": "application/json"}
        if instance.token:
            headers["Authorization"] = f"Bearer {instance.token}"
        self._http = httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def export(self, resource: str, **params: object) -> dict:
        """
        GET /export/{resource}.json

        resource examples: "categ/269", "event/9531", "event/search/OKC"
        Requires 'legacy_api' scope on the Bearer token.
        """
        clean_params = {k: v for k, v in params.items() if v is not None}
        url = f"{self._base_url}/export/{resource}.json"
        try:
            resp = await self._http.get(url, params=clean_params)
        except httpx.RequestError as exc:
            raise IndicoError(f"Network error reaching {self._base_url}: {exc}") from exc

        if resp.status_code == 401:
            raise IndicoError(
                "Authentication failed. Check INDICO_TOKEN is set and valid.", 401
            )
        if resp.status_code == 403:
            raise IndicoError(
                "Access denied. The token may lack the 'legacy_api' scope.", 403
            )
        if resp.status_code == 404:
            raise IndicoError(f"Resource not found: {resource}", 404)
        if not resp.is_success or "text/html" in resp.headers.get("content-type", ""):
            raise IndicoError(
                "Indico returned HTML instead of JSON. "
                "Ensure the token has the 'Classic API' scope enabled "
                f"(My Profile → Personal Tokens on {self._base_url}).",
                resp.status_code,
            )

        return resp.json()

    async def api(self, path: str, **params: object) -> dict:
        """
        GET /api/{path}
        """
        clean_params = {k: v for k, v in params.items() if v is not None}
        url = f"{self._base_url}/api/{path.lstrip('/')}"
        try:
            resp = await self._http.get(url, params=clean_params)
        except httpx.RequestError as exc:
            raise IndicoError(f"Network error reaching {self._base_url}: {exc}") from exc

        if resp.status_code == 401:
            raise IndicoError("Authentication failed. Check INDICO_TOKEN.", 401)
        if resp.status_code == 403:
            raise IndicoError("Access denied.", 403)
        if resp.status_code == 404:
            raise IndicoError(f"API endpoint not found: {path}", 404)
        if not resp.is_success:
            raise IndicoError(
                f"Indico returned HTTP {resp.status_code} for {url}", resp.status_code
            )

        return resp.json()

    async def post_form(self, path: str, **data: object) -> dict:
        """
        POST /api/{path}  with form-encoded body.

        Used for write operations such as room booking.
        Requires a token with the 'write:legacy_api' scope.
        """
        clean_data = {k: str(v) for k, v in data.items() if v is not None}
        url = f"{self._base_url}/api/{path.lstrip('/')}"
        try:
            resp = await self._http.post(url, data=clean_data)
        except httpx.RequestError as exc:
            raise IndicoError(f"Network error reaching {self._base_url}: {exc}") from exc

        if resp.status_code == 401:
            raise IndicoError(
                "Authentication failed. The token needs the 'write:legacy_api' scope "
                "to perform write operations.", 401
            )
        if resp.status_code == 403:
            raise IndicoError(
                "Access denied — ensure the token has the 'Classic API (read and write)' "
                "scope enabled and that you have permission to book the requested room.", 403
            )
        if not resp.is_success:
            raise IndicoError(
                f"Indico returned HTTP {resp.status_code} for {url}", resp.status_code
            )

        return resp.json()

    async def get(self, path: str, **params: object) -> dict:
        """
        GET {base_url}/{path}  (arbitrary path, no prefix added)

        Used for internal Indico web endpoints such as /category/search that are
        not under /api/ or /export/.
        """
        clean_params = {k: v for k, v in params.items() if v is not None}
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            resp = await self._http.get(url, params=clean_params)
        except httpx.RequestError as exc:
            raise IndicoError(f"Network error reaching {self._base_url}: {exc}") from exc

        if resp.status_code == 401:
            raise IndicoError("Authentication failed. Check INDICO_TOKEN.", 401)
        if resp.status_code == 403:
            raise IndicoError("Access denied.", 403)
        if resp.status_code == 404:
            raise IndicoError(f"Endpoint not found: {path}", 404)
        if not resp.is_success or "text/html" in resp.headers.get("content-type", ""):
            raise IndicoError(
                f"Indico returned HTTP {resp.status_code} for {url}", resp.status_code
            )

        return resp.json()
