class VideoPlayer {
    constructor(videoElementId, options = {}) {
        this.video = document.getElementById(videoElementId);
        this.overlay = options.overlayId ? document.getElementById(options.overlayId) : null;
        this.hls = null;
        this.streamId = null;
        this.duration = null;
        
        // State
        this.isUserSeeking = false;
        this.isInternalSeeking = false;
        this.hasStartedPlaying = false;
        this.isDestroyed = false;
        this.lastKnownPosition = 0;
        this.wakeLock = null;
        
        // Callbacks/Hooks
        this.hooks = {
            onPlay: [],
            onPause: [],
            onEnded: [],
            onError: [],
            onFatalError: [],
            onProgress: [],
            onTimeUpdate: []
        };

        // Timers
        this.pausedOverlayTimer = null;
        this._setupBaseEvents();
        this._setupKeyboardControls();
    }

    on(event, callback) {
        if (this.hooks[event]) {
            this.hooks[event].push(callback);
        }
    }

    _trigger(event, ...args) {
        if (this.hooks[event]) {
            this.hooks[event].forEach(cb => cb(...args));
        }
    }

    _setupBaseEvents() {
        this.video.addEventListener("play", () => {
            this.hasStartedPlaying = true;
            this._handlePlay();
            this._trigger('onPlay');
            this.requestWakeLock();
        });

        this.video.addEventListener("pause", () => {
            this._handlePause();
            this._trigger('onPause');
            this.releaseWakeLock();
        });

        this.video.addEventListener("ended", () => {
            this._trigger('onEnded');
            this.destroy();
        });

        this.video.addEventListener("timeupdate", () => {
            if (!this.isUserSeeking) {
                const currentTime = this.video.currentTime;
                // Ignore micro-adjustments from hls.js (less than 1s jump)
                if (Math.abs(currentTime - this.lastKnownPosition) >= 1) {
                    this.lastKnownPosition = currentTime;
                    this._trigger('onTimeUpdate', currentTime);
                }
            }
        });

        this.video.addEventListener("seeking", async () => {
            if (this.isUserSeeking || this.isInternalSeeking) return;
            
            const seekTo = this.video.currentTime;
            // Block seeking if playback hasn't started or it's a near-zero seek (HLS init noise)
            if (!this.hasStartedPlaying || seekTo < 2) return;

            console.log(`[Player] Manual seek detected: ${this.lastKnownPosition}s -> ${seekTo}s`);
            await this.smartSeek(seekTo);
        });

        this.video.addEventListener("error", (e) => {
            const error = this.video.error;
            console.error("[Player] Video element error:", error);
            if (error && (error.code === 3 || error.code === 4)) {
                this._trigger('onFatalError', error);
            } else {
                this._trigger('onError', error);
            }
        });
    }

    _setupKeyboardControls() {
        const handleKeyDown = (e) => {
            if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') return;

            switch (e.key) {
                case ' ':
                case 'k':
                    e.preventDefault();
                    if (this.video.paused) this.video.play();
                    else this.video.pause();
                    break;
                case 'ArrowRight':
                case 'l':
                    e.preventDefault();
                    this.smartSeek(this.video.currentTime + 10);
                    break;
                case 'ArrowLeft':
                case 'j':
                    e.preventDefault();
                    this.smartSeek(Math.max(0, this.video.currentTime - 10));
                    break;
                case 'f':
                    e.preventDefault();
                    if (document.fullscreenElement) document.exitFullscreen();
                    else this.video.parentElement.requestFullscreen();
                    break;
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        this._cleanupKeydown = () => window.removeEventListener('keydown', handleKeyDown);
    }

    _handlePlay() {
        if (this.pausedOverlayTimer) {
            clearTimeout(this.pausedOverlayTimer);
            this.pausedOverlayTimer = null;
        }
        if (this.overlay) {
            this.overlay.classList.add('opacity-0');
            this.overlay.classList.remove('opacity-100');
            setTimeout(() => {
                if (!this.video.paused) this.overlay.style.display = 'none';
            }, 500);
        }
    }

    _handlePause() {
        if (this.pausedOverlayTimer) clearTimeout(this.pausedOverlayTimer);
        this.pausedOverlayTimer = setTimeout(() => {
            if (this.video.paused && this.overlay) {
                this.overlay.style.display = 'block';
                // Trigger reflow
                this.overlay.offsetHeight;
                this.overlay.classList.remove('opacity-0');
                this.overlay.classList.add('opacity-100');
            }
        }, 3000);
    }

    async requestWakeLock() {
        if ("wakeLock" in navigator && document.visibilityState === "visible") {
            try {
                this.wakeLock = await navigator.wakeLock.request("screen");
            } catch (err) { }
        }
    }

    async releaseWakeLock() {
        if (this.wakeLock) {
            try {
                await this.wakeLock.release();
                this.wakeLock = null;
            } catch (err) { }
        }
    }

    async load(apiUrl, startTime = 0) {
        const data = await fetch(apiUrl).then(r => r.json());
        if (this.isDestroyed) return;
        
        this.streamId = data.stream_id;
        this.duration = data.probe_info?.duration 
            ? parseFloat(data.probe_info.duration) 
            : null;
        this._loadHls(data.url, startTime);
    }

    _applyDuration() {
        if (!this.duration || !this.hls || !this.hls.levels) return;
        this.hls.levels.forEach(level => {
            if (level.details && level.details.live) level.details.totalduration = this.duration;
        });
    }

    _loadHls(hlsUrl, startTime = 0) {
        if (this.hls) this.hls.destroy();
        
        this.hls = new Hls({ 
            maxBufferLength: 30,
            maxMaxBufferLength: 60,
            liveDurationIntersection: false,
            enableSafeDuration: true
        });
        
        this.hls.loadSource(hlsUrl);
        this.hls.attachMedia(this.video);

        this.hls.on(Hls.Events.MANIFEST_PARSED, () => {
            this._applyDuration();
            this.video.currentTime = startTime;
            this.video.play();
        });

        this.hls.on(Hls.Events.LEVEL_LOADED, (event, data) => {
            if (this.duration && data.details && data.details.live) data.details.totalduration = this.duration;
        });

        this.hls.on(Hls.Events.LEVEL_UPDATED, (event, data) => {
            if (this.duration && data.details && data.details.live) data.details.totalduration = this.duration;
        });

        this.hls.on(Hls.Events.ERROR, (event, data) => {
            if (data.fatal) {
                console.error("[Player] HLS fatal error", data);
                if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                    this.hls.recoverMediaError();
                } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                    this.hls.startLoad();
                } else {
                    this._trigger('onFatalError', data);
                }
            }
        });
    }

