from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from secure_coding_lab.config import get_settings
from secure_coding_lab.db import engine
from secure_coding_lab.routers import auth, health, pages, products, profile

PACKAGE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


settings = get_settings()
app = FastAPI(
    title="Secure Coding Lab",
    version="0.1.0",
    debug=settings.app_debug,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(products.router)
app.include_router(pages.router)
