"""Download API routes for Last.fm playlist downloads."""

import asyncio
import logging
import uuid
from typing import Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("streamrip.admin")

# Track active download tasks
active_downloads: Dict[str, asyncio.Task] = {}


class LastfmRequest(BaseModel):
    url: str
    source: Optional[str] = None
    fallback_source: Optional[str] = None


def create_download_router() -> APIRouter:
    """Create the download API router."""
    router = APIRouter(prefix="/api")

    @router.post("/lastfm")
    async def submit_lastfm_download(request: LastfmRequest):
        """Submit a Last.fm playlist for download."""
        task_id = str(uuid.uuid4())
        
        # Import here to avoid circular imports
        from streamrip.rip.main import Main
        from streamrip.config import Config, DEFAULT_CONFIG_PATH
        
        async def execute_download():
            try:
                # Load config
                config = Config(DEFAULT_CONFIG_PATH)
                
                # Apply lastfm source overrides if provided
                if request.source:
                    config.session.lastfm.source = request.source
                if request.fallback_source:
                    config.session.lastfm.fallback_source = request.fallback_source
                
                async with Main(config) as main:
                    await main.resolve_lastfm(request.url)
                    await main.rip()
                    
            except Exception as e:
                logger.error(f"Last.fm download task {task_id} failed: {e}")
            finally:
                active_downloads.pop(task_id, None)
        
        # Start background task
        task = asyncio.create_task(execute_download())
        active_downloads[task_id] = task
        
        return {"task_id": task_id, "status": "started"}

    @router.get("/downloads")
    async def list_downloads():
        """List active downloads."""
        from .sse_manager import sse_manager
        
        return {
            "active": list(active_downloads.keys()),
            "total_clients": len(sse_manager.clients),
            "total_playlists": len(sse_manager.playlists),
            "total_tracks": len(sse_manager.tracks)
        }

    return router