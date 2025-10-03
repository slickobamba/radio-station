"""Admin service FastAPI server - API only."""

import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .sse_manager import sse_manager
from .download_routes import create_download_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("streamrip.admin")

def create_app() -> FastAPI:
    """Create the admin service FastAPI app."""
    app = FastAPI(title="Mama-Radio Admin API")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Add download routes
    app.include_router(create_download_router())
    
    # SSE endpoint
    @app.get("/events")
    async def stream_events():
        """Server-Sent Events endpoint for real-time progress updates."""
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            sse_manager.add_client(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Cache-Control",
            }
        )
    
    # Health check
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "clients": len(sse_manager.clients),
            "playlists": len(sse_manager.playlists),
            "tracks": len(sse_manager.tracks)
        }
    
    return app

app = create_app()