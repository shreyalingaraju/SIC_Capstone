"""
LightSafe decision-support API.

Serves the generated Stage 11-14 artifacts read-only. Start from the repository root:

    .venv\\Scripts\\python.exe -m uvicorn backend.main:app --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .api.routes import router
from .services.data_store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("lightsafe.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading LightSafe artifacts...")
    store.load_all()
    yield


app = FastAPI(
    title="LightSafe Decision-Support API",
    description="Read-only API over the Stage 11-14 outputs (priority index, FIFO comparison, constrained dispatch).",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=config.API_HOST, port=config.API_PORT)
