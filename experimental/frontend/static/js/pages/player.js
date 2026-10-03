class VideoPlayer {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.overlay = options.overlayId ? document.getElementById(options.overlayId) : null;
        this.hls = null;
        this.art = null;
        this.streamId = null;
        this.duration = null;

        this.isUserSeeking = false;
        this.isInternalSeeking = false;
        this.hasStartedPlaying = false;
        this.isDestroyed = false;
        this.lastKnownPosition = 0;
        this.wakeLock = null;
        this.streamOffset = 0;
        this.subtitleText = null;
        this.subtitleUrl = null;
        this.fatalReported = false;

        this.hooks = {
            onPlay: [],
            onPause: [],
            onEnded: [],
            onError: [],
            onFatalError: [],
            onProgress: [],
            onTimeUpdate: [],
            onClose: []
        };

        this.pausedOverlayTimer = null;
    }

    on(event, callback) {
        if (this.hooks[event]) {
            this.hooks[event].push(callback);
        }
    }

    _trigger(event, ...args) {
        if (this.isDestroyed && event !== 'onClose') return;
        if (event === 'onFatalError') {
            if (this.fatalReported || this.isInternalSeeking) return;
            this.fatalReported = true;
        }
        if (this.hooks[event]) {
            this.hooks[event].forEach(cb => cb(...args));
        }
    }

    async load(apiUrl, startTime = 0) {
        const response = await fetch(apiUrl);
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `Playback request failed (${response.status})`);
        }
        let data;
        if ((response.headers.get('content-type') || '').includes('application/json')) {
            data = await response.json();
            if (!data.url) throw new Error('Playback response contains no video URL');
        } else {

            if (response.body) await response.body.cancel();
            data = { url: apiUrl, type: 'direct' };
        }
        if (this.isDestroyed) {
            if (data.stream_id) await fetch(`/api/stream/transcode/${data.stream_id}`, { method: 'DELETE' });
            return;
        }
        this.streamId = data.stream_id;
        this.probeInfo = data.probe_info || {};
        if (this.streamId) this.startHeartbeat();
        this.duration = data.probe_info?.duration
            ? parseFloat(data.probe_info.duration)
            : null;
        if (!this.duration && this.streamId) this._waitForDuration(this.streamId);

        if (this.streamId && startTime > 0) {
            const response = await fetch(`/api/stream/transcode/${this.streamId}/seek?position=${startTime}`, { method: 'POST' });
            if (!response.ok) throw new Error('Unable to resume this video');
            this.streamOffset = startTime;
        }
        if (this.isDestroyed) {
            if (this.streamId) await fetch(`/api/stream/transcode/${this.streamId}`, { method: 'DELETE' });
            return;
        }
        this.nextStartTime = this.streamId ? 0 : startTime;
        this._initArtPlayer(data.url, data.type === 'hls');
    }

    _initArtPlayer(hlsUrl, isHls = true) {
        if (this.art) this.art.destroy();
        if (this.hls) this.hls.destroy();

        this.art = new Artplayer({
            container: `#${this.containerId}`,
            url: hlsUrl,
            type: isHls ? 'm3u8' : '',
            volume: 1,
            isLive: false,
            muted: false,
            autoplay: true,
            hotkey: !this.streamId,
            pip: true,
            autoSize: false,
            autoMini: false,
            screenshot: true,
            setting: true,
            loop: false,
            flip: true,
            playbackRate: true,
            aspectRatio: true,
            fullscreen: true,
            fullscreenWeb: true,
            subtitleOffset: true,
            miniProgressBar: !this.streamId,
            mutex: true,
            backdrop: true,
            playsInline: true,
            autoPlayback: false,
            airplay: true,
            theme: '#3b82f6',
            lang: navigator.language.toLowerCase(),
            customType: {
                m3u8: (video, url, art) => {
                    if (this.hls) this.hls.destroy();

                    if (!window.Hls || !Hls.isSupported()) {
                        if (video.canPlayType('application/vnd.apple.mpegurl')) {
                            video.src = url;
                            video.addEventListener('loadedmetadata', () => {
                                if (this.nextStartTime) video.currentTime = this.nextStartTime;
                                this.nextStartTime = 0;
                            }, { once: true });
                            return;
                        }
                        throw new Error('This browser does not support HLS playback');
                    }
                    let recoveryAttempts = 0;
                    this.hls = new Hls({
                        startPosition: 0,
                        maxBufferLength: 30,
                        maxMaxBufferLength: 60,
                        liveDurationIntersection: false,
                        enableSafeDuration: true
                    });

                    this.hls.loadSource(url);
                    this.hls.attachMedia(video);

                    this.hls.on(Hls.Events.MANIFEST_PARSED, () => {
                        this._applyDuration();
                        if (this.nextStartTime) {
                            video.currentTime = this.nextStartTime;
                            this.nextStartTime = 0;
                        }
                    });

                    this.hls.on(Hls.Events.ERROR, (event, data) => {
                        if (data.fatal) {
                            console.error("[Player] HLS fatal error", data);
                            if (++recoveryAttempts > 3) {
                                this.hls.stopLoad();
                                this._trigger('onFatalError', data);
                            } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                                this.hls.recoverMediaError();
                            } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                                this.hls.startLoad();
                            } else {
                                this._trigger('onFatalError', data);
                            }
                        }
                    });
                }
            }
        });

        this._setupArtPlayerEvents();
        if (this.streamId) {
            this.keyHandler = (event) => {
                if (this.isDestroyed || /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
                if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
                event.preventDefault();
                this.seek(this.currentTime + (event.key === 'ArrowRight' ? 10 : -10))
                    .catch(error => this._trigger('onError', error));
            };
            document.addEventListener('keydown', this.keyHandler);
        }
        if (!isHls) {
            this.art.on('video:loadedmetadata', () => {
                if (this.nextStartTime) this.art.currentTime = this.nextStartTime;
                this.nextStartTime = 0;
            });
            this.art.on('video:error', () => this._trigger('onFatalError', {
                message: 'The browser could not play this video source'
            }));
        }
    }

    get currentTime() {
        return this.streamOffset + (this.art?.video?.currentTime || 0);
    }

    get totalDuration() {
        const duration = this.duration || this.art?.video?.duration;
        return Number.isFinite(duration) ? duration : 0;
    }

    async _waitForDuration(streamId) {
        for (let attempt = 0; attempt < 60; attempt++) {
            await new Promise(resolve => setTimeout(resolve, 3000));
            if (this.isDestroyed || this.streamId !== streamId || this.duration) return;
            try {
                const response = await fetch(`/api/stream/transcode/${streamId}/segment-range`);
                if (!response.ok) continue;
                const data = await response.json();
                if (data.duration > 0 && this.streamId === streamId) {
                    this.duration = Number(data.duration);
                    return;
                }
            } catch (error) {}
        }
    }

    _applyDuration() {

    }

    _setupArtPlayerEvents() {
        this.art.on('video:canplay', () => {
            if (this.hasStartedPlaying || this.isInternalSeeking || this.isDestroyed) return;
            this.art.play().catch(error => {
                if (error.name === 'NotAllowedError') this.art.notice.show = 'Press Play to resume';
            });
        });
        this.art.on('video:playing', () => {
            this.hasStartedPlaying = true;
            this._handlePlay();
            this._trigger('onPlay');
            this.requestWakeLock();
        });

        this.art.on('pause', () => {
            this._handlePause();
            this._trigger('onPause');
            this.releaseWakeLock();
        });

        this.art.on('video:ended', () => {
            this._trigger('onEnded');

        });

        this.art.on('video:timeupdate', () => {
            if (!this.isUserSeeking) {
                const currentTime = this.currentTime;
                if (Math.abs(currentTime - this.lastKnownPosition) >= 1) {
                    this.lastKnownPosition = currentTime;
                    this._trigger('onTimeUpdate', currentTime);
                }
            }
        });

        this.art.on('seek', async (currentTime) => {

            if (this.isUserSeeking || this.isInternalSeeking || this.streamId) return;

            const seekTo = currentTime;
            if (!this.hasStartedPlaying || seekTo < 2) return;

            console.log(`[Player] Manual seek detected: ${this.lastKnownPosition}s -> ${seekTo}s`);
            try { await this.smartSeek(seekTo); }
            catch (error) { this._trigger('onError', error); }
        });

        this.art.on('video:error', (e) => {
            if (this.isDestroyed || this.isInternalSeeking) return;
            const error = this.art.video.error;
            console.error("[Player] Video element error:", error);
            if (error && (error.code === 3 || error.code === 4)) {
                this._trigger('onFatalError', error);
            } else {
                this._trigger('onError', error);
            }
        });
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
                if (this.art && !this.art.video.paused) this.overlay.style.display = 'none';
            }, 500);
        }
    }

    _handlePause() {
        if (this.pausedOverlayTimer) clearTimeout(this.pausedOverlayTimer);
        this.pausedOverlayTimer = setTimeout(() => {
            if (this.art && this.art.video.paused && this.overlay) {
                this.overlay.style.display = 'block';
                this.overlay.offsetHeight;
                this.overlay.classList.remove('opacity-0');
                this.overlay.classList.add('opacity-100');
            }
        }, 1000);
    }

    startHeartbeat() {
        const ping = async () => {
            if (this.isDestroyed) return;
            try {
                const response = await fetch(`/api/stream/transcode/${this.streamId}/heartbeat`, { method: 'POST' });
                if (response.status === 404) {
                    clearInterval(this.heartbeatTimer);
                    this._trigger('onFatalError', new Error('Playback session expired. Please restart playback.'));
                }
            } catch (_) {  }
        };
        this.heartbeatTimer = setInterval(ping, 30000);
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

    async smartSeek(position) {
        if (!this.art || this.isDestroyed || this.isUserSeeking || !Number.isFinite(position)) return;
        position = Math.max(0, Math.min(position, Math.max(0, this.totalDuration - 0.1)));
        if (!this.streamId) {
            this.art.currentTime = position;
            return;
        }
        this.isUserSeeking = true;
        try {
            const response = await fetch(`/api/stream/transcode/${this.streamId}/segment-range`);
            if (this.isDestroyed) return;
            const range = response.ok ? await response.json() : null;
            if (range?.segment_count && position >= range.min_time && position < range.max_time - 0.25) {
                this.art.video.currentTime = position - this.streamOffset;
            } else {
                await this._restartSeek(position);
            }
        } finally {
            this.isUserSeeking = false;
        }
    }

    async _restartSeek(position) {
        this.isInternalSeeking = true;
        const wasPlaying = !this.art.video.paused;
        this.art.pause();
        if (this.hls) {
            this.hls.destroy();
            this.hls = null;
        }
        this.art.notice.show = 'Seeking…';
        try {
            const response = await fetch(`/api/stream/transcode/${this.streamId}/seek?position=${position}`, { method: 'POST' });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || 'Unable to seek');
            }
            if (this.isDestroyed) return;
            this.streamOffset = position;
            this.nextStartTime = 0;

            await new Promise((resolve, reject) => {
                const video = this.art.video;
                const timer = setTimeout(() => finish(new Error('Timed out loading the seek position')), 30000);
                const ready = () => finish();
                const failed = () => finish(new Error('Unable to load the seek position'));
                const finish = (error) => {
                    clearTimeout(timer);
                    video.removeEventListener('canplay', ready);
                    video.removeEventListener('error', failed);
                    this.cancelSeekWait = null;
                    error ? reject(error) : resolve();
                };
                this.cancelSeekWait = () => finish(new Error('Playback was closed'));
                video.addEventListener('canplay', ready, { once: true });
                video.addEventListener('error', failed, { once: true });
                this.art.url = `/api/stream/hls/${this.streamId}/master.m3u8?t=${Date.now()}`;
            });
            if (this.isDestroyed) return;
            await this._applySubtitle();
            this.lastKnownPosition = position;
            this._trigger('onTimeUpdate', position);
            if (wasPlaying) await this.art.play();
            this.art.notice.show = '';
        } catch (error) {
            if (!this.isDestroyed) this.art.notice.show = error.message;
            throw error;
        } finally {
            this.isInternalSeeking = false;
        }
    }

    seek(position) {
        return this.smartSeek(position);
    }

    async setSubtitleText(text, timestampOffset = 0) {
        this.subtitleText = text;
        this.subtitleTimestampOffset = timestampOffset;
        await this._applySubtitle();
    }

    async _applySubtitle() {
        if (!this.art) return;
        if (!this.subtitleText) {
            this.art.subtitle.show = false;
            return;
        }
        const seconds = (value) => {
            const parts = value.replace(',', '.').split(':').map(Number);
            return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
        };
        const timestamp = (value) => {
            const ms = Math.round(Math.max(0, value) * 1000);
            return `${String(Math.floor(ms / 3600000)).padStart(2, '0')}:${String(Math.floor(ms / 60000) % 60).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`;
        };
        const blocks = this.subtitleText.replace(/\r/g, '').replace(/^\uFEFF/, '').split(/\n\s*\n/);
        const cues = blocks.filter(block => block.includes('-->')).map(block => {
            let keep = true;
            const cue = block.replace(/((?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3})/,
                (_, start, end) => {
                    if (seconds(end) + (this.subtitleTimestampOffset || 0) <= this.streamOffset) keep = false;
                    return `${timestamp(seconds(start) + (this.subtitleTimestampOffset || 0) - this.streamOffset)} --> ${timestamp(seconds(end) + (this.subtitleTimestampOffset || 0) - this.streamOffset)}`;
                });

            return keep ? cue.replace(/<\/?(?:i|b|u|font|c|v|lang|ruby|rt)(?:[.\s][^>]*)?>/gi, '') : '';
        }).filter(Boolean);
        const oldUrl = this.subtitleUrl;
        this.subtitleUrl = URL.createObjectURL(new Blob(['WEBVTT\n\n' + cues.join('\n\n')], { type: 'text/vtt' }));
        await this.art.subtitle.switch(this.subtitleUrl, { type: 'vtt' });
        this.art.subtitle.show = true;
        if (oldUrl) URL.revokeObjectURL(oldUrl);
    }

    switchAudio(index) {
        if (this.hls) {
            this.hls.audioTrack = index;
            if (this.art && this.art.notice) this.art.notice.show = 'Audio track switched';
        }
    }

    switchSubtitle(index) {
        if (this.hls) this.hls.subtitleTrack = index;
    }

    destroy() {
        if (this.isDestroyed) return;
        this.isDestroyed = true;
        if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
        if (this.cancelSeekWait) this.cancelSeekWait();
        if (this.keyHandler) document.removeEventListener('keydown', this.keyHandler);
        if (this.subtitleUrl) URL.revokeObjectURL(this.subtitleUrl);
        this.releaseWakeLock();

        if (this.hls) {
            this.hls.destroy();
            this.hls = null;
        }

        if (this.art) {
            this.art.destroy(false);
            this.art = null;
        }

        if (this.pausedOverlayTimer) clearTimeout(this.pausedOverlayTimer);
        this.hasStartedPlaying = false;
        this._trigger('onClose');
    }
}