"""Optimized playlist module with integrated SSE support."""

import asyncio
import html
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

import aiohttp

from .. import progress
from ..client import Client
from ..config import Config
from ..console import console
from ..db import Database
from ..exceptions import NonStreamableError
from ..filepath_utils import clean_filepath
from ..metadata import AlbumMetadata, PlaylistMetadata, SearchResults, TrackMetadata
from ..utils.ssl_utils import get_aiohttp_connector_kwargs
from .artwork import download_artwork
from .media import Media, Pending
from .track import Track

# SSE integration
try:
    from ..sse_server import sse_manager, PlaylistEvent, SearchEvent, TrackEvent
    SSE_AVAILABLE = True
except ImportError:
    SSE_AVAILABLE = False
    sse_manager = None

logger = logging.getLogger("streamrip")


@dataclass(slots=True)
class PendingPlaylistTrack(Pending):
    id: str
    client: Client
    config: Config
    folder: str
    playlist_name: str
    position: int
    db: Database
    playlist_id: Optional[str] = None

    async def resolve(self) -> Optional[Track]:
        if self.db.downloaded(self.id):
            logger.info(f"Track ({self.id}) already downloaded. Skipping.")
            return None
            
        try:
            resp = await self.client.get_metadata(self.id, "track")
            album = AlbumMetadata.from_track_resp(resp, self.client.source)
            meta = TrackMetadata.from_resp(album, self.client.source, resp)
            
            if not album or not meta:
                self.db.set_failed(self.client.source, "track", self.id)
                return None

            # Apply playlist metadata settings
            c = self.config.session.metadata
            if c.renumber_playlist_tracks:
                meta.tracknumber = self.position
            if c.set_playlist_to_album:
                album.album = self.playlist_name

            quality = self.config.session.get_source(self.client.source).quality
            embedded_cover_path, downloadable = await asyncio.gather(
                self._download_cover(album.covers, self.folder),
                self.client.get_downloadable(self.id, quality),
            )

            return Track(
                meta, downloadable, self.config, self.folder,
                embedded_cover_path, self.db, playlist_id=self.playlist_id
            )
            
        except Exception as e:
            logger.error(f"Error resolving playlist track {self.id}: {e}")
            self.db.set_failed(self.client.source, "track", self.id)
            
            if SSE_AVAILABLE and sse_manager:
                track_event = TrackEvent(
                    track_id=self.id, title=f"Track {self.position}",
                    artist="Unknown", status="failed", error_message=str(e),
                    playlist_id=self.playlist_id
                )
                await sse_manager.update_track(track_event)
            return None

    async def _download_cover(self, covers, folder: str) -> Optional[str]:
        embed_path, _ = await download_artwork(
            self.client.session, folder, covers,
            self.config.session.artwork, for_playlist=True
        )
        return embed_path


@dataclass(slots=True)
class Playlist(Media):
    name: str
    config: Config
    client: Client
    tracks: list[PendingPlaylistTrack]
    playlist_id: str = ""

    def __post_init__(self):
        if not self.playlist_id:
            self.playlist_id = str(uuid4())

    async def preprocess(self):
        progress.add_title(self.name)
        
        if SSE_AVAILABLE and sse_manager:
            playlist_event = PlaylistEvent(
                playlist_id=self.playlist_id, playlist_name=self.name,
                status="downloading", total_tracks=len(self.tracks)
            )
            await sse_manager.update_playlist(playlist_event)

    async def postprocess(self):
        progress.remove_title(self.name)
        
        if SSE_AVAILABLE and sse_manager:
            current_playlist = sse_manager.playlists.get(self.playlist_id)
            if current_playlist:
                current_playlist.status = "completed"
                current_playlist.timestamp = time.time()
                await sse_manager.update_playlist(current_playlist)

    async def download(self):
        """Download playlist tracks in batches."""
        batch_size = 20

        async def _resolve_download(item: PendingPlaylistTrack):
            try:
                track = await item.resolve()
                if track:
                    await track.rip()
            except Exception as e:
                logger.error(f"Error downloading track: {e}")

        # Process in batches
        for i in range(0, len(self.tracks), batch_size):
            batch = self.tracks[i:i + batch_size]
            results = await asyncio.gather(
                *[_resolve_download(track) for track in batch], 
                return_exceptions=True
            )
            
            # Log batch errors
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Batch error: {result}")


@dataclass(slots=True)
class PendingPlaylist(Pending):
    id: str
    client: Client
    config: Config
    db: Database

    async def resolve(self) -> Optional[Playlist]:
        try:
            resp = await self.client.get_metadata(self.id, "playlist")
            meta = PlaylistMetadata.from_resp(resp, self.client.source)
            
            name = meta.name
            parent = self.config.session.downloads.folder
            folder = os.path.join(parent, clean_filepath(name))
            playlist_id = str(uuid4())
            
            tracks = [
                PendingPlaylistTrack(
                    track_id, self.client, self.config, folder, name,
                    position + 1, self.db, playlist_id=playlist_id
                )
                for position, track_id in enumerate(meta.ids())
            ]
            
            return Playlist(name, self.config, self.client, tracks, playlist_id)
            
        except Exception as e:
            logger.error(f"Error resolving playlist {self.id}: {e}")
            return None


