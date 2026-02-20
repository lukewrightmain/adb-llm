"""FastAPI application for cellswarm dashboard."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from cellswarm.server.routes import chat, devices, models, ring
from cellswarm.server.state import app_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting cellswarm dashboard server")
    await app_state.startup()
    yield
    logger.info("Shutting down dashboard server")
    await app_state.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="CellSwarm Dashboard",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(devices.router)
    app.include_router(ring.router)
    app.include_router(models.router)
    app.include_router(chat.router)

    # Serve dashboard static files at /
    dashboard_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        ))),
        "dashboard", "build",
    )
    if os.path.isdir(dashboard_dir):
        app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")
        logger.info("Serving dashboard from {}", dashboard_dir)
    else:
        logger.warning("Dashboard build not found at {}", dashboard_dir)

        @app.get("/")
        async def root():
            return {
                "message": "CellSwarm Dashboard API",
                "note": "Build the dashboard: cd dashboard && npm install && npm run build",
            }

    return app
