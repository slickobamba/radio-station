"""Cover art lookup API endpoint for radio player."""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import unquote

from fastapi import HTTPException, Query
from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TXXX
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

logger = logging.getLogger("streamrip.radio")


class CoverArtLookup:
    """Looks up cover art from streamrip downloaded files."""
    
    def __init__(self, downloads_folder: str):
        self.downloads_folder = Path(downloads_folder)
        self.cache_db = "cover_cache.db"
        self.init_cache_db()
    
    def init_cache_db(self):
        """Initialize SQLite cache for faster lookups."""
        with sqlite3.connect(self.cache_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cover_cache (
                    lookup_key TEXT PRIMARY KEY,
                    cover_url TEXT,
                    file_path TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lookup_key 
                ON cover_cache(lookup_key)
            """)
    
    def get_cache_key(self, artist: str, title: str, album: str = "") -> str:
        """Generate cache key for track lookup."""
        # Normalize for consistent lookups
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
        
        # Search in downloaded files
        result = self.search_in_files(artist, title, album)
        
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
                    "cover_url": row["cover_url"],
                    "file_path": row["file_path"],
                    "source": "cache"
                }
        return None
    
    def cache_result(self, cache_key: str, result: Optional[Dict[str, Any]]):
        """Cache lookup result."""
        with sqlite3.connect(self.cache_db) as conn:
            if result:
                conn.execute("""
                    INSERT OR REPLACE INTO cover_cache 
                    (lookup_key, cover_url, file_path)
                    VALUES (?, ?, ?)
                """, (cache_key, result.get("cover_url"), result.get("file_path")))
            else:
                # Cache negative results too (with empty values)
                conn.execute("""
                    INSERT OR REPLACE INTO cover_cache 
                    (lookup_key, cover_url, file_path)
                    VALUES (?, ?, ?)
                """, (cache_key, None, None))
    
    def search_in_files(self, artist: str, title: str, album: str = "") -> Optional[Dict[str, Any]]:
        """Search for matching audio files in downloads folder."""
        if not self.downloads_folder.exists():
            return None
        
        # Common audio file extensions
        audio_extensions = {'.flac', '.mp3', '.m4a', '.ogg', '.opus'}
        
        # Search strategies (in order of preference)
        search_strategies = [
            self._exact_match_search,
            self._fuzzy_match_search,
            self._partial_match_search
        ]
        
        for strategy in search_strategies:
            result = strategy(artist, title, album, audio_extensions)
            if result:
                return result
        
        return None
    
    def _exact_match_search(self, artist: str, title: str, album: str, extensions: set) -> Optional[Dict[str, Any]]:
        """Try exact filename matches."""
        # Common filename patterns from streamrip
        patterns = [
            f"*{artist} - {title}*",
            f"*{title}*{artist}*",
            f"*{artist}*{title}*"
        ]
        
        for pattern in patterns:
            for ext in extensions:
                for file_path in self.downloads_folder.rglob(f"{pattern}{ext}"):
                    cover_url = self._extract_cover_url(file_path)
                    if cover_url:
                        return {
                            "cover_url": cover_url,
                            "file_path": str(file_path),
                            "source": "exact_match"
                        }
        return None
    
    def _fuzzy_match_search(self, artist: str, title: str, album: str, extensions: set) -> Optional[Dict[str, Any]]:
        """Try fuzzy matching by checking file metadata."""
        # Search recent files first (more likely to be relevant)
        audio_files = []
        for ext in extensions:
            audio_files.extend(self.downloads_folder.rglob(f"*{ext}"))
        
        # Sort by modification time (newest first)
        audio_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        # Limit search to recent files for performance
        for file_path in audio_files[:500]:  # Check last 500 files
            try:
                metadata = self._read_file_metadata(file_path)
                if metadata and self._is_metadata_match(metadata, artist, title, album):
                    cover_url = self._extract_cover_url(file_path)
                    if cover_url:
                        return {
                            "cover_url": cover_url,
                            "file_path": str(file_path),
                            "source": "metadata_match"
                        }
            except Exception as e:
                logger.debug(f"Error reading metadata from {file_path}: {e}")
                continue
        
        return None
    
    def _partial_match_search(self, artist: str, title: str, album: str, extensions: set) -> Optional[Dict[str, Any]]:
        """Try partial string matching in filenames."""
        # Normalize search terms
        artist_norm = self._normalize_string(artist)
        title_norm = self._normalize_string(title)
        
        for ext in extensions:
            for file_path in self.downloads_folder.rglob(f"*{ext}"):
                filename_norm = self._normalize_string(file_path.stem)
                
                # Check if both artist and title appear in filename
                if (artist_norm in filename_norm and title_norm in filename_norm):
                    cover_url = self._extract_cover_url(file_path)
                    if cover_url:
                        return {
                            "cover_url": cover_url,
                            "file_path": str(file_path),
                            "source": "partial_match"
                        }
        
        return None
    
    def _normalize_string(self, s: str) -> str:
        """Normalize string for comparison."""
        return s.lower().replace(" ", "").replace("-", "").replace("_", "")
    
    def _read_file_metadata(self, file_path: Path) -> Optional[Dict[str, str]]:
        """Read metadata from audio file."""
        try:
            audio_file = MutagenFile(str(file_path))
            if not audio_file:
                return None
            
            # Extract common metadata fields
            metadata = {}
            
            if isinstance(audio_file, FLAC):
                metadata = {
                    'artist': str(audio_file.get('ARTIST', [''])[0]),
                    'title': str(audio_file.get('TITLE', [''])[0]),
                    'album': str(audio_file.get('ALBUM', [''])[0])
                }
            elif isinstance(audio_file, MP4):
                metadata = {
                    'artist': str(audio_file.get('\xa9ART', [''])[0]),
                    'title': str(audio_file.get('\xa9nam', [''])[0]),
                    'album': str(audio_file.get('\xa9alb', [''])[0])
                }
            elif isinstance(audio_file, ID3):
                metadata = {
                    'artist': str(audio_file.get('TPE1', '')),
                    'title': str(audio_file.get('TIT2', '')),
                    'album': str(audio_file.get('TALB', ''))
                }
            else:
                # Generic fallback
                metadata = {
                    'artist': str(audio_file.get('artist', [''])[0] if audio_file.get('artist') else ''),
                    'title': str(audio_file.get('title', [''])[0] if audio_file.get('title') else ''),
                    'album': str(audio_file.get('album', [''])[0] if audio_file.get('album') else '')
                }
            
            return metadata
            
        except Exception as e:
            logger.debug(f"Error reading metadata from {file_path}: {e}")
            return None
    
    def _is_metadata_match(self, metadata: Dict[str, str], artist: str, title: str, album: str) -> bool:
        """Check if file metadata matches search criteria."""
        def normalize_compare(a: str, b: str) -> bool:
            return self._normalize_string(a) == self._normalize_string(b)
        
        artist_match = normalize_compare(metadata.get('artist', ''), artist)
        title_match = normalize_compare(metadata.get('title', ''), title)
        
        # Require both artist and title to match
        if not (artist_match and title_match):
            return False
        
        # Album is optional but can help with disambiguation
        if album and metadata.get('album'):
            album_match = normalize_compare(metadata.get('album', ''), album)
            return album_match
        
        return True
    
    def _extract_cover_url(self, file_path: Path) -> Optional[str]:
        """Extract cover URL from audio file metadata."""
        try:
            audio_file = MutagenFile(str(file_path))
            if not audio_file:
                return None
            
            # Look for cover URLs in different formats
            cover_url = None
            
            if isinstance(audio_file, FLAC):
                # FLAC uses uppercase field names
                cover_url = (
                    audio_file.get('COVER_URL_ORIGINAL', [None])[0] or
                    audio_file.get('COVER_URL_LARGE', [None])[0] or
                    audio_file.get('COVER_URL_SMALL', [None])[0] or
                    audio_file.get('COVER_URL_THUMBNAIL', [None])[0]
                )
            
            elif isinstance(audio_file, MP4):
                # MP4 uses freeform fields
                cover_url = (
                    audio_file.get('----:com.apple.iTunes:COVER_URL_ORIGINAL') or
                    audio_file.get('----:com.apple.iTunes:COVER_URL_LARGE') or
                    audio_file.get('----:com.apple.iTunes:COVER_URL_SMALL') or
                    audio_file.get('----:com.apple.iTunes:COVER_URL_THUMBNAIL')
                )
                if cover_url:
                    # MP4 freeform values are bytes
                    cover_url = cover_url[0].decode('utf-8') if isinstance(cover_url[0], bytes) else str(cover_url[0])
            
            elif isinstance(audio_file, ID3):
                # MP3 uses TXXX frames
                for frame in audio_file.getall('TXXX'):
                    if frame.desc in ['COVER_URL_ORIGINAL', 'COVER_URL_LARGE', 'COVER_URL_SMALL', 'COVER_URL_THUMBNAIL']:
                        cover_url = str(frame.text[0]) if frame.text else None
                        break
            
            return cover_url if cover_url else None
            
        except Exception as e:
            logger.debug(f"Error extracting cover URL from {file_path}: {e}")
            return None


# Global cover lookup instance
_cover_lookup: Optional[CoverArtLookup] = None


def get_cover_lookup(downloads_folder: str) -> CoverArtLookup:
    """Get or create cover lookup instance."""
    global _cover_lookup
    if _cover_lookup is None:
        _cover_lookup = CoverArtLookup(downloads_folder)
    return _cover_lookup


def add_cover_api_endpoints(app, downloads_folder: str):
    """Add cover art API endpoints to FastAPI app."""
    
    @app.get("/api/cover")
    async def lookup_cover_art(
        artist: str = Query(..., description="Artist name"),
        title: str = Query(..., description="Track title"),
        album: str = Query("", description="Album name (optional)")
    ):
        """Look up cover art for a track."""
        try:
            # URL decode parameters
            artist = unquote(artist).strip()
            title = unquote(title).strip()
            album = unquote(album).strip() if album else ""
            
            if not artist or not title:
                raise HTTPException(status_code=400, detail="Artist and title are required")
            
            cover_lookup = get_cover_lookup(downloads_folder)
            result = cover_lookup.lookup_cover_url(artist, title, album)
            
            if result and result.get('cover_url'):
                return {
                    "found": True,
                    "cover_url": result['cover_url'],
                    "source": result.get('source', 'unknown'),
                    "file_path": result.get('file_path') if result.get('source') != 'cache' else None
                }
            else:
                return {
                    "found": False,
                    "message": f"No cover art found for '{artist} - {title}'"
                }
                
        except Exception as e:
            logger.error(f"Cover lookup error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during cover lookup")
    
    @app.get("/api/cover/search")
    async def search_cover_files(
        query: str = Query(..., description="Search query"),
        limit: int = Query(10, description="Max results")
    ):
        """Search for files containing cover art."""
        try:
            cover_lookup = get_cover_lookup(downloads_folder)
            
            # Search for files matching query
            results = []
            audio_extensions = {'.flac', '.mp3', '.m4a', '.ogg', '.opus'}
            
            query_norm = cover_lookup._normalize_string(query)
            
            for ext in audio_extensions:
                for file_path in cover_lookup.downloads_folder.rglob(f"*{ext}"):
                    if len(results) >= limit:
                        break
                    
                    filename_norm = cover_lookup._normalize_string(file_path.stem)
                    if query_norm in filename_norm:
                        cover_url = cover_lookup._extract_cover_url(file_path)
                        metadata = cover_lookup._read_file_metadata(file_path)
                        
                        results.append({
                            "file_path": str(file_path),
                            "filename": file_path.name,
                            "cover_url": cover_url,
                            "metadata": metadata
                        })
                
                if len(results) >= limit:
                    break
            
            return {
                "query": query,
                "results": results,
                "total": len(results)
            }
            
        except Exception as e:
            logger.error(f"Cover search error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error during cover search")
    
    @app.delete("/api/cover/cache")
    async def clear_cover_cache():
        """Clear the cover art cache."""
        try:
            cover_lookup = get_cover_lookup(downloads_folder)
            
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
        """Get cover art cache statistics."""
        try:
            cover_lookup = get_cover_lookup(downloads_folder)
            
            with sqlite3.connect(cover_lookup.cache_db) as conn:
                conn.row_factory = sqlite3.Row
                
                total_entries = conn.execute("SELECT COUNT(*) as count FROM cover_cache").fetchone()['count']
                
                with_covers = conn.execute(
                    "SELECT COUNT(*) as count FROM cover_cache WHERE cover_url IS NOT NULL"
                ).fetchone()['count']
                
                without_covers = total_entries - with_covers
            
            # Check downloads folder stats
            downloads_path = Path(downloads_folder)
            audio_files = 0
            if downloads_path.exists():
                audio_extensions = {'.flac', '.mp3', '.m4a', '.ogg', '.opus'}
                for ext in audio_extensions:
                    audio_files += len(list(downloads_path.rglob(f"*{ext}")))
            
            return {
                "cache_entries": total_entries,
                "with_covers": with_covers,
                "without_covers": without_covers,
                "audio_files_in_downloads": audio_files,
                "downloads_folder": str(downloads_path),
                "cache_hit_rate": f"{(with_covers/total_entries*100):.1f}%" if total_entries > 0 else "0%"
            }
            
        except Exception as e:
            logger.error(f"Stats error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error getting stats")