@dataclass(slots=True)
class PendingLastfmPlaylist(Pending):
    lastfm_url: str
    client: Client
    fallback_client: Optional[Client]
    config: Config
    db: Database
    playlist_id: str = ""

    def __post_init__(self):
        if not self.playlist_id:
            self.playlist_id = str(uuid4())

    async def resolve(self) -> Optional[Playlist]:
        try:
            playlist_title, titles_artists = await self._parse_lastfm_playlist()
        except Exception as e:
            logger.error(f"Error parsing last.fm playlist: {e}")
            return None

        # Emit playlist resolution start
        if SSE_AVAILABLE and sse_manager:
            playlist_event = PlaylistEvent(
                playlist_id=self.playlist_id, playlist_name=playlist_title,
                status="resolving", total_tracks=len(titles_artists)
            )
            await sse_manager.update_playlist(playlist_event)

        # Search for tracks
        found_count = failed_count = 0
        results = []
        
        for title, artist in titles_artists:
            query = f"{title} {artist}"
            track_id, used_fallback = await self._search_track(query)
            
            if track_id:
                found_count += 1
                client = self.fallback_client if used_fallback else self.client
                results.append((track_id, client))
            else:
                failed_count += 1
                logger.warning(f"No results found for {title} by {artist}")
            
            # Emit search progress
            if SSE_AVAILABLE and sse_manager:
                search_event = SearchEvent(
                    playlist_id=self.playlist_id, total=len(titles_artists),
                    found=found_count, failed=failed_count, current_query=query
                )
                await sse_manager.update_search(search_event)

        # Create playlist
        parent = self.config.session.downloads.folder
        folder = os.path.join(parent, clean_filepath(playlist_title))
        
        tracks = [
            PendingPlaylistTrack(
                track_id, client, self.config, folder, playlist_title,
                pos, self.db, playlist_id=self.playlist_id
            )
            for pos, (track_id, client) in enumerate(results, start=1)
        ]
        
        return Playlist(playlist_title, self.config, self.client, tracks, self.playlist_id)

    async def _search_track(self, query: str) -> tuple[Optional[str], bool]:
        """Search for track, return (track_id, used_fallback)."""
        try:
            # Try main client first
            pages = await self.client.search("track", query, limit=1)
            if pages:
                result = SearchResults.from_pages(self.client.source, "track", pages)
                if result.results:
                    return result.results[0].id, False
            
            # Try fallback client
            if self.fallback_client:
                pages = await self.fallback_client.search("track", query, limit=1)
                if pages:
                    result = SearchResults.from_pages(self.fallback_client.source, "track", pages)
                    if result.results:
                        return result.results[0].id, True
            
            return None, False
            
        except Exception as e:
            logger.error(f"Search error for '{query}': {e}")
            return None, False

    async def _parse_lastfm_playlist(self) -> tuple[str, list[tuple[str, str]]]:
        """Parse Last.fm playlist URL and extract track info."""
        title_tags = re.compile(r'<a\s+href="[^"]+"\s+title="([^"]+)"')
        total_tracks_re = re.compile(r'data-playlisting-entry-count="(\d+)"')
        title_re = re.compile(r'<h1 class="playlisting-playlist-header-title">([^<]+)</h1>')

        def extract_pairs(page_text):
            titles = title_tags.findall(page_text)
            pairs = []
            for i in range(0, len(titles) - 1, 2):
                pairs.append((html.unescape(titles[i]), html.unescape(titles[i + 1])))
            return pairs

        # Setup HTTP session
        verify_ssl = getattr(self.config.session.downloads, "verify_ssl", True)
        connector_kwargs = get_aiohttp_connector_kwargs(verify_ssl=verify_ssl)
        connector = aiohttp.TCPConnector(**connector_kwargs)

        async with aiohttp.ClientSession(connector=connector) as session:
            # Get first page
            async with session.get(self.lastfm_url) as resp:
                page = await resp.text("utf-8")

            # Extract playlist title
            title_match = title_re.search(page)
            if not title_match:
                raise Exception("Could not find playlist title")
            playlist_title = html.unescape(title_match.group(1))

            # Extract track pairs
            pairs = extract_pairs(page)

            # Get total tracks for pagination
            total_match = total_tracks_re.search(page)
            if not total_match:
                return playlist_title, pairs
                
            total_tracks = int(total_match.group(1))
            remaining = total_tracks - 50

            if remaining > 0:
                # Fetch remaining pages
                last_page = 1 + (remaining // 50) + (1 if remaining % 50 else 0)
                tasks = [
                    session.get(self.lastfm_url, params={"page": page_num})
                    for page_num in range(2, last_page + 1)
                ]
                
                responses = await asyncio.gather(*tasks)
                for response in responses:
                    page_text = await response.text("utf-8")
                    pairs.extend(extract_pairs(page_text))

        return playlist_title, pairs