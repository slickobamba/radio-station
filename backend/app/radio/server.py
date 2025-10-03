"""Radio service FastAPI server - API only."""

import logging
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .cover_api import add_cover_api_endpoints

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("streamrip.radio")

def create_app() -> FastAPI:
    """Create the radio service FastAPI app."""
    app = FastAPI(title="Mama-Radio Radio API")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Get downloads database path from config
    downloads_db_path = None
    try:
        from streamrip.config import Config, DEFAULT_CONFIG_PATH
        config = Config(DEFAULT_CONFIG_PATH)
        downloads_db_path = config.session.database.downloads_path
        logger.info(f"Using downloads database at: {downloads_db_path}")
    except Exception as e:
        logger.error(f"Could not load config: {e}")
        from streamrip.config import APP_DIR
        downloads_db_path = os.path.join(APP_DIR, "downloads.db")
        logger.warning(f"Using default downloads database path: {downloads_db_path}")
    
    # Add cover art API endpoints
    if downloads_db_path and os.path.exists(downloads_db_path):
        add_cover_api_endpoints(app, downloads_db_path)
    else:
        logger.error(f"Downloads database not found at {downloads_db_path}")
    
    # Radio info endpoint
    @app.get("/api/radio/info")
    async def radio_info():
        """Get radio service info."""
        return {
            "name": "Mama-Radio",
            "version": "1.0.0",
            "database_path": downloads_db_path,
            "database_exists": os.path.exists(downloads_db_path) if downloads_db_path else False,
            "endpoints": {
                "cover_lookup": "/api/cover",
                "cover_by_id": "/api/cover/by-id/{track_id}",
                "cover_stats": "/api/cover/stats",
                "clear_cache": "/api/cover/cache (DELETE)"
            }
        }
    
    # Health check
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy"}
    
    return app

app = create_app()