    async smartSeek(position) {
        if (!this.streamId) return;
        this.isUserSeeking = true;

        try {
            const resp = await fetch(`/api/stream/transcode/${this.streamId}/segment-range`);
            if (resp.ok) {
                const info = await resp.json();
                const { min_time, max_time, is_complete } = info;

                if (position >= min_time && position <= max_time) {
                    console.log(`[Player] Smart seek: ${position}s is within transcoded range [${min_time}-${max_time}], using native seek`);
                    this.video.currentTime = position;
                    setTimeout(() => { this.isUserSeeking = false; }, 500);
                    return;
                }

                if (!is_complete && position > max_time && position <= max_time + 60) {
                    console.log(`[Player] Smart seek: ${position}s is ahead, waiting for transcode...`);
                    const deadline = Date.now() + 15000;
                    while (Date.now() < deadline) {
                        await new Promise(r => setTimeout(r, 1500));
                        const check = await fetch(`/api/stream/transcode/${this.streamId}/segment-range`);
                        if (check.ok) {
                            const updated = await check.json();
                            if (position <= updated.max_time) {
                                this.video.currentTime = position;
                                setTimeout(() => { this.isUserSeeking = false; }, 500);
                                return;
                            }
                        }
                    }
                }
            }
        } catch (e) {
            console.warn('[Player] Smart seek check failed:', e.message);
        }

        await this._restartSeek(position);
    }

    async _restartSeek(position) {
        this.isInternalSeeking = true;
        console.log(`[Player] Restart seek to ${position}s`);
        
        // Stop current HLS instance from fetching segments that are about to be deleted
        if (this.hls) {
            this.hls.stopLoad();
        }

        try {
            await fetch(`/api/stream/transcode/${this.streamId}/seek?position=${position}`, {
                method: "POST"
            });
            if (this.isDestroyed) return;
            const hlsUrl = `/api/stream/hls/${this.streamId}/master.m3u8?t=${Date.now()}`;
            this._loadHls(hlsUrl, position);
        } finally {
            setTimeout(() => { 
                this.isUserSeeking = false; 
                this.isInternalSeeking = false;
            }, 2000);
        }
    }

    async seek(position) {
        return this.smartSeek(position);
    }

    switchAudio(index) {
        if (this.hls) this.hls.audioTrack = index;
    }

    switchSubtitle(index) {
        if (this.hls) this.hls.subtitleTrack = index;
    }

    destroy() {
        this.isDestroyed = true;
        this.releaseWakeLock();
        if (this.hls) {
            this.hls.destroy();
            this.hls = null;
        }
        if (this._cleanupKeydown) this._cleanupKeydown();
        if (this.pausedOverlayTimer) clearTimeout(this.pausedOverlayTimer);
        this.hasStartedPlaying = false;
        this._trigger('onClose');
    }
}