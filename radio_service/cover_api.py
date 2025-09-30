"""Cover art lookup API endpoint for radio player."""

import logging
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import unquote

from fastapi import HTTPException, Query

logger = logging.getLogger("streamrip.radio")


class CoverArtLookup:
    """Looks up cover art from streamrip downloads database."""
    
    def __init__(self, downloads_db_path: str):
        self.db_path = Path(downloads_db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Downloads database not found at {self.db_path}")
        
        # Put cache DB in same directory as this Python file
        radio_service_dir = Path(__file__).parent
        self.cache_db = str(radio_service_dir / "cover_cache.db")
        self.init_cache_db()

    def init_cache_db(self):
        """Initialize SQLite cache for faster lookups."""
        with sqlite3.connect(self.cache_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cover_cache (
                    lookup_key TEXT PRIMARY KEY,
                    track_id TEXT,
                    cover_url TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lookup_key 
                ON cover_cache(lookup_key)
            """)
    
    def get_cache_key(self, artist: str, title: str, album: str = "") -> str:
        """Generate cache key for track lookup."""
        key_parts = [
            artist.lower().strip(),
            title.lower().strip(),
            album.lower().strip() if album else ""
        ]
        return "|".join(key_parts)
    
    def lookup_cover_url(self, artist: str, title: str, album: str = "") -> Optional[Dict[str, Any]]:
        """Look up cover URL for a track."""
        cache_key = self.get_cache_key(artist, title, album)
        
        # Check cache first
        cached = self.get_from_cache(cache_key)
        if cached:
            return cached
        
        # Search in downloads database by track metadata
        result = self.search_by_metadata(artist, title, album)
        
        # Cache the result (even if None)
        self.cache_result(cache_key, result)
        
        return result
    
    def get_from_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get result from cache."""
        with sqlite3.connect(self.cache_db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM cover_cache WHERE lookup_key = ?",
                (cache_key,)
            ).fetchone()
            
            if row:
                return {
                    "track_id": row["track_id"],
                    "cover_url": row["cover_url"],
                    "source": "cache"
                }
        return None
    
    def cache_result(self, cache_key: str, result: Optional[Dict[str, Any]]):
        """Cache lookup result."""
        with sqlite3.connect(self.cache_db) as conn:
            if result:
                conn.execute("""
                    INSERT OR REPLACE INTO cover_cache 
                    (lookup_key, track_id, cover_url)
                    VALUES (?, ?, ?)
                """, (cache_key, result.get("track_id"), result.get("cover_url")))
            else:
                # Cache negative results too (with empty values)
                conn.execute("""
                    INSERT OR REPLACE INTO cover_cache 
                    (lookup_key, track_id, cover_url)
                    VALUES (?, ?, ?)
                """, (cache_key, None, None))
    
    def search_by_metadata(self, artist: str, title: str, album: str = "") -> Optional[Dict[str, Any]]:
        """Search for cover URL by track metadata in the covers table."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Case-insensitive search
            row = conn.execute(
                "SELECT track_id, artist, title, cover_url FROM covers "
                "WHERE LOWER(artist) = LOWER(?) AND LOWER(title) = LOWER(?)",
                (artist, title)
            ).fetchone()
            
            if row and row["cover_url"]:
                return {
                    "track_id": row["track_id"],
                    "artist": row["artist"],
                    "title": row["title"],
                    "cover_url": row["cover_url"],
                    "source": "database"
                }
        
        return None
    
    def get_cover_by_track_id(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get cover URL directly by track ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT track_id, artist, title, cover_url FROM covers WHERE track_id = ?",
                (track_id,)
            ).fetchone()
            
            if row and row["cover_url"]:
                return {
                    "track_id": row["track_id"],
                    "artist": row["artist"],
                    "title": row["title"],
                    "cover_url": row["cover_url"],
                    "source": "database"
                }
        return None


# Global cover lookup instance
_cover_lookup: Optional[CoverArtLookup] = None


def get_cover_lookup(downloads_db_path: str) -> CoverArtLookup:
    """Get or create cover lookup instance."""
    global _cover_lookup
    if _cover_lookup is None:
        _cover_lookup = CoverArtLookup(downloads_db_path)
    return _cover_lookup


def add_cover_api_endpoints(app, downloads_db_path: str):
    """Add cover art API endpoints to FastAPI app."""
    
    @app.get("/api/cover")
    async def lookup_cover_art(
        artist: str = Query(..., description="Artist name"),
        title: str = Query(..., description="Track title"),
        album: str = Query("", description="Album name (optional)")
    ):
        """Look up cover art for a track by metadata."""
        try:
            artist = unquote(artist).strip()
            title = unquote(title).strip()
            album = unquote(album).strip() if album else ""
            
            if not artist or not title:
                raise HTTPException(status_code=400, detail="Artist and title are required")
            
            cover_lookup = get_cover_lookup(downloads_db_path)
            result = cover_lookup.lookup_cover_url(artist, title, album)
            
            if result and result.get('cover_url'):
                return {
                    "found": True,
                    "cover_url": result['cover_url'],
                    "track_id": result.get('track_id'),
                    "artist": result.get('artist'),
                    "title": result.get('title'),
                    "source": result.get('source', 'unknown')
                }
            else:
                return {
                    "found": False,
                    "message": f"No cover art found for '{artist} - {title}'"
                }
                
        except Exception as e:
            logger.error(f"Cover lookup error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during cover lookup")
    
    @app.get("/api/cover/by-id/{track_id}")
    async def lookup_cover_by_id(track_id: str):
        """Look up cover art directly by track ID."""
        try:
            cover_lookup = get_cover_lookup(downloads_db_path)
            result = cover_lookup.get_cover_by_track_id(track_id)
            
            if result and result.get('cover_url'):
                return {
                    "found": True,
                    "cover_url": result['cover_url'],
                    "track_id": result['track_id'],
                    "artist": result.get('artist'),
                    "title": result.get('title'),
                    "source": "database"
                }
            else:
                return {
                    "found": False,
                    "message": f"No cover art found for track ID {track_id}"
                }
                
        except Exception as e:
            logger.error(f"Cover lookup error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during cover lookup")
    
    @app.delete("/api/cover/cache")
    async def clear_cover_cache():
        """Clear the cover art cache."""
        try:
            cover_lookup = get_cover_lookup(downloads_db_path)
            
            with sqlite3.connect(cover_lookup.cache_db) as conn:
                conn.execute("DELETE FROM cover_cache")
                rows_deleted = conn.total_changes
            
            return {
                "success": True,
                "message": f"Cleared {rows_deleted} cached entries"
            }
            
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during cache clear")
    
    @app.get("/api/cover/stats")
    async def get_cover_stats():
        """Get cover art database statistics."""
        try:
            cover_lookup = get_cover_lookup(downloads_db_path)
            
            # Get stats from covers table
            with sqlite3.connect(cover_lookup.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                total_covers = conn.execute(
                    "SELECT COUNT(*) as count FROM covers"
                ).fetchone()['count']
                
                # Get some sample entries
                sample_entries = conn.execute(
                    "SELECT track_id, artist, title FROM covers LIMIT 5"
                ).fetchall()
                sample_list = [
                    {"track_id": row["track_id"], "artist": row["artist"], "title": row["title"]}
                    for row in sample_entries
                ]
            
            # Get cache stats
            with sqlite3.connect(cover_lookup.cache_db) as conn:
                conn.row_factory = sqlite3.Row
                
                cache_entries = conn.execute("SELECT COUNT(*) as count FROM cover_cache").fetchone()['count']
                
                with_covers = conn.execute(
                    "SELECT COUNT(*) as count FROM cover_cache WHERE cover_url IS NOT NULL"
                ).fetchone()['count']
                
                without_covers = cache_entries - with_covers
            
            return {
                "database_covers": total_covers,
                "sample_entries": sample_list,
                "cache_entries": cache_entries,
                "cache_with_covers": with_covers,
                "cache_without_covers": without_covers,
                "cache_hit_rate": f"{(with_covers/cache_entries*100):.1f}%" if cache_entries > 0 else "0%",
                "database_path": str(cover_lookup.db_path)
            }
            
        except Exception as e:
            logger.error(f"Stats error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error getting stats")