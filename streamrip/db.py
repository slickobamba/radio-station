"""Wrapper over a database that stores item IDs."""

import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger("streamrip")


class DatabaseInterface(ABC):
    @abstractmethod
    def create(self):
        pass

    @abstractmethod
    def contains(self, **items) -> bool:
        pass

    @abstractmethod
    def add(self, kvs):
        pass

    @abstractmethod
    def remove(self, kvs):
        pass

    @abstractmethod
    def all(self) -> list:
        pass


class Dummy(DatabaseInterface):
    """This exists as a mock to use in case databases are disabled."""

    def create(self):
        pass

    def contains(self, **_):
        return False

    def add(self, *_):
        pass

    def remove(self, *_):
        pass

    def all(self):
        return []


class DatabaseBase(DatabaseInterface):
    """A wrapper for an sqlite database."""

    structure: dict
    name: str

    def __init__(self, path: str):
        """Create a Database instance.

        :param path: Path to the database file.
        """
        assert self.structure != {}
        assert self.name
        assert path

        self.path = path

        # Create table if it doesn't exist (not just if file doesn't exist)
        if not self._table_exists():
            self.create()
    
    def _table_exists(self) -> bool:
        """Check if this table exists in the database."""
        if not os.path.exists(self.path):
            return False
        
        try:
            with sqlite3.connect(self.path) as conn:
                result = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (self.name,)
                ).fetchone()
                return result is not None
        except sqlite3.Error:
            return False

    def create(self):
        """Create a database."""
        with sqlite3.connect(self.path) as conn:
            params = ", ".join(
                f"{key} {' '.join(map(str.upper, props))} NOT NULL"
                for key, props in self.structure.items()
            )
            command = f"CREATE TABLE {self.name} ({params})"

            logger.debug("executing %s", command)

            conn.execute(command)

    def keys(self):
        """Get the column names of the table."""
        return self.structure.keys()

    def contains(self, **items) -> bool:
        """Check whether items matches an entry in the table.

        :param items: a dict of column-name + expected value
        :rtype: bool
        """
        allowed_keys = set(self.structure.keys())
        assert all(
            key in allowed_keys for key in items.keys()
        ), f"Invalid key. Valid keys: {allowed_keys}"

        items = {k: str(v) for k, v in items.items()}

        with sqlite3.connect(self.path) as conn:
            conditions = " AND ".join(f"{key}=?" for key in items.keys())
            command = f"SELECT EXISTS(SELECT 1 FROM {self.name} WHERE {conditions})"

            logger.debug("Executing %s", command)

            return bool(conn.execute(command, tuple(items.values())).fetchone()[0])

    def add(self, items: tuple[str]):
        """Add a row to the table.

        :param items: Column-name + value. Values must be provided for all cols.
        :type items: Tuple[str]
        """
        assert len(items) == len(self.structure)

        params = ", ".join(self.structure.keys())
        question_marks = ", ".join("?" for _ in items)
        command = f"INSERT INTO {self.name} ({params}) VALUES ({question_marks})"

        logger.debug("Executing %s", command)
        logger.debug("Items to add: %s", items)

        with sqlite3.connect(self.path) as conn:
            try:
                conn.execute(command, tuple(items))
            except sqlite3.IntegrityError as e:
                # tried to insert an item that was already there
                logger.debug(e)

    def remove(self, **items):
        """Remove items from a table.

        Warning: NOT TESTED!

        :param items:
        """
        conditions = " AND ".join(f"{key}=?" for key in items.keys())
        command = f"DELETE FROM {self.name} WHERE {conditions}"

        with sqlite3.connect(self.path) as conn:
            logger.debug(command)
            conn.execute(command, tuple(items.values()))

    def all(self):
        """Iterate through the rows of the table."""
        with sqlite3.connect(self.path) as conn:
            return list(conn.execute(f"SELECT * FROM {self.name}"))

    def reset(self):
        """Delete the database file."""
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


class Downloads(DatabaseBase):
    """A table that stores the downloaded IDs."""

    name = "downloads"
    structure: Final[dict] = {
        "id": ["text", "unique"],
    }


