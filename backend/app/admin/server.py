"""Admin service FastAPI server with SSE for download monitoring."""

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from .sse_manager import sse_manager
from .download_routes import create_download_router

# Suppress uvicorn logging
for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "fastapi", "starlette"]:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

logger = logging.getLogger("streamrip.admin")


def create_app() -> FastAPI:
    """Create the admin service FastAPI app."""
    app = FastAPI(title="StreamRip Admin API")
    
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
    
    # Serve admin UI
    @app.get("/")
    async def serve_admin_ui():
        """Serve the admin monitoring UI."""
        static_path = Path(__file__).parent / "static" / "index.html"
        
        if static_path.exists():
            return FileResponse(static_path, media_type="text/html")
        
        # Fallback HTML if file not found
        return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>StreamRip Admin</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
                .container { background: white; padding: 30px; border-radius: 10px; max-width: 600px; margin: 0 auto; }
                .status { background: #e8f5e8; padding: 15px; border-radius: 5px; margin: 20px 0; }
                h1 { color: #333; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎵 StreamRip Admin</h1>
                <div class="status">
                    <strong>✅ Server Running</strong>
                </div>
                <h3>Available Endpoints:</h3>
                <ul>
                    <li><code>GET /events</code> - SSE stream for progress updates</li>
                    <li><code>POST /api/lastfm</code> - Submit Last.fm download</li>
                    <li><code>GET /api/downloads</code> - List active downloads</li>
                    <li><code>DELETE /api/downloads/{task_id}</code> - Cancel download</li>
                    <li><code>GET /health</code> - Server health check</li>
                </ul>
                <p><strong>Note:</strong> Place your admin UI files in <code>admin_service/static/</code></p>
            </div>
        </body>
        </html>
        """)
    
    # Serve static files
    @app.get("/styles.css")
    async def serve_css():
        """Serve admin CSS file."""
        css_path = Path(__file__).parent / "static" / "styles.css"
        if css_path.exists():
            return FileResponse(css_path, media_type="text/css")
        return {"error": "CSS file not found"}
    
    @app.get("/script.js")
    async def serve_js():
        """Serve admin JavaScript file."""
        js_path = Path(__file__).parent / "static" / "script.js"
        if js_path.exists():
            return FileResponse(js_path, media_type="application/javascript")
        return {"error": "JS file not found"}
    
    return app


# Create the app instance
app = create_app() 