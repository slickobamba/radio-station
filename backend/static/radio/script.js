// Configuration
const settings = {
    'radio_name': 'Mama Radio',
    'icecast_url': 'http://159.65.37.196:8002',
    'stream_mount': '/mystream.ogg',
    'cover_api_url': 'http://159.65.37.196:8001/api/cover',
    'default_cover_art': 'data:image/svg+xml,%3Csvg width="320" height="320" xmlns="http://www.w3.org/2000/svg"%3E%3Crect width="320" height="320" fill="%231a1a1a"/%3E%3C/svg%3E'
};

const STREAM_URL = settings.icecast_url + settings.stream_mount;
let audio = new Audio(STREAM_URL);
let isPlaying = false;
let pollInterval = null;
let songHistory = [];
let currentTrack = null;

// DOM Elements
const btnPlay = document.getElementById('btnPlay');
const volumeSlider = document.getElementById('volume');
const volPercentage = document.getElementById('volPercentage');

// Play/Pause functionality
btnPlay.addEventListener('click', function() {
    if (isPlaying) {
        audio.pause();
        btnPlay.innerHTML = '<div class="icon-play"></div>';
        btnPlay.setAttribute('aria-label', 'Play');
        isPlaying = false;
    } else {
        audio.play();
        btnPlay.innerHTML = '<div class="icon-pause"></div>';
        btnPlay.setAttribute('aria-label', 'Pause');
        isPlaying = true;
    }
});

// Volume control
volumeSlider.addEventListener('input', function() {
    const volume = this.value / 100;
    audio.volume = volume;
    volPercentage.textContent = this.value;
});

// Initialize volume
audio.volume = 1;

// Fetch cover art from API
async function fetchCoverArt(artist, song) {
    try {
        const response = await fetch(`${settings.cover_api_url}?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(song)}`);
        const data = await response.json();
        
        if (data.found && data.cover_url) {
            return data.cover_url;
        }
    } catch (error) {
        console.error('Error fetching cover art:', error);
    }
    
    return settings.default_cover_art;
}

// Update current song display
function refreshCurrentSong(song, artist) {
    document.getElementById('currentSong').textContent = song;
    document.getElementById('currentArtist').textContent = artist;
}

// Update cover art
async function refreshCover(song, artist) {
    const coverArt = document.getElementById('currentCoverArt');
    const coverBackground = document.getElementById('bgCover');
    
    const coverUrl = await fetchCoverArt(artist, song);
    coverArt.style.backgroundImage = `url(${coverUrl})`;
    
    // Add animation
    coverArt.className = 'animated bounceInLeft';
    
    if (coverBackground) {
        coverBackground.style.backgroundImage = `url(${coverUrl})`;
    }
    
    setTimeout(() => {
        coverArt.className = '';
    }, 2000);
    
    // Update media session metadata if available
    if ('mediaSession' in navigator) {
        navigator.mediaSession.metadata = new MediaMetadata({
            title: song,
            artist: artist,
            artwork: [{
                src: coverUrl,
                sizes: '512x512',
                type: 'image/jpeg'
            }]
        });
    }
}

// Update historic songs
async function refreshHistoric(info, n) {
    if (!info || !info.song) return;

    const historicDivs = document.querySelectorAll('#historicSong article');
    const songNames = document.querySelectorAll('#historicSong article .song');
    const artistNames = document.querySelectorAll('#historicSong article .artist');
    const historicCovers = document.querySelectorAll('#historicSong article .cover-historic');

    if (!historicCovers[n]) return;

    const coverUrl = await fetchCoverArt(info.artist, info.song);
    historicCovers[n].style.backgroundImage = `url(${coverUrl})`;

    const music = info.song.replace(/&apos;/g, "'").replace(/&amp;/g, '&');
    const artist = info.artist.replace(/&apos;/g, "'").replace(/&amp;/g, '&');

    songNames[n].textContent = music;
    artistNames[n].textContent = artist;

    // Add animation
    historicDivs[n].classList.add('animated', 'slideInRight');

    setTimeout(() => {
        for (let j = 0; j < historicDivs.length; j++) {
            historicDivs[j].classList.remove('animated', 'slideInRight');
        }
    }, 2000);
}

