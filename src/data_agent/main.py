"""FastAPI application entry point."""

import logging

from fastapi import FastAPI

from data_agent.api.chat import router as chat_router
from data_agent.api.datasource import router as datasource_router
from data_agent.config import settings
from data_agent.storage.database import init_database

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Data Agent",
    description="Intelligent data query agent supporting structured and unstructured data",
    version="0.1.0",
)

app.include_router(chat_router)
app.include_router(datasource_router)


@app.on_event("startup")
async def startup():
    logger.info("Initializing database...")
    init_database()
    logger.info("Data Agent ready.")


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("data_agent.main:app", host="0.0.0.0", port=8000, reload=True)
