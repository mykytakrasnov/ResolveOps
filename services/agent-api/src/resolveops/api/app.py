"""ResolveOps FastAPI application assembly."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from resolveops.api.runs import router as runs_router
from resolveops.repositories.runs import DatabaseRunRepository
from resolveops.storage.artifacts import LocalObjectStorage, ObjectStorage
from resolveops.tools.read_only import ReadOnlyToolset
from resolveops.tools.synthetic_api import SyntheticApiBackend


def create_app(
    repository: DatabaseRunRepository | None = None,
    read_tools: ReadOnlyToolset | None = None,
    object_storage: ObjectStorage | None = None,
    checkpoint_dsn: str | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if repository is not None:
            application.state.run_repository = repository
        else:
            database_url = os.getenv("DATABASE_URL_POOLED")
            if database_url:
                application.state.run_repository = DatabaseRunRepository(database_url)
        if read_tools is not None:
            application.state.read_tools = read_tools
        else:
            synthetic_api_base_url = os.getenv("SYNTHETIC_API_BASE_URL")
            synthetic_api_hmac_secret = os.getenv("SYNTHETIC_API_HMAC_SECRET")
            if synthetic_api_base_url and synthetic_api_hmac_secret:
                application.state.read_tools = ReadOnlyToolset(
                    SyntheticApiBackend(
                        base_url=synthetic_api_base_url,
                        hmac_secret=synthetic_api_hmac_secret,
                    )
                )
        if object_storage is not None:
            application.state.object_storage = object_storage
        else:
            object_storage_root = os.getenv("RESOLVEOPS_OBJECT_STORAGE_ROOT")
            if object_storage_root:
                application.state.object_storage = LocalObjectStorage(Path(object_storage_root))
        configured_checkpoint_dsn = (
            checkpoint_dsn
            or os.getenv("DATABASE_URL_CHECKPOINT")
            or os.getenv("DATABASE_URL_POOLED")
        )
        if configured_checkpoint_dsn:
            application.state.checkpoint_dsn = configured_checkpoint_dsn
        yield

    application = FastAPI(title="ResolveOps Agent API", version="0.0.0", lifespan=lifespan)
    application.include_router(runs_router)
    return application


app = create_app()
