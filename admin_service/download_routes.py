"""Download API routes for Last.fm playlist downloads."""

import asyncio
import logging
import uuid
import httpx
import re
import time
from typing import Dict, Optional
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("streamrip.admin")

# Track active download tasks
active_downloads: Dict[str, asyncio.Task] = {}

# Last.fm credentials - configure these
LASTFM_USERNAME = "streamrip"
LASTFM_SESSIONID = ".eJyVVMmSozgQ_Ref2y4Qi01F9MFmsaEMmMUGc3EIgdmXZjFLx_z7iKqamOU2BwUZypfvJU9S_l49YN_Fj74Nm0cM23j1vgrCJ-zzbvVj1YZtm1TlIwnwNgkBZINnsOYICNb0kw3X8Mlu18h_hlzABJRPbXHNP_g-y97f36-WaMoCDkiS2pIMvf0PzocoC8sFnD-LzRD6m2EYNrCu280C2nznN9emPXxDcX3dNy0uUW1E6rYxqoJBqQIateHnzyXdfqXDSYn9I0r0RLGus0xqidzKRVd7vMzir2LftEswKdwGA2tEqQuwCk7mgObqdaa0HFFa7QO694p88hwlRuDae4B7eWDMz4X28i2uPjtjjCiz8C0yDlyzkpMhubu3TE6rBFpke3c1wiuIDu8RkF8akMjgGD-DY955lsz-6x-sIQlcrJtzX02mIq2mMqWl-1FNEcbsZ22OCDU1Bs3OJl1QcS5jtPlO45jRcX7RR9QtWfRVO5pwHVAFcdJmcVAxXheMSRP2WFOm1RkB3c4wR4TjjMb55Mwr9d01Ej0VKaxBqPaewLW47xuN-M9-MZ-KV_TZr-eQMXSGxbvMc8bEcxXiXCgvz4k743jLfIfrvZOX-6VJouI2f_m2-HDr7k7eupSXoyIvoLN4Vhd3Z5y9hfdvzxO9bBPomAtHjX3L0SSz-Mxy35FqH_fkTP9HWwmemB-BvPSxz3gNqODAwo8S7Hl5wOep4RutZIuXwfHW3x2GWPwMXGU-O8v5RR06YR2ezHzKHDVL4Z7GRhJanZ2OSc2LRUW0wvpSE5YnTRLQ_FJ48lI1vj4ALFHml7ByJLNb83ETCZcaPe5lRCfymTAa_xKbXHXkaOdjTmmJ7kQUznepzcE21mYhkue5VsDHJXkWtR1VMWOW0ocbzjq49CLZ-XZ1pa_niNBUUX_1biq6XF1wfiySXlif1kahnmrNuEtga_mPaxSw4JEXD4IfUJfJ0Lx2kXZgfqm7HdPCI3nfi_UuPETwBEWDQvvdy-KD48n0UqZLlSOTpvLl4Dae7vc1etn6CIKzwLB8QY1jcgsFbeavnAVZXzRAhx-JFYa8cLIJunHTrrk9fOtXd2FMU8L3nmbQeSJM6zBX8NWEzpijUtgvD7r7fNBln-c_Vjh8NGH0PZh2AGxZggRbkiCo7Y4GNMFwYEdtWY7bsrsdxe0IClPUOZzypO2SMvqcObj2bRlAb23XhLBokvrtL0j7RtLUjibxqPrjT9pnuWQ:1v4JzF:jzEn3HFoFuybRdPjtKtlXq-TBpbwK7LHdGubVRbtphQ"
LASTFM_CSRFTOKEN = "s8mVwF3FbfkLLO7cUFDc6yw5qo5u16B6"


class SpotifyRequest(BaseModel):
    url: str
    source: Optional[str] = None
    fallback_source: Optional[str] = None

