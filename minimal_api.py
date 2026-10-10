"""
Minimal API server for testing V2 endpoints.
Bypasses complex lifecycle management.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.v2 import v2_router
from src.api.middleware.auth import AuthenticationMiddleware
from src.core.config_unified import get_settings

app = FastAPI(
    title="TalkBot API",
    version="4.0.0",
    description="TalkBot V2 API - Minimal Server"
)

app.add_middleware(AuthenticationMiddleware, allowed_origins=get_settings().CORS_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v2_router, prefix="/api")

@app.get("/")
async def root():
    return {"status": "ok", "version": "4.0.0"}

@app.get("/health")
async def health():
    return {"status": "healthy"}