// Get stream mountpoint from URL
function getStreamMountpoint() {
    try {
        const url = new URL(STREAM_URL);
        return url.pathname;
    } catch {
        return settings.stream_mount;
    }
}

// Parse Icecast JSON response
function parseIcecastMetadata(data) {
    try {
        if (!data.icestats || !data.icestats.source) {
            return null;
        }

        // Handle both single source and array of sources
        const sources = Array.isArray(data.icestats.source) 
            ? data.icestats.source 
            : [data.icestats.source];

        const mountpoint = getStreamMountpoint();
        
        // Find the source matching our mount point
        let source = null;
        for (let i = 0; i < sources.length; i++) {
            if (sources[i].listenurl && sources[i].listenurl.includes(mountpoint)) {
                source = sources[i];
                break;
            }
        }

        if (!source) {
            // If no match, use first source
            source = sources[0];
        }

        const artist = source.artist ? source.artist.trim() : 'Unknown Artist';
        const title = source.title ? source.title.trim() : 'Unknown Title';

        if (!title) return null;

        return {
            artist: artist,
            title: title,
            album: source.album || '',
            listeners: parseInt(source.listeners || 0),
            bitrate: source.bitrate || '',
            server_name: source.server_name || '',
            server_description: source.server_description || ''
        };
    } catch (error) {
        console.error('Error parsing Icecast metadata:', error);
        return null;
    }
}

// Add track to history
function addToHistory(artist, title) {
    const trackId = artist.toLowerCase() + '|' + title.toLowerCase();
    
    // Check if this track is already at the top of history
    if (songHistory.length > 0) {
        const lastTrackId = songHistory[0].artist.toLowerCase() + '|' + songHistory[0].title.toLowerCase();
        if (lastTrackId === trackId) {
            return; // Don't add duplicates
        }
    }
    
    // Add to beginning of history
    songHistory.unshift({
        artist: artist,
        title: title
    });
    
    // Keep only last 20 tracks
    if (songHistory.length > 20) {
        songHistory = songHistory.slice(0, 20);
    }
}

// Handle metadata update
async function handleMetadataUpdate(metadata) {
    const trackId = metadata.artist.toLowerCase() + '|' + metadata.title.toLowerCase();
    const isNewTrack = !currentTrack || currentTrack.id !== trackId;

    if (isNewTrack) {
        // Add current track to history before switching
        if (currentTrack) {
            addToHistory(currentTrack.artist, currentTrack.title);
        }
        
        currentTrack = {
            id: trackId,
            artist: metadata.artist,
            title: metadata.title
        };
        
        // Format characters to UTF-8
        const song = metadata.title.replace(/&apos;/g, "'").replace(/&amp;/g, '&');
        const artist = metadata.artist.replace(/&apos;/g, "'").replace(/&amp;/g, '&').replace('  ', ' ');

        // Update title
        document.title = `${song} - ${artist} | ${settings.radio_name}`;

        // Update UI
        refreshCover(song, artist);
        refreshCurrentSong(song, artist);

        // Update recently played (show last 2 tracks from history)
        for (let i = 0; i < 2 && i < songHistory.length; i++) {
            refreshHistoric({
                artist: songHistory[i].artist,
                song: songHistory[i].title
            }, i);
        }
    }
}

// Poll Icecast metadata
async function pollMetadata() {
    try {
        const response = await fetch(settings.icecast_url + '/status-json.xsl');
        
        if (!response.ok) {
            throw new Error('Failed to fetch stream metadata');
        }

        const data = await response.json();
        const metadata = parseIcecastMetadata(data);
        
        if (metadata) {
            await handleMetadataUpdate(metadata);
        }
    } catch (error) {
        console.error('Metadata polling error:', error);
    }
}

// Start metadata polling
function startPolling() {
    pollMetadata(); // Initial poll
    pollInterval = setInterval(pollMetadata, 5000); // Poll every 5 seconds
}

// Initialize on page load
window.addEventListener('DOMContentLoaded', function() {
    // Start polling for metadata
    startPolling();
    
    // Set cover album aspect ratio (square)
    const coverAlbum = document.querySelector('.cover-album');
    if (coverAlbum) {
        coverAlbum.style.height = coverAlbum.offsetWidth + 'px';
    }
});