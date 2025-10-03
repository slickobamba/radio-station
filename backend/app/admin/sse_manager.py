"""SSE manager for broadcasting progress events."""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import uuid4

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


class SSEManager:
    """Simplified SSE manager for progress events."""
    
    def __init__(self):
        self.clients: Dict[str, asyncio.Queue] = {}
        self.playlists: Dict[str, PlaylistEvent] = {}
        self.tracks: Dict[str, TrackEvent] = {}
        self.search_status: Dict[str, SearchEvent] = {}

    async def add_client(self) -> AsyncGenerator[str, None]:
        """Add a new SSE client and stream events."""
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


# Global manager instance
sse_manager = SSEManager()