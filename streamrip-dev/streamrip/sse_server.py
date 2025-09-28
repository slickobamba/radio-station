"""Streamlined SSE server for progress monitoring."""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import uuid4
from pydantic import BaseModel
import uuid
from fastapi.staticfiles import StaticFiles

# Suppress uvicorn logging
for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "fastapi", "starlette"]:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

logger = logging.getLogger("streamrip.sse")


@dataclass
class TrackEvent:
    track_id: str
    title: str
    artist: str
    status: str  # "searching", "found", "downloading", "completed", "failed"
    progress: float = 0.0
    error_message: Optional[str] = None
    playlist_id: Optional[str] = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class PlaylistEvent:
    playlist_id: str
    playlist_name: str
    status: str  # "resolving", "downloading", "completed", "failed"
    total_tracks: int = 0
    found_tracks: int = 0
    failed_tracks: int = 0
    completed_tracks: int = 0
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class SearchEvent:
    playlist_id: str
    total: int
    found: int
    failed: int
    current_query: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

@dataclass
class LastfmRequest(BaseModel):
    url: str
    source: Optional[str] = None
    fallback_source: Optional[str] = None

class SSEManager:
    """Simplified SSE manager for progress events."""
    
    def __init__(self):
        self.clients: Dict[str, asyncio.Queue] = {}
        self.playlists: Dict[str, PlaylistEvent] = {}
        self.tracks: Dict[str, TrackEvent] = {}
        self.search_status: Dict[str, SearchEvent] = {}

    async def add_client(self) -> AsyncGenerator[str, None]:
        """Add a new SSE client."""
        client_id = str(uuid4())
        client_queue = asyncio.Queue(maxsize=50)
        self.clients[client_id] = client_queue
        
        try:
            yield f"event: connection\ndata: {json.dumps({'status': 'connected'})}\n\n"
            
            # Send current state
            for playlist in self.playlists.values():
                yield f"event: playlist_update\ndata: {json.dumps(asdict(playlist))}\n\n"
            
            for track in self.tracks.values():
                yield f"event: track_update\ndata: {json.dumps(asdict(track))}\n\n"

            # Listen for events
            while True:
                try:
                    event_type, data = await asyncio.wait_for(client_queue.get(), timeout=30.0)
                    yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                except Exception:
                    break
                    
        finally:
            self.clients.pop(client_id, None)

    async def broadcast_event(self, event_type: str, data: Any):
        """Broadcast event to all clients."""
        if not self.clients:
            return

        disconnected = []
        for client_id, queue in self.clients.items():
            try:
                queue.put_nowait((event_type, data))
            except:
                disconnected.append(client_id)
        
        for client_id in disconnected:
            self.clients.pop(client_id, None)

    async def update_playlist(self, playlist_event: PlaylistEvent):
        """Update playlist and broadcast."""
        self.playlists[playlist_event.playlist_id] = playlist_event
        await self.broadcast_event("playlist_update", asdict(playlist_event))

    async def update_track(self, track_event: TrackEvent):
        """Update track and broadcast."""
        self.tracks[track_event.track_id] = track_event
        await self.broadcast_event("track_update", asdict(track_event))
        await self._update_playlist_stats()

    async def update_search(self, search_event: SearchEvent):
        """Update search progress."""
        self.search_status[search_event.playlist_id] = search_event
        await self.broadcast_event("search_update", asdict(search_event))

    async def _update_playlist_stats(self):
        """Update playlist statistics from track events."""
        for playlist_id, playlist in self.playlists.items():
            playlist_tracks = [t for t in self.tracks.values() if t.playlist_id == playlist_id]
            
            if playlist_tracks:
                found = len([t for t in playlist_tracks if t.status in ["found", "downloading", "completed"]])
                completed = len([t for t in playlist_tracks if t.status == "completed"])
                failed = len([t for t in playlist_tracks if t.status == "failed"])
                
                if (playlist.found_tracks != found or 
                    playlist.completed_tracks != completed or 
                    playlist.failed_tracks != failed):
                    
                    playlist.found_tracks = found
                    playlist.completed_tracks = completed
                    playlist.failed_tracks = failed
                    playlist.timestamp = time.time()
                    
                    if completed + failed == playlist.total_tracks and playlist.total_tracks > 0:
                        playlist.status = "completed"
                    elif completed > 0 or found > 0:
                        playlist.status = "downloading"
                    
                    await self.broadcast_event("playlist_update", asdict(playlist))


# Add after SSEManager class
active_downloads: Dict[str, asyncio.Task] = {}


class SSEServer:
    """FastAPI server for SSE endpoints."""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        self.host = host
        self.port = port
        self.server = None
        
        try:
            from fastapi import FastAPI
            from fastapi.middleware.cors import CORSMiddleware
            from fastapi.responses import StreamingResponse, HTMLResponse
            
            self.app = FastAPI(title="Streamrip Progress API")
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            
            @self.app.get("/events")
            async def stream_events():
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

            @self.app.get("/health")
            async def health_check():
                return {
                    "status": "healthy",
                    "clients": len(sse_manager.clients),
                    "playlists": len(sse_manager.playlists),
                    "tracks": len(sse_manager.tracks),
                    "server_info": {
                        "host": self.host,
                        "port": self.port
                    }
                }

            @self.app.get("/")
            async def serve_ui():
                """Serve the web UI from file."""
                import os
                from fastapi.responses import FileResponse
                
                # Look for web/index.html relative to the streamrip package
                web_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "index.html")
                
                if os.path.exists(web_path):
                    return FileResponse(web_path, media_type="text/html")
                
                # Fallback to simple status page if file not found
                html = f"""<!DOCTYPE html>
            <html>
            <head>
                <title>Streamrip Progress Server</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
                    .container {{ background: white; padding: 30px; border-radius: 10px; max-width: 600px; margin: 0 auto; }}
                    .status {{ background: #e8f5e8; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                    .endpoint {{ background: #f0f0f0; padding: 10px; border-radius: 5px; margin: 10px 0; font-family: monospace; }}
                    h1 {{ color: #333; }}
                    .error {{ background: #fee2e2; color: #991b1b; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🎵 Streamrip Progress Server</h1>
                    <div class="error">
                        <strong>⚠️ Web UI Not Found</strong><br>
                        Could not find web/index.html at: {web_path}
                    </div>
                    <div class="status">
                        <strong>✅ Server Running</strong><br>
                        Host: {self.host}<br>
                        Port: {self.port}
                    </div>
                    
                    <h3>Available Endpoints:</h3>
                    <div class="endpoint">GET /events - SSE stream for progress updates</div>
                    <div class="endpoint">POST /api/lastfm - Submit Last.fm download</div>
                    <div class="endpoint">GET /api/downloads - List active downloads</div>
                    <div class="endpoint">GET /health - Server health check</div>
                </div>
            </body>
            </html>"""
                return HTMLResponse(html)

            @self.app.post("/api/lastfm")
            async def submit_lastfm_download(request: LastfmRequest):
                task_id = str(uuid.uuid4())
                
                # Import here to avoid circular imports
                from .rip.main import Main
                from .config import Config
                
                async def execute_download():
                    try:
                        # Load config
                        from .config import DEFAULT_CONFIG_PATH
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

            @self.app.get("/api/downloads")
            async def list_downloads():
                return {
                    "active": list(active_downloads.keys()),
                    "total_clients": len(sse_manager.clients),
                    "total_playlists": len(sse_manager.playlists),
                    "total_tracks": len(sse_manager.tracks)
                }

            @self.app.delete("/api/downloads/{task_id}")
            async def cancel_download(task_id: str):
                if task_id in active_downloads:
                    active_downloads[task_id].cancel()
                    active_downloads.pop(task_id, None)
                    return {"status": "cancelled"}
                return {"error": "task not found"}, 404
            
            @self.app.get("/styles.css")
            async def serve_css():
                import os
                css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "styles.css")
                if os.path.exists(css_path):
                    from fastapi.responses import FileResponse
                    return FileResponse(css_path, media_type="text/css")
                return {"error": "CSS file not found"}

            @self.app.get("/script.js")
            async def serve_js():
                import os
                js_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "script.js")
                if os.path.exists(js_path):
                    from fastapi.responses import FileResponse
                    return FileResponse(js_path, media_type="application/javascript")
                return {"error": "JS file not found"}
            
        except ImportError:
            raise ImportError("FastAPI not available. Install with: pip install fastapi uvicorn")

    async def start(self):
        """Start the server."""
        try:
            import uvicorn
            config = uvicorn.Config(
                self.app, host=self.host, port=self.port,
                log_level="critical", access_log=False
            )
            self.server = uvicorn.Server(config)
            await self.server.serve()
        except Exception:
            pass

    def start_background(self):
        """Start server in background."""
        return asyncio.create_task(self.start())


# Global manager instance
sse_manager = SSEManager()