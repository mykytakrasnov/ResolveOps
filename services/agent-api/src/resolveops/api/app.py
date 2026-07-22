"""ResolveOps FastAPI application assembly."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from resolveops.api.runs import router as runs_router
from resolveops.repositories.runs import DatabaseRunRepository


def create_app(repository: DatabaseRunRepository | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if repository is not None:
            application.state.run_repository = repository
        else:
            database_url = os.getenv("DATABASE_URL_POOLED")
            if database_url:
                application.state.run_repository = DatabaseRunRepository(database_url)
        yield

    application = FastAPI(title="ResolveOps Agent API", version="0.0.0", lifespan=lifespan)
    application.include_router(runs_router)
    return application


app = create_app()
