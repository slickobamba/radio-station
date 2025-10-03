class StreamripMonitor {
    constructor() {
        this.eventSource = null;
        this.playlists = new Map();
        this.tracks = new Map();
        this.isConnected = false;
        this.reconnectDelay = 1000;
        
        this.initElements();
        this.connect();
        this.setupForm();
    }

    initElements() {
        this.elements = {
            connectionDot: document.getElementById('connectionDot'),
            connectionStatus: document.getElementById('connectionStatus'),
            playlistsContainer: document.getElementById('playlistsContainer')
        };
    }

    setupForm() {
        document.getElementById('spotifyForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const formData = new FormData(e.target);
            const url = formData.get('url');
            
            if (!url) {
                this.showNotification('Please enter a spotify playlist URL', 'error');
                return;
            }
            
            const requestData = {
                url: url,
                source: formData.get('source') || null,
                fallback_source: formData.get('fallback_source') || null
            };
            
            try {
                const response = await fetch('/api/spotify', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(requestData)
                });
                
                const result = await response.json();
                
                if (response.ok) {
                    this.showNotification(`Last.fm download started: ${result.task_id}`, 'success');
                    e.target.reset();
                } else {
                    this.showNotification(`Error: ${result.error || 'Unknown error'}`, 'error');
                }
            } catch (error) {
                this.showNotification(`Network error: ${error.message}`, 'error');
            }
        });
    }

    showNotification(message, type) {
        const notification = document.getElementById('downloadNotification');
        notification.textContent = message;
        notification.className = `status-${type}`;
        notification.style.display = 'block';
        
        setTimeout(() => {
            notification.style.display = 'none';
        }, 5000);
    }

    connect() {
        let host, port;
        
        if (window.location.protocol === 'file:') {
            host = 'localhost';
            port = '8000';
        } else {
            host = window.location.hostname || 'localhost';
            port = window.location.port || '8000';
        }
        
        const url = `http://${host}:${port}/events`;
        console.log('Connecting to:', url);

        this.eventSource = new EventSource(url);

        this.eventSource.onopen = () => {
            this.isConnected = true;
            this.reconnectDelay = 1000;
            this.updateConnectionStatus(true);
        };

        this.eventSource.addEventListener('playlist_update', (event) => {
            const data = JSON.parse(event.data);
            this.handlePlaylistUpdate(data);
        });

        this.eventSource.addEventListener('track_update', (event) => {
            const data = JSON.parse(event.data);
            this.handleTrackUpdate(data);
        });

        this.eventSource.onerror = () => {
            this.isConnected = false;
            this.updateConnectionStatus(false);
            this.scheduleReconnect();
        };
    }

    scheduleReconnect() {
        setTimeout(() => {
            if (!this.isConnected) {
                this.connect();
                this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
            }
        }, this.reconnectDelay);
    }

    updateConnectionStatus(connected) {
        if (connected) {
            this.elements.connectionDot.classList.add('connected');
            this.elements.connectionStatus.textContent = 'Connected';
        } else {
            this.elements.connectionDot.classList.remove('connected');
            this.elements.connectionStatus.textContent = 'Disconnected';
        }
    }

    handlePlaylistUpdate(data) {
        this.playlists.set(data.playlist_id, data);
        this.updateDisplay();
    }

    handleTrackUpdate(data) {
        this.tracks.set(data.track_id, data);
        this.updateDisplay();
    }

    updateDisplay() {
        this.updatePlaylists();
    }

    updatePlaylists() {
        const playlists = Array.from(this.playlists.values());
        
        if (playlists.length === 0) {
            this.elements.playlistsContainer.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">♪</div>
                    <p>No active playlists</p>
                </div>
            `;
            return;
        }

        this.elements.playlistsContainer.innerHTML = playlists.map(playlist => {
            const progress = playlist.total_tracks > 0 ? 
                (playlist.completed_tracks / playlist.total_tracks) * 100 : 0;

            // Get tracks for this playlist
            const playlistTracks = Array.from(this.tracks.values())
                .filter(track => track.playlist_id === playlist.playlist_id)
                .sort((a, b) => a.title.localeCompare(b.title));

            return `
                <div class="playlist-container" style="margin-bottom: 20px;">
                    <div class="playlist-header">
                        <div class="playlist-title">${this.escapeHtml(playlist.playlist_name)}</div>
                        <div class="playlist-status status-${playlist.status}">${playlist.status}</div>
                    </div>
                    
                    <div class="playlist-progress">
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: ${progress}%"></div>
                        </div>
                        
                        <div class="playlist-stats">
                            <div class="playlist-stat">
                                <div class="playlist-stat-number">${playlist.total_tracks}</div>
                                <div class="playlist-stat-label">Total</div>
                            </div>
                            <div class="playlist-stat">
                                <div class="playlist-stat-number">${playlist.found_tracks}</div>
                                <div class="playlist-stat-label">Found</div>
                            </div>
                            <div class="playlist-stat">
                                <div class="playlist-stat-number">${playlist.completed_tracks}</div>
                                <div class="playlist-stat-label">Done</div>
                            </div>
                            <div class="playlist-stat">
                                <div class="playlist-stat-number">${playlist.failed_tracks}</div>
                                <div class="playlist-stat-label">Failed</div>
                            </div>
                        </div>
                    </div>
                    
                    ${playlistTracks.length > 0 ? `
                        <table class="tracks-table">
                            <thead>
                                <tr>
                                    <th>Title</th>
                                    <th>Artist</th>
                                    <th>Progress</th>
                                    <th>Status</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${playlistTracks.map(track => `
                                    <tr>
                                        <td class="track-title" title="${this.escapeHtml(track.title)}">${this.escapeHtml(track.title)}</td>
                                        <td class="track-artist" title="${this.escapeHtml(track.artist)}">${this.escapeHtml(track.artist)}</td>
                                        <td class="track-progress">
                                            ${track.status === 'downloading' ? `
                                                <div class="track-progress-bar">
                                                    <div class="track-progress-fill" style="width: ${track.progress}%"></div>
                                                </div>
                                                <div class="track-progress-text">${Math.round(track.progress)}%</div>
                                            ` : ''}
                                        </td>
                                        <td><span class="track-status status-${track.status}">${track.status}</span></td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    ` : ''}
                </div>
            `;
        }).join('');
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize when page loads
document.addEventListener('DOMContentLoaded', () => {
    new StreamripMonitor();
});