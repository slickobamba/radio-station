"""
Last.fm Spotify Playlist Importer
Simple CLI tool to import Spotify playlists to Last.fm

Usage: python lastfm_import.py <spotify_url>
"""

import requests
import re
import time
import sys

# CONFIG - Fill in your credentials here
LASTFM_USERNAME = "streamrip"
LASTFM_SESSIONID = ".eJyVVMmSozgQ_Ref2y4Qi01F9MFmsaEMmMUGc3EIgdmXZjFLx_z7iKqamOU2BwUZypfvJU9S_l49YN_Fj74Nm0cM23j1vgrCJ-zzbvVj1YZtm1TlIwnwNgkBZINnsOYICNb0kw3X8Mlu18h_hlzABJRPbXHNP_g-y97f36-WaMoCDkiS2pIMvf0PzocoC8sFnD-LzRD6m2EYNrCu280C2nznN9emPXxDcX3dNy0uUW1E6rYxqoJBqQIateHnzyXdfqXDSYn9I0r0RLGus0xqidzKRVd7vMzir2LftEswKdwGA2tEqQuwCk7mgObqdaa0HFFa7QO694p88hwlRuDae4B7eWDMz4X28i2uPjtjjCiz8C0yDlyzkpMhubu3TE6rBFpke3c1wiuIDu8RkF8akMjgGD-DY955lsz-6x-sIQlcrJtzX02mIq2mMqWl-1FNEcbsZ22OCDU1Bs3OJl1QcS5jtPlO45jRcX7RR9QtWfRVO5pwHVAFcdJmcVAxXheMSRP2WFOm1RkB3c4wR4TjjMb55Mwr9d01Ej0VKaxBqPaewLW47xuN-M9-MZ-KV_TZr-eQMXSGxbvMc8bEcxXiXCgvz4k743jLfIfrvZOX-6VJouI2f_m2-HDr7k7eupSXoyIvoLN4Vhd3Z5y9hfdvzxO9bBPomAtHjX3L0SSz-Mxy35FqH_fkTP9HWwmemB-BvPSxz3gNqODAwo8S7Hl5wOep4RutZIuXwfHW3x2GWPwMXGU-O8v5RR06YR2ezHzKHDVL4Z7GRhJanZ2OSc2LRUW0wvpSE5YnTRLQ_FJ48lI1vj4ALFHml7ByJLNb83ETCZcaPe5lRCfymTAa_xKbXHXkaOdjTmmJ7kQUznepzcE21mYhkue5VsDHJXkWtR1VMWOW0ocbzjq49CLZ-XZ1pa_niNBUUX_1biq6XF1wfiySXlif1kahnmrNuEtga_mPaxSw4JEXD4IfUJfJ0Lx2kXZgfqm7HdPCI3nfi_UuPETwBEWDQvvdy-KD48n0UqZLlSOTpvLl4Dae7vc1etn6CIKzwLB8QY1jcgsFbeavnAVZXzRAhx-JFYa8cLIJunHTrrk9fOtXd2FMU8L3nmbQeSJM6zBX8NWEzpijUtgvD7r7fNBln-c_Vjh8NGH0PZh2AGxZggRbkiCo7Y4GNMFwYEdtWY7bsrsdxe0IClPUOZzypO2SMvqcObj2bRlAb23XhLBokvrtL0j7RtLUjibxqPrjT9pnuWQ:1v4JzF:jzEn3HFoFuybRdPjtKtlXq-TBpbwK7LHdGubVRbtphQ"
LASTFM_CSRFTOKEN = "s8mVwF3FbfkLLO7cUFDc6yw5qo5u16B6"

def import_playlist(spotify_url, sessionid, csrftoken, username, max_attempts=120):
    session = requests.Session()
    session.cookies.set('sessionid', sessionid, domain='.last.fm')
    session.cookies.set('csrftoken', csrftoken, domain='.last.fm')
    
    headers = {
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'x-requested-with': 'XMLHttpRequest',
        'referer': f'https://www.last.fm/user/{username}/playlists',
        'origin': 'https://www.last.fm',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    # Initiate import
    print(f"Importing: {spotify_url}")
    response = session.post(
        f"https://www.last.fm/user/{username}/playlists/import",
        headers=headers,
        data={'uri': spotify_url, 'csrfmiddlewaretoken': csrftoken, 'ajax': '1'},
        timeout=10
    )
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        if response.status_code == 403:
            print("   CSRF token or session may be invalid/expired")
        print(f"   Response: {response.text[:200]}")
        return None
    
    job_id_match = re.search(r'job-id=([a-f0-9-]+)', response.text)
    if not job_id_match:
        print(f"No job ID found in response\n   Response: {response.text[:200]}")
        return None
    
    job_id = job_id_match.group(1)
    print(f"Import started (job: {job_id})")
    
    status_url = f"https://www.last.fm/user/{username}/playlists/import"
    params = {'job-id': job_id, 'ajax': '1'}
    
    # Poll for completion with adaptive intervals
    for attempt in range(max_attempts):
        # Exponential backoff: start at 0.2s, cap at 1s
        interval = min(0.2 * (1.5 ** attempt), 1.0)
        
        try:
            response = session.get(status_url, headers=headers, params=params, timeout=5)
        except requests.exceptions.Timeout:
            print(f"Request timeout on attempt {attempt + 1}")
            continue
        
        if response.status_code != 200:
            continue
        
        if 'Import Complete' in response.text or 'finished importing' in response.text:
            playlist_id_match = re.search(r'href="/user/[^/]+/playlists/(\d+)"', response.text)
            
            if playlist_id_match:
                playlist_id = playlist_id_match.group(1)
                print(f"URL: https://www.last.fm/user/{username}/playlists/{playlist_id}")
                return playlist_id
            
            print("\nImport complete but couldn't extract playlist ID")
            return None
        
        print(f"Importing{'.' * (attempt % 4):<3} ({attempt + 1})", end='\r')

        time.sleep(interval)
    
    print("\nTimeout: Import took longer than expected")
    return None

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    spotify_url = sys.argv[1]
    
    if 'spotify.com/playlist/' not in spotify_url:
        print("Invalid Spotify URL (should contain 'spotify.com/playlist/')")
        sys.exit(1)
    
    playlist_id = import_playlist(spotify_url, LASTFM_SESSIONID, LASTFM_CSRFTOKEN, LASTFM_USERNAME)
    sys.exit(0 if playlist_id else 1)

if __name__ == "__main__":
    main()