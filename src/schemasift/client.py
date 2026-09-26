from __future__ import annotations

import httpx

from .exceptions import ProviderError
from .models import SchemaSelectionRequest, SchemaSelectionResult


class SchemaSiftClient:
    def __init__(self, base_url: str, *, api_key: str | None = None,
                 timeout_seconds: float = 30, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def select(self, request: SchemaSelectionRequest) -> SchemaSelectionResult:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = await self.client.post(f"{self.base_url}/v1/schema/select",
                                          json=request.model_dump(mode="json"), headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                detail = response.json().get("detail", response.text)
            except (ValueError, AttributeError):
                detail = response.text
            raise ProviderError(
                f"SchemaSift API returned HTTP {response.status_code}: {detail}"
            ) from exc
        return SchemaSelectionResult.model_validate(response.json())

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.close()