async def import_spotify_to_lastfm(
    spotify_url: str,
    sessionid: str,
    csrftoken: str,
    username: str,
    max_attempts: int = 120
) -> Optional[str]:

    cookies = {
        'sessionid': sessionid,
        'csrftoken': csrftoken
    }
    
    headers = {
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'x-requested-with': 'XMLHttpRequest',
        'referer': f'https://www.last.fm/user/{username}/playlists',
        'origin': 'https://www.last.fm',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    async with httpx.AsyncClient(cookies=cookies, headers=headers) as client:
        
        logger.info(f"Importing Spotify playlist: {spotify_url}")
        
        try:
            response = await client.post(
                f"https://www.last.fm/user/{username}/playlists/import",
                data={'uri': spotify_url, 'csrfmiddlewaretoken': csrftoken, 'ajax': '1'},
                timeout=10.0
            )
            
            if response.status_code != 200:
                logger.error(f"Import failed with status {response.status_code}: {response.text[:200]}")
                if response.status_code == 403:
                    logger.error("CSRF token or session may be invalid/expired")
                return None
            
            job_id_match = re.search(r'job-id=([a-f0-9-]+)', response.text)
            
            if not job_id_match:
                logger.error(f"No job ID found in response: {response.text[:200]}")
                return None
            
            job_id = job_id_match.group(1)
            logger.info(f"Import started (job: {job_id})")


        except httpx.TimeoutException:
            logger.error("Import initiation timed out")
            return None

        # Poll for completion
        status_url = f"https://www.last.fm/user/{username}/playlists/import"
        params = {'job-id': job_id, 'ajax': '1'}
        
        for attempt in range(max_attempts):
            
            try:
                response = await client.get(status_url, params=params, timeout=5.0)
                
                if response.status_code != 200:
                    continue
                
                response_text = response.text
                
                if 'Import Complete' in response_text or 'finished importing' in response_text:

                    playlist_id_match = re.search(r'href="/user/[^/]+/playlists/(\d+)"',response_text)
                    
                    if playlist_id_match:
                        playlist_id = playlist_id_match.group(1)
                        lastfm_url = f"https://www.last.fm/user/{username}/playlists/{playlist_id}"
                        logger.info(f"Import complete: {lastfm_url}")
                        return lastfm_url
                    
                    logger.warning("Import complete but couldn't extract playlist ID")
                    return None
                
                logger.debug(f"Polling attempt {attempt + 1}/{max_attempts}")
                
            except httpx.TimeoutException:
                logger.warning(f"Poll timeout on attempt {attempt + 1}")
                continue

            if attempt < max_attempts - 1:
                interval = min(0.2 * (1.5 ** attempt), 1.0)
                await asyncio.sleep(interval)

        logger.error("Import timeout: took longer than expected")
        return None

def create_download_router() -> APIRouter:
    """Create the download API router."""
    router = APIRouter(prefix="/api")

    @router.post("/spotify")
    async def submit_spotify_download(request: SpotifyRequest):

        task_id = str(uuid.uuid4())
        
        # Import here to avoid circular imports
        from streamrip.rip.main import Main
        from streamrip.config import Config, DEFAULT_CONFIG_PATH
        
        async def execute_download():
            try:

                # Convert Spotify URL to last.fm
                spotify_url = request.url
                
                lastfm_url = await import_spotify_to_lastfm(
                    request.url,
                    LASTFM_SESSIONID,
                    LASTFM_CSRFTOKEN,
                    LASTFM_USERNAME
                )
                
                if not lastfm_url:
                    logger.error(f"Failed to import Spotify playlist to Last.fm")
                    return
                    
                logger.error(f"Using Last.fm URL: {lastfm_url}")
                
                # Load config
                config = Config(DEFAULT_CONFIG_PATH)
                
                # Apply lastfm source overrides if provided
                if request.source:
                    config.session.lastfm.source = request.source
                if request.fallback_source:
                    config.session.lastfm.fallback_source = request.fallback_source
                
                async with Main(config) as main:
                    await main.resolve_lastfm(lastfm_url)
                    await main.rip()
                    
            except Exception as e:
                logger.error(f"Last.fm download task {task_id} failed: {e}")
            finally:
                active_downloads.pop(task_id, None)
        
        # Start background task
        task = asyncio.create_task(execute_download())
        active_downloads[task_id] = task
        
        return {"task_id": task_id, "status": "started"}

    @router.get("/downloads")
    async def list_downloads():
        """List active downloads."""
        from .sse_manager import sse_manager
        
        return {
            "active": list(active_downloads.keys()),
            "total_clients": len(sse_manager.clients),
            "total_playlists": len(sse_manager.playlists),
            "total_tracks": len(sse_manager.tracks)
        }

    return router