class Failed(DatabaseBase):
    """A table that stores information about failed downloads."""

    name = "failed_downloads"
    structure: Final[dict] = {
        "source": ["text"],
        "media_type": ["text"],
        "id": ["text", "unique"],
    }


class Covers(DatabaseBase):
    """A table that stores cover URLs for downloaded tracks."""

    name = "covers"
    structure: Final[dict] = {
        "track_id": ["text", "primary key"],
        "artist": ["text"],
        "title": ["text"],
        "cover_url": ["text"],
    }

    def create(self):
        """Create the covers table with indices."""
        with sqlite3.connect(self.path) as conn:
            params = ", ".join(
                f"{key} {' '.join(map(str.upper, props))} NOT NULL"
                for key, props in self.structure.items()
            )
            command = f"CREATE TABLE {self.name} ({params})"
            
            logger.debug("executing %s", command)
            conn.execute(command)
            
            # Create index for artist/title searches
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_artist_title ON {self.name}(artist, title)"
            )
            logger.debug("Created index idx_artist_title on covers table")

    def get_cover_url(self, track_id: str) -> str | None:
        """Get cover URL for a track ID.
        
        :param track_id: Track ID to lookup
        :return: Cover URL if found, None otherwise
        """
        with sqlite3.connect(self.path) as conn:
            result = conn.execute(
                f"SELECT cover_url FROM {self.name} WHERE track_id = ?",
                (track_id,)
            ).fetchone()
            return result[0] if result else None

    def get_cover_by_metadata(self, artist: str, title: str) -> tuple[str, str, str] | None:
        """Get track_id and cover URL by artist and title.
        
        :param artist: Artist name
        :param title: Track title
        :return: Tuple of (track_id, artist, title, cover_url) if found, None otherwise
        """
        with sqlite3.connect(self.path) as conn:
            # Case-insensitive search
            result = conn.execute(
                f"SELECT track_id, artist, title, cover_url FROM {self.name} "
                f"WHERE LOWER(artist) = LOWER(?) AND LOWER(title) = LOWER(?)",
                (artist, title)
            ).fetchone()
            return result if result else None

    def add_cover(self, track_id: str, artist: str, title: str, cover_url: str):
        """Add or update a cover URL for a track.
        
        :param track_id: Track ID
        :param artist: Artist name
        :param title: Track title
        :param cover_url: Cover URL
        """
        with sqlite3.connect(self.path) as conn:
            # Use INSERT OR REPLACE to handle updates
            conn.execute(
                f"INSERT OR REPLACE INTO {self.name} (track_id, artist, title, cover_url) VALUES (?, ?, ?, ?)",
                (track_id, artist, title, cover_url)
            )
            logger.debug("Added cover URL for track %s (%s - %s): %s", track_id, artist, title, cover_url)


@dataclass(slots=True)
class Database:
    downloads: DatabaseInterface
    failed: DatabaseInterface
    covers: DatabaseInterface

    def downloaded(self, item_id: str) -> bool:
        return self.downloads.contains(id=item_id)

    def set_downloaded(self, item_id: str):
        self.downloads.add((item_id,))

    def get_failed_downloads(self) -> list[tuple[str, str, str]]:
        return self.failed.all()

    def set_failed(self, source: str, media_type: str, id: str):
        self.failed.add((source, media_type, id))

    def get_cover_url(self, track_id: str) -> str | None:
        """Get cover URL for a track."""
        if isinstance(self.covers, Covers):
            return self.covers.get_cover_url(track_id)
        return None

    def get_cover_by_metadata(self, artist: str, title: str) -> tuple[str, str, str, str] | None:
        """Get cover info by artist and title."""
        if isinstance(self.covers, Covers):
            return self.covers.get_cover_by_metadata(artist, title)
        return None

    def set_cover_url(self, track_id: str, artist: str, title: str, cover_url: str):
        """Set cover URL for a track."""
        if isinstance(self.covers, Covers):
            self.covers.add_cover(track_id, artist, title, cover_url)