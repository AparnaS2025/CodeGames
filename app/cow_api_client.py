from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class CapacityApiConfig:
    base_url: str = "http://127.0.0.1:8000"
    bearer_token: str | None = None
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


class CapacityApiClient:
    def __init__(self, config: CapacityApiConfig) -> None:
        self.config = config

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json=payload)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=self._clean(params or {}))

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.config.normalized_base_url}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.config.bearer_token:
            headers["Authorization"] = f"Bearer {self.config.bearer_token}"

        try:
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                transport=self.config.transport,
                base_url=self.config.normalized_base_url,
            ) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            return {
                "status": "error",
                "error_type": "http_status",
                "method": method,
                "path": path,
                "status_code": exc.response.status_code,
                "detail": _response_detail(exc.response),
            }
        except httpx.RequestError as exc:
            return {
                "status": "error",
                "error_type": "request",
                "method": method,
                "path": path,
                "detail": str(exc),
            }

    @staticmethod
    def _clean(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if value is not None}


def _response_detail(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
