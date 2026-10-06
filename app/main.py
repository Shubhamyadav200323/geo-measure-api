"""FastAPI application entrypoint: ``uvicorn app.main:app``."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.files import router as files_router
from app.core.config import get_settings
from app.core.exceptions import GeoServiceError
from app.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Upload a KML file or a zipped Shapefile; the service extracts every feature "
            "and computes areas (polygons) and lengths (lines) in a locally appropriate "
            "projected CRS."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(GeoServiceError)
    async def _domain_error(_: Request, exc: GeoServiceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    app.include_router(files_router)
    return app


app = create_app()
