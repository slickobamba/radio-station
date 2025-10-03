"""Radio service FastAPI server with cover art API."""

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from .cover_api import add_cover_api_endpoints

# Suppress uvicorn logging
for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "fastapi", "starlette"]:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

logger = logging.getLogger("streamrip.radio")


def create_app() -> FastAPI:
    """Create the radio service FastAPI app."""
    app = FastAPI(title="StreamRip Radio API")
    
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
        # Try default path as fallback
        from streamrip.config import APP_DIR
        downloads_db_path = os.path.join(APP_DIR, "downloads.db")
        logger.warning(f"Using default downloads database path: {downloads_db_path}")
    
    # Add cover art API endpoints
    if downloads_db_path and os.path.exists(downloads_db_path):
        add_cover_api_endpoints(app, downloads_db_path)
    else:
        logger.error(f"Downloads database not found at {downloads_db_path}")
    
    # Serve radio player UI
    @app.get("/")
    @app.get("/radio")
    async def serve_radio_player():
        """Serve the radio player HTML."""
        radio_html_path = Path(__file__).parent / "static" / "radio.html"
        
        if radio_html_path.exists():
            return FileResponse(radio_html_path, media_type="text/html")
        
        # Fallback HTML
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>StreamRip Radio</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background: #1e1e2e; color: #cdd6f4; }
                .container { background: #313244; padding: 30px; border-radius: 10px; max-width: 600px; margin: 0 auto; }
                h1 { color: #cdd6f4; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎵 StreamRip Radio</h1>
                <p>Radio player UI not found. Place your radio player HTML in <code>radio_service/static/radio.html</code></p>
                <h3>Available Endpoints:</h3>
                <ul>
                    <li><code>GET /api/cover</code> - Cover art lookup by metadata</li>
                    <li><code>GET /api/cover/by-id/{track_id}</code> - Cover art lookup by track ID</li>
                    <li><code>GET /api/cover/stats</code> - Database statistics</li>
                    <li><code>DELETE /api/cover/cache</code> - Clear lookup cache</li>
                    <li><code>GET /api/radio/info</code> - Radio info</li>
                </ul>
            </div>
        </body>
        </html>
        """)
    
    # Radio info endpoint
    @app.get("/api/radio/info")
    async def radio_info():
        """Get radio service info."""
        return {
            "name": "StreamRip Radio",
            "version": "1.0.0",
            "database_path": downloads_db_path,
            "database_exists": os.path.exists(downloads_db_path) if downloads_db_path else False,
            "endpoints": {
                "player": "/radio",
                "cover_lookup": "/api/cover",
                "cover_by_id": "/api/cover/by-id/{track_id}",
                "cover_stats": "/api/cover/stats",
                "clear_cache": "/api/cover/cache (DELETE)"
            },
            "instructions": {
                "setup": "Update the JavaScript config in your radio.html with your Icecast server details",
                "icecast_url": "http://your-server:8000",
                "stream_url": "http://your-server:8000/your-stream.ogg",
                "note": "Cover art lookups now use the streamrip downloads database"
            }
        }
    
    return app


# Create the app instance
app = create_app()