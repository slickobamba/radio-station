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
    
    # Get downloads folder from config
    downloads_folder = os.path.expanduser("~/StreamripDownloads")  # Default
    try:
        from streamrip.config import Config, DEFAULT_CONFIG_PATH
        config = Config(DEFAULT_CONFIG_PATH)
        downloads_folder = config.session.downloads.folder
    except Exception:
        logger.warning("Could not load config, using default downloads folder")
    
    # Add cover art API endpoints
    add_cover_api_endpoints(app, downloads_folder)
    
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
                    <li><code>GET /api/cover</code> - Cover art lookup</li>
                    <li><code>GET /api/cover/search</code> - Search for tracks</li>
                    <li><code>GET /api/cover/stats</code> - Cache statistics</li>
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
            "endpoints": {
                "player": "/radio",
                "cover_lookup": "/api/cover",
                "cover_search": "/api/cover/search",
                "cover_stats": "/api/cover/stats"
            },
            "instructions": {
                "setup": "Update the JavaScript config in your radio.html with your Icecast server details",
                "icecast_url": "http://your-server:8000",
                "stream_url": "http://your-server:8000/your-stream.ogg"
            }
        }
    
    return app


# Create the app instance
app = create_app()