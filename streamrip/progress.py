"""Modified progress.py with SSE event emission support."""

import asyncio
from dataclasses import dataclass
from typing import Callable

from rich.console import Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.rule import Rule
from rich.text import Text

from .console import console

# Import SSE components if available
try:
    from admin_service.sse_manager import sse_manager, TrackEvent
    SSE_AVAILABLE = True
except ImportError:
    SSE_AVAILABLE = False
    sse_manager = None

class ProgressManager:
    def __init__(self):
        self.started = False
        self.progress = Progress(console=console)
        self.progress = Progress(
            TextColumn("[cyan]{task.description}"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "â€¢",
            TransferSpeedColumn(),
            "â€¢",
            TimeRemainingColumn(),
            console=console,
        )

        self.task_titles = []
        self.prefix = Text.assemble(("Downloading ", "bold cyan"), overflow="ellipsis")
        self._text_cache = self.gen_title_text()
        self.live = Live(Group(self._text_cache, self.progress), refresh_per_second=10)
        
        # Track active downloads for SSE
        self.active_tracks = {}  # task_id -> track_info

    def get_callback(self, total: int, desc: str, track_id: str = None, track_title: str = "", track_artist: str = "", playlist_id: str = None):
        if not self.started:
            self.live.start()
            self.started = True

        task = self.progress.add_task(f"[cyan]{desc}", total=total)
        
        # Store track info for SSE
        if track_id:
            self.active_tracks[task] = {
                "track_id": track_id,
                "title": track_title,
                "artist": track_artist,
                "total": total,
                "progress": 0,
                "playlist_id": playlist_id
            }

        def _callback_update(x: int):
            self.progress.update(task, advance=x)
            self.live.update(Group(self.get_title_text(), self.progress))
            
            # Emit SSE event if available and track_id provided
            if SSE_AVAILABLE and sse_manager and track_id:
                track_info = self.active_tracks.get(task, {})
                track_info["progress"] += x
                
                # Calculate percentage
                percentage = (track_info["progress"] / track_info["total"]) * 100 if track_info["total"] > 0 else 0
                
                # Create and emit track event
                track_event = TrackEvent(
                    track_id=track_id,
                    title=track_title,
                    artist=track_artist,
                    status="downloading",
                    progress=min(percentage, 100.0),
                    playlist_id=playlist_id
                )
                
                # Schedule the coroutine to run
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(sse_manager.update_track(track_event))
                except RuntimeError:
                    # No event loop running, skip SSE update
                    pass

        def _callback_done():
            self.progress.update(task, visible=False)
            
            # Emit completion event
            if SSE_AVAILABLE and sse_manager and track_id:
                track_event = TrackEvent(
                    track_id=track_id,
                    title=track_title,
                    artist=track_artist,
                    status="completed",
                    progress=100.0,
                    playlist_id=playlist_id
                )
                
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(sse_manager.update_track(track_event))
                except RuntimeError:
                    pass
            
            # Clean up
            if task in self.active_tracks:
                del self.active_tracks[task]

        return Handle(_callback_update, _callback_done)

    def cleanup(self):
        if self.started:
            self.live.stop()

    def add_title(self, title: str):
        self.task_titles.append(title.strip())
        self._text_cache = self.gen_title_text()

    def remove_title(self, title: str):
        self.task_titles.remove(title.strip())
        self._text_cache = self.gen_title_text()

    def gen_title_text(self) -> Rule:
        titles = ", ".join(self.task_titles[:3])
        if len(self.task_titles) > 3:
            titles += "..."
        t = self.prefix + Text(titles)
        return Rule(t)

    def get_title_text(self) -> Rule:
        return self._text_cache


@dataclass(slots=True)
class Handle:
    update: Callable[[int], None]
    done: Callable[[], None]

    def __enter__(self):
        return self.update

    def __exit__(self, *_):
        self.done()


# global instance
_p = ProgressManager()


def get_progress_callback(enabled: bool, total: int, desc: str, track_id: str = None, 
                         track_title: str = "", track_artist: str = "", playlist_id: str = None) -> Handle:
    global _p
    if not enabled:
        return Handle(lambda _: None, lambda: None)
    return _p.get_callback(total, desc, track_id, track_title, track_artist, playlist_id)


def add_title(title: str):
    global _p
    _p.add_title(title)


def remove_title(title: str):
    global _p
    _p.remove_title(title)


def clear_progress():
    global _p
    _p.cleanup()


# SSE-specific helper functions
async def emit_track_error(track_id: str, title: str, artist: str, error_message: str, playlist_id: str = None):
    """Emit a track error event via SSE."""
    if SSE_AVAILABLE and sse_manager:
        track_event = TrackEvent(
            track_id=track_id,
            title=title,
            artist=artist,
            status="failed",
            error_message=error_message,
            playlist_id=playlist_id
        )
        await sse_manager.update_track(track_event)


async def emit_track_found(track_id: str, title: str, artist: str, playlist_id: str = None):
    """Emit a track found event via SSE."""
    if SSE_AVAILABLE and sse_manager:
        track_event = TrackEvent(
            track_id=track_id,
            title=title,
            artist=artist,
            status="found",
            playlist_id=playlist_id
        )
        await sse_manager.update_track(track_event)