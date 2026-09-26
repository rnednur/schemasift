from __future__ import annotations

from contextlib import asynccontextmanager

from .config import SchemaSiftConfig, build_selector
from .exceptions import BudgetExceeded, ProviderError, SchemaSiftError
from .models import SchemaSelectionRequest, SchemaSelectionResult


def create_app(config: SchemaSiftConfig):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import ORJSONResponse, RedirectResponse
    except ImportError as exc:
        raise RuntimeError("server dependencies are required: pip install 'schemasift[server]'") from exc

    selector = build_selector(config)

    @asynccontextmanager
    async def lifespan(app):
        yield
        for provider in selector.providers.values():
            close = getattr(provider, "close", None)
            if close:
                await close()

    app = FastAPI(title="SchemaSift", version="0.1.0", lifespan=lifespan,
                  default_response_class=ORJSONResponse,
                  description="High-recall, provider-neutral schema context selection")

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0",
                "features": ["selection_confidence", "provider_retries"]}

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/docs")

    @app.get("/v1/providers")
    async def providers():
        rows = []
        for name, provider in selector.providers.items():
            rows.append({"name": name, "adapter": provider.adapter, "model": provider.model,
                         "healthy": await provider.health(),
                         "capabilities": provider.capabilities.__dict__})
        return {"default": selector.default_provider, "providers": rows}

    @app.post("/v1/schema/select", response_model=SchemaSelectionResult)
    async def select(request: SchemaSelectionRequest):
        try:
            return await selector.select(request)
        except BudgetExceeded as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except SchemaSiftError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
