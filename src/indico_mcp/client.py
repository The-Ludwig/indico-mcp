"""
Async HTTP client for the Indico HTTP Export API and REST API.

Indico Export API pattern:  GET /export/{resource}.json?{params}
Indico REST API pattern:     GET /api/{path}?{params}

Authentication: Authorization: Bearer <token>  (optional for public instances)
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

        resource examples: "categ/0", "event/12345", "event/12345/contributions"
        """
        # Remove None values so they don't end up as "None" strings
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
                "Access denied. The token may lack required scopes (legacy_api).", 403
            )
        if resp.status_code == 404:
            raise IndicoError(f"Resource not found: {resource}", 404)
        if not resp.is_success:
            raise IndicoError(
                f"Indico returned HTTP {resp.status_code} for {url}", resp.status_code
            )

        return resp.json()

    async def api(self, path: str, **params: object) -> dict:
        """
        GET /api/{path}

        path examples: "search/", "categories/123/"
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
