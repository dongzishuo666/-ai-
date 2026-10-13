"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.ai.scheduler import scheduler
from app.config import BASE_DIR, settings
from app.core.database import init_db
from app.routers import module1, module2, module3, system


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield
    await scheduler.aclose()


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="多AI协助系统 · 三大功能 MVP（文档→Prompt / 多AI讨论 / 三级文章生成）",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(module1.router)
app.include_router(module2.router)
app.include_router(module3.router)


@app.get("/api")
async def api_root() -> dict:
    return {
        "app": settings.app_name,
        "version": settings.version,
        "docs": "/docs",
        "modules": {
            "module1": "/api/module1/generate",
            "module2": "/api/module2/discussions",
            "module3": "/api/module3/generate",
        },
    }


# 若前端已构建（frontend/dist），直接由后端托管，便于单进程部署
_dist = Path(BASE_DIR).parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
