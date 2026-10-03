
const GENRE_MAP = {
  28: "Action",
  12: "Adventure",
  16: "Animation",
  35: "Comedy",
  99: "Documentary",
  18: "Drama",
  10751: "Family",
  14: "Fantasy",
  36: "History",
  27: "Horror",
  10402: "Music",
  9648: "Mystery",
  10749: "Romance",
  878: "Science Fiction",
  53: "Thriller",
  10752: "War",
  37: "Western",
  80: "Crime",
  10765: "Kids",
};

function getGenreNames(genreIds) {
  if (!genreIds || !Array.isArray(genreIds)) return [];
  return genreIds.map((id) => GENRE_MAP[id] || `Genre ${id}`).filter(Boolean);
}

function mediaDetailPage(serverMedia = null, isVirtual = false) {
  return {
    media: serverMedia,
    sources: {
      local: [],
      streams: {},
    },
    loading: !serverMedia,
    activeTab: "all",
    processing: false,
    isVirtual: isVirtual,
    inWatchlist: false,
    watchlistId: null,
    requestsEnabled: false,
    requestStatus: null,
    episodeProgress: {},
    selectedSeason: null,
    selectedEpisode: null,
    sourcesLoading: false,
    playerLoading: false,
    magnetLink: "",
    savingDownload: false,

    currentPlayerSource: null,
    progressInterval: null,
    allSources: [],
    selectedSourceIndex: 0,
    audioTracks: [],
    selectedAudioTrack: null,
    sourceMenuOpen: false,
    audioMenuOpen: false,
    subtitleTracks: [],
    externalSubtitles: [],
    selectedSubtitle: 'off',
    subtitleLoading: false,
    subtitleTimer: null,
    playerPosition: 0,
    playerDuration: 0,
    seekPosition: 0,
    playerTranscoded: false,
    seekBusy: false,
    currentStreamId: null,

    videoPlayer: null,

    async init() {

      this._unloadHandler = () => this.handleUnload();
      window.addEventListener('pagehide', this._unloadHandler);
      window.addEventListener('beforeunload', this._unloadHandler);

      try {

        if (this.media && this.isVirtual) {
          this.media = {
            ...this.media,
            cast: this.media.cast || [],
            directors: this.media.directors || [],
            genres:
              this.media.genres || getGenreNames(this.media.genre_ids || []),
          };
        }

        if (this.media && !this.media.genres && this.media.genre_ids) {
          this.media.genres = getGenreNames(this.media.genre_ids);
        }

        if (this.media) {
          this.media = this.sanitizeMediaObject(this.media);
        }

        const mediaIdFromUrl = this.getMediaIdFromUrl();

        const mediaId =
          this.media?.id ||
          this.media?.tmdb_id ||
          this.media?.uuid ||
          mediaIdFromUrl;

        if (mediaId) {
          await this.loadMedia(mediaId);

          await Promise.allSettled([
            this.checkWatchlist().catch((e) =>
              console.log("Watchlist check failed:", e),
            ),
            this.loadRequestState().catch((e) =>
              console.log("Request status load failed:", e),
            ),
            this.loadEpisodeProgress().catch((e) =>
              console.log("Episode progress load failed:", e),
            ),
          ]);

          const params = new URLSearchParams(window.location.search);
          const requestedEpisode = /^\d+$/.test(params.get("season") || "") && /^\d+$/.test(params.get("episode") || "");

          if (!requestedEpisode && (
            this.media?.media_type === "tv" ||
            this.media?.media_type === "video_series"
          )) {
            await this.setInitialSelection();
          }

          if ((this.media?.media_type === 'tv' || this.media?.media_type === 'video_series') &&
              /^\d+$/.test(params.get('season') || '') && /^\d+$/.test(params.get('episode') || '')) {
            this.selectedSeason = Number(params.get('season'));
            this.selectedEpisode = Number(params.get('episode'));
            await this.loadMedia(this.media.uuid || `tmdb:${this.media.tmdb_id}`, this.selectedSeason, this.selectedEpisode);
          }
          if (params.get("autoPlay") === "true") {
            const time = parseFloat(params.get("time")) || 0;

            this.buildAllSourcesList();

            if (this.allSources.length > 0) {
              const requestedSource = Number(params.get("source_id"));
              const sourceIndex = params.has("source_id") ? this.allSources.findIndex(source => source.type === "local" && source.data.id === requestedSource) : 0;
              if (sourceIndex >= 0) await this.playSourceByIndex(sourceIndex, time);
            }
          }
        } else {
          this.loading = false;
        }
      } catch (error) {
        console.error("Failed to initialize media detail page:", error);
        this.loading = false;

        if (Alpine.store("notifications")) {
          Alpine.store("notifications").error("Failed to load media details");
        }
      }
    },

    sanitizeMediaObject(media) {

      return {
        ...media,
        title: media.title || media.name || "Untitled",
        overview: media.overview || media.description || "",
        year:
          media.year ||
          (media.release_date
            ? new Date(media.release_date).getFullYear()
            : null),
        poster_path: media.poster_path || media.poster_url || null,
        backdrop_path: media.backdrop_path || media.backdrop_url || null,
        vote_average: media.vote_average || null,
        content_rating: media.content_rating || null,
        director: media.director || null,
        cast: media.cast || [],
        genre_names: media.genre_names || [],
        runtime: media.runtime || null,
        number_of_seasons: media.number_of_seasons || null,
        number_of_episodes: media.number_of_episodes || null,
        seasons: media.seasons || [],
        uuid: media.uuid || null,
        id_source: media.id_source || null,
        external_id: media.external_id || null,
      };
    },

    getMediaIdFromUrl() {
      const path = window.location.pathname;
      const segments = path.split("/").filter(Boolean);

      if (segments[0] === "media" && segments.length >= 3) {
        const id = segments[2];
        return /^\d+$/.test(id) ? parseInt(id) : id;
      }

      const last = segments[segments.length - 1];
      return /^\d+$/.test(last) ? parseInt(last) : null;
    },

    async loadMedia(mediaId, season = null, episode = null) {
      if (!season && !episode) this.loading = true;
      else this.sourcesLoading = true;

      try {
        const type = this.media?.media_type;

        let identifier = mediaId;
        if (
          typeof mediaId === "number" ||
          (typeof mediaId === "string" && /^\d+$/.test(mediaId))
        ) {
          identifier = `tmdb:${mediaId}`;
        }

        let url = `/api/media/${identifier}/sources`;
        const params = new URLSearchParams();
        if (type) params.append("media_type", type);
        if (season !== null) params.append("season", season);
        if (episode !== null) params.append("episode", episode);

        const queryString = params.toString();
        if (queryString) url += `?${queryString}`;

        let response = await fetch(url);

        if (!response.ok) {
          console.error(`Source fetch failed with status ${response.status}`);
          this.sources = { local: [], strm: [], streams: {} };
          return;
        }

        const data = await response.json();

        if (!season && !episode && data.media) {
          this.media = this.sanitizeMediaObject({
            ...this.media,
            ...data.media,
          });

          if (data.seasons) {
            this.media.seasons = data.seasons;
          }
          if (data.in_library !== undefined) {
            this.media.in_library = data.in_library;
          }

          if (!this.media.genres && this.media.genre_ids) {
            this.media.genres = getGenreNames(this.media.genre_ids);
          }

          if (this.media.is_persisted) {
            this.isVirtual = false;
          }
        }

        const sourceData = data.sources || data;
        this.sources = {
          local: sourceData.local || [],
          jit_local: sourceData.jit_local || [],
          strm: sourceData.strm || [],
          url: sourceData.url || [],
          streams: sourceData.streams || {},
        };

        this.buildAllSourcesList();
      } catch (error) {
        console.error("Failed to load media:", error);
      } finally {
        this.loading = false;
        this.sourcesLoading = false;
      }
    },

    get totalSourceCount() {
      return (this.sources.local?.length || 0) + this.streamAddonCount;
    },

    get streamAddonCount() {
      return Object.values(this.sources.streams || {}).reduce(
        (sum, streams) => sum + streams.length,
        0,
      );
    },

    getPosterUrl(path) {
      if (this.media?.uuid) {
        return `/api/media/images/poster/${this.media.uuid}`;
      }
      const type = this.media?.media_type || "movie";
      const tmdbId = this.media?.tmdb_id || this.media?.id;
      return window.getPosterUrl(path, type, tmdbId);
    },

    getBackdropUrl(path) {
      if (this.media?.uuid) {
        return `/api/media/images/backdrop/${this.media.uuid}`;
      }
      const type = this.media?.media_type || "movie";
      const tmdbId = this.media?.tmdb_id || this.media?.id;
      return window.getBackdropUrl(path, type, tmdbId);
    },

    formatFileSize(bytes) {
      if (!bytes) return "";
      const gb = bytes / (1024 * 1024 * 1024);
      if (gb >= 1) {
        return `${gb.toFixed(2)} GB`;
      }
      const mb = bytes / (1024 * 1024);
      return `${mb.toFixed(2)} MB`;
    },

    async initPlayer(url, startTime = 0) {

      const previous = this.videoPlayer;
      this.videoPlayer = null;
      if (previous) previous.destroy();
      const previousStreamId = this.currentStreamId || previous?.streamId;
      this.currentStreamId = null;
      this.videoPlayer = new VideoPlayer('main-player', { overlayId: 'paused-overlay' });

      const player = this.videoPlayer;
      let failed = false;
      const sourceIndex = this.selectedSourceIndex;
      const failover = async (error) => {
        if (failed || this.videoPlayer !== player) return;
        failed = true;
        this.currentStreamId = player.streamId;
        console.error('[Player] Source failed:', error);
        const position = player.currentTime || startTime;
        if (sourceIndex + 1 < this.allSources.length) {
          await this.playSourceByIndex(sourceIndex + 1, position);
        } else {
          await this.closePlayer();
          Alpine.store('notifications')?.error(error.message || 'All sources failed');
        }
      };
      player.on('onPlay', () => {
        if (this.videoPlayer === player) this.startProgressTracking();
      });
      player.on('onPause', () => {
        if (this.videoPlayer === player) this.stopProgressTracking();
      });
      player.on('onEnded', () => {
        if (this.videoPlayer !== player) return;
        this.reportProgress();
        this.closePlayer();
      });
      player.on('onTimeUpdate', (position) => {
        if (this.videoPlayer !== player) return;
        this.playerPosition = position;
        if (!this.seekBusy) this.seekPosition = position;
        this.playerDuration = player.totalDuration;
        if (!this.subtitleTimer && /^\d+$/.test(this.selectedSubtitle) && !this.subtitleLoading &&
            this.subtitleWindow !== Math.floor(position / 240)) {
          this.selectSubtitle(this.selectedSubtitle);
        }
      });
      player.on('onError', (error) => {
        if (this.videoPlayer === player && error?.message)
          Alpine.store('notifications')?.error(error.message);
      });
      player.on('onFatalError', failover);
      player.on('onClose', () => {
        if (this.videoPlayer === player) this._onPlayerClosed();
      });
      this.playerLoading = true;
      try {
        if (previousStreamId) await fetch(`/api/stream/transcode/${previousStreamId}`, { method: 'DELETE' });
        if (this.videoPlayer !== player) return;
        await player.load(url, startTime);
        if (this.videoPlayer !== player) return;
        this.currentStreamId = player.streamId;
        this.playerTranscoded = Boolean(player.streamId);
        this.playerDuration = player.totalDuration;
        this.audioTracks = player.probeInfo?.audio_tracks || [];
        this.subtitleTracks = player.probeInfo?.subtitle_tracks || [];
        this.playerPosition = startTime;
        this.seekPosition = startTime;
        if (this.media) document.title = `${this.media.title} - JF-Resolve`;
      } catch (error) {
        if (this.videoPlayer === player) {
          this.currentStreamId = player.streamId;
          await failover(error);
        }
      } finally {
        if (this.videoPlayer === player) this.playerLoading = false;
      }
    },

    async stopCurrentTranscode() {
      if (this.currentStreamId) {
        try {
          await fetch(`/api/stream/transcode/${this.currentStreamId}`, { method: 'DELETE' });
        } catch (err) { }
        this.currentStreamId = null;
      }
    },

    handleUnload() {
      clearInterval(this.subtitleTimer);
      this.subtitleTimer = null;
      const streamId = this.currentStreamId || this.videoPlayer?.streamId;
      if (streamId) {
        fetch(`/api/stream/transcode/${streamId}`, { method: 'DELETE', keepalive: true }).catch(() => {});
        this.currentStreamId = null;
      }
      this.videoPlayer?.destroy();
    },

    destroy() {
      if (this._unloadHandler) {
        window.removeEventListener('beforeunload', this._unloadHandler);
        window.removeEventListener('pagehide', this._unloadHandler);
      }
      this.closePlayer();
      this.handleUnload();
    },

    getSourceDisplayName() {
      if (!this.currentPlayerSource) return "";
      const source = this.currentPlayerSource;
      if (
        source.sourceType === "local" ||
        source.sourceType === "strm" ||
        source.type === "local" ||
        source.type === "strm"
      ) {
        if (source.file_path) return source.file_path.split("/").pop();
        return source.displayName || "";
      }
      const parts = [];
      if (source.sourceType && source.sourceType !== "stream")
        parts.push(source.sourceType);
      if (source.quality || source.resolution)
        parts.push(source.quality || source.resolution);
      if (source.tag) parts.push(source.tag);
      if (parts.length > 0) return parts.join(" • ");
      const displayName = source.displayName || source.title || "";
      return displayName.length > 60
        ? displayName.substring(0, 57) + "..."
        : displayName;
    },

    buildAllSourcesList() {
      const allSources = [];
      const locals = [
        ...(this.sources.local || []),
        ...(this.sources.jit_local || []),
      ];
      locals.forEach((source, idx) => {
        allSources.push({
          type: "local",
          sourceType: "Local",
          data: source,
          index: idx,
          displayName:
            source.display_name ||
            source.file_path?.split("/").pop() ||
            "Local File",
          quality:
            source.resolution ||
            this.extractQuality(source.display_name || source.file_path || ""),
          priority: 0,
        });
      });
      (this.sources.strm || []).forEach((source, idx) => {
        allSources.push({
          type: "strm",
          sourceType: "STRM",
          data: source,
          index: idx,
          displayName: source.display_name || source.file_path || "STRM File",
          quality: this.extractQuality(
            source.display_name || source.file_path || "",
          ),
          priority: 0,
        });
      });
      if (this.sources.streams) {
        Object.entries(this.sources.streams).forEach(([addonName, streams]) => {
          streams.forEach((stream, idx) => {
            const resolution =
              stream.resolution ||
              this.extractQuality(stream.title || stream.raw_title || "");
            allSources.push({
              type: "stream",
              sourceType: addonName,
              data: stream,
              index: idx,
              displayName: stream.title || stream.raw_title || "Stream",
              quality: [resolution, stream.tag].filter(Boolean).join(" "),
              priority: 1,
            });
          });
        });
      }
      allSources.sort((a, b) => a.priority - b.priority);
      this.allSources = allSources;
    },

    getSanitizedSourceInfo(source) {
      if (!this.media) return { title: "Unknown", ext: null };

      const isSeries =
        this.media.media_type === "tv" ||
        this.media.media_type === "video_series";
      let title = this.media.title;

      if (
        isSeries &&
        (this.selectedSeason !== null || this.currentPlayerSource?.season)
      ) {
        const s = this.selectedSeason ?? this.currentPlayerSource?.season;
        const e = this.selectedEpisode ?? this.currentPlayerSource?.episode;
        if (s !== null && e !== null) {
          title += ` - S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`;
        }
      }

      let ext = null;
      if (source.file_path) {
        const parts = source.file_path.split(".");
        if (parts.length > 1) {
          ext = parts.pop().toUpperCase();
        }
      } else if (source.source_type === "strm" || source.type === "strm") {
        ext = "STRM";
      }

      return { title, ext };
    },

    extractQuality(text) {
      if (!text) return "";
      const resMatch = text.match(/\b(2160p|4k|8k|1080p|720p|480p|360p)\b/i);
      const hdrMatch = text.match(/\b(HDR10?|DV|Dolby Vision)\b/i);
      const parts = [];
      if (resMatch) parts.push(resMatch[1].toUpperCase());
      if (hdrMatch) parts.push(hdrMatch[1].toUpperCase());
      return parts.join(" ") || "";
    },

    async playBestSource() {
      if (this.allSources.length === 0) this.buildAllSourcesList();
      if (this.allSources.length > 0) {
        await this.playSourceByIndex(0);
      } else {
        Alpine.store("notifications").info("No playable sources found.");
      }
    },

    async playSourceByIndex(sourceIndex, initialTime = 0) {
      if (this.allSources.length === 0) this.buildAllSourcesList();
      if (this.allSources.length === 0 || sourceIndex >= this.allSources.length)
        return;

      const requestId = this._playRequestId = (this._playRequestId || 0) + 1;
      const sourceEntry = this.allSources[sourceIndex];
      this.selectedSourceIndex = sourceIndex;
      const wasFullscreen = document.fullscreenElement !== null;

      this.currentPlayerSource = {
        ...sourceEntry.data,
        sourceType: sourceEntry.type,
        sourceQuality: sourceEntry.quality,
        season: this.selectedSeason,
        episode: this.selectedEpisode,
      };
      this.subtitleTracks = [];
      this.externalSubtitles = [];
      this.loadExternalSubtitles(requestId);
      clearInterval(this.subtitleTimer);
      this.subtitleTimer = null;
      this.selectedSubtitle = 'off';
      this.audioTracks = [];
      this.playerTranscoded = false;
      this.seekBusy = false;
      this.subtitleLoading = false;
      Alpine.store("ui").playerOpen = true;

      let startTime = initialTime;
      const video = this.videoPlayer?.art?.video;
      if (startTime === 0 && (!video || video.paused))
        startTime = await this.getResumePosition();

      let streamUrl;
      if (sourceEntry.type === "local" || sourceEntry.type === "strm") {
        streamUrl = `/api/stream/source/${sourceEntry.data.id}`;
        if (this.selectedAudioTrack !== null)
          streamUrl += `?audio_track=${this.selectedAudioTrack}`;
      } else {
        streamUrl = `/api/playback/proxy?url=${encodeURIComponent(sourceEntry.data.url)}`;
        if (this.selectedAudioTrack !== null)
          streamUrl += `&audio_track=${this.selectedAudioTrack}`;
      }

      if (requestId !== this._playRequestId) return;
      await this.$nextTick();
      if (requestId !== this._playRequestId) return;
      await this.initPlayer(streamUrl, startTime);
      if (requestId !== this._playRequestId) return;
      {
        if (sourceEntry.type === "local" || sourceEntry.type === "strm")
          this.fetchAudioTracks(sourceEntry.data.id);
        else this.fetchAudioTracksFromUrl(sourceEntry.data.url);
        if (wasFullscreen)
          setTimeout(
            () => document.documentElement.requestFullscreen().catch(() => { }),
            100,
          );
      }
    },

    async getResumePosition() {
      try {
        const mediaType = this.media?.media_type;
        const isUserContent =
          mediaType === "video" || mediaType === "video_series";
        let url;
        if (isUserContent) {
          const uuid = this.media?.uuid;
          if (!uuid) return 0;
          url = `/api/playback/progress/${uuid}?media_type=${mediaType}`;
          if (mediaType === "video_series" && this.currentPlayerSource?.season)
            url += `&season=${this.currentPlayerSource.season}&episode=${this.currentPlayerSource.episode}`;
        } else {
          const tmdbId = this.media?.tmdb_id;
          if (!tmdbId) return 0;
          url = `/api/playback/progress/tmdb:${tmdbId}?media_type=${mediaType || "movie"}`;
          if (mediaType === "tv" && this.currentPlayerSource?.season)
            url += `&season=${this.currentPlayerSource.season}&episode=${this.currentPlayerSource.episode}`;
        }
        const resp = await fetch(url);
        if (resp.ok) {
          const session = await resp.json();
          if (session && session.current_time > 10 && !session.completed)
            return session.current_time;
        }
      } catch (e) { }
      return 0;
    },

    async fetchAudioTracks(sourceId) {
      const player = this.videoPlayer;
      try {
        const resp = await fetch(`/api/stream/info/source/${sourceId}`);
        if (resp.ok) {
          const info = await resp.json();
          if (this.videoPlayer !== player) return;
          if (info.probe_info?.audio_tracks)
            this.audioTracks = info.probe_info.audio_tracks;
          this.subtitleTracks = info.probe_info?.subtitle_tracks || [];
        }
      } catch (e) { }
    },

    async fetchAudioTracksFromUrl(url) {
      const player = this.videoPlayer;
      try {
        const resp = await fetch(
          `/api/stream/probe?url=${encodeURIComponent(url)}`,
        );
        if (resp.ok) {
          const info = await resp.json();
          if (this.videoPlayer !== player) return;
          if (info.audio_tracks) this.audioTracks = info.audio_tracks;
          this.subtitleTracks = info.subtitle_tracks || [];
        }
      } catch (e) {
        console.log("Failed to fetch audio tracks from URL:", e);
      }
    },

    async switchSource(sourceIndex) {
      const video = this.videoPlayer?.art?.video;
      if (!video) return;
      const currentTime = this.videoPlayer.currentTime;
      const wasPaused = video.paused;
      await this.playSourceByIndex(sourceIndex, currentTime);
      if (wasPaused) this.$nextTick(() => {
        const newVideo = this.videoPlayer?.art?.video;
        if (newVideo) newVideo.pause();
      });
      this.sourceMenuOpen = false;
    },

    async switchAudioTrack(index) {
      this.selectedAudioTrack = index;
      this.audioMenuOpen = false;

      if (this.videoPlayer) {
        this.videoPlayer.switchAudio(index);
      }
    },

    async playSource(source, season = null, episode = null, initialTime = 0) {
      if (this.allSources.length === 0) this.buildAllSourcesList();
      const sourceIndex = this.allSources.findIndex(
        (s) =>
          s.data.id === source.id &&
          (s.type === 'local' || s.type === 'strm'),
      );
      if (sourceIndex >= 0)
        await this.playSourceByIndex(sourceIndex, initialTime);
      else {
        this.currentPlayerSource = { ...source, season, episode };
        Alpine.store("ui").playerOpen = true;
        let startTime = initialTime;
        if (startTime === 0) startTime = await this.getResumePosition();
        this.$nextTick(() =>
          this.initPlayer(`/api/stream/source/${source.id}`, startTime),
        );
      }
    },

    async playStream(stream) {
      if (this.allSources.length === 0) this.buildAllSourcesList();
      const index = this.allSources.findIndex(source => source.type === 'stream' && source.data.url === stream.url);
      if (index < 0) {
        Alpine.store('notifications')?.error('This stream is no longer available. Refresh sources.');
        return;
      }
      await this.playSourceByIndex(index);
    },

    downloadParams() {
      const source = this.currentPlayerSource;
      if (!source) return null;
      const params = new URLSearchParams({ title: this.media?.title || 'Media' });
      if (source.sourceType === 'local' || source.sourceType === 'strm') params.set('source_id', source.id);
      else params.set('url', source.url);
      return params;
    },

    deviceDownloadUrl() {
      const params = this.downloadParams();
      return params ? `/api/downloads/file?${params}` : '#';
    },

    async saveDownloadOnServer() {
      const params = this.downloadParams();
      if (!params || this.savingDownload) return;
      this.savingDownload = true;
      try {
        const payload = Object.fromEntries(params);
        if (payload.source_id) payload.source_id = Number(payload.source_id);
        payload.media_identifier = this.media?.uuid || (this.media?.tmdb_id ? `tmdb:${this.media.tmdb_id}` : null);
        payload.media_type = this.media?.media_type;
        if (payload.media_type === 'tv' || payload.media_type === 'video_series') {
          payload.season = this.currentPlayerSource?.season ?? this.selectedSeason;
          payload.episode = this.currentPlayerSource?.episode ?? this.selectedEpisode;
        }
        const response = await fetch('/api/downloads/jobs', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unable to start download');
        Alpine.store('notifications')?.success('Server download queued. Open Downloads to see progress.');
      } catch (error) { Alpine.store('notifications')?.error(error.message); }
      finally { this.savingDownload = false; }
    },

    async playMagnet() {
      const url = this.magnetLink.trim();
      if (!/^magnet:\?/.test(url)) {
        Alpine.store("notifications")?.error("Enter a magnet link");
        return;
      }
      this.buildAllSourcesList();
      this.allSources.push({ type: "stream", sourceType: "P2P", displayName: "Magnet", quality: "", data: {url, title: "Magnet (P2P)", raw_title: "Magnet (P2P)"} });
      await this.playSourceByIndex(this.allSources.length - 1);
    },

    async loadExternalSubtitles(requestId) {
      if (!(this.media?.uuid || this.media?.tmdb_id) || !['movie', 'tv'].includes(this.media.media_type)) return;
      const params = new URLSearchParams({ media_type: this.media.media_type });
      if (this.selectedSeason != null) params.set('season', this.selectedSeason);
      if (this.selectedEpisode != null) params.set('episode', this.selectedEpisode);
      const identifier = this.media.uuid || `tmdb:${this.media.tmdb_id}`;
      try {
        const response = await fetch(`/api/media/${identifier}/subtitles?${params}`);
        if (!response.ok || requestId !== this._playRequestId) return;
        const subtitles = await response.json();
        if (requestId === this._playRequestId) this.externalSubtitles = subtitles;
      } catch (error) {}
    },

    async selectSubtitle(value) {
      clearInterval(this.subtitleTimer);
      this.subtitleTimer = null;
      this.selectedSubtitle = value;
      const player = this.videoPlayer;
      if (!player) return;
      if (value === 'off') {
        await player.setSubtitleText(null);
        return;
      }
      if (value.startsWith('ext:')) {
        const subtitle = this.externalSubtitles[Number(value.slice(4))];
        this.subtitleLoading = true;
        try {
          const response = await fetch(`/api/stream/external-subtitle?token=${encodeURIComponent(subtitle.token)}`);
          if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || 'Unable to load subtitles');
          }
          const text = await response.text();
          if (this.videoPlayer === player && this.selectedSubtitle === value) await player.setSubtitleText(text);
        } catch (error) {
          if (this.videoPlayer === player) {
            this.selectedSubtitle = 'off';
            Alpine.store('notifications')?.error(error.message);
          }
        } finally {
          if (this.videoPlayer === player) this.subtitleLoading = false;
        }
        return;
      }
      if (player.streamId) {
        let lastText = null;
        let lastOffset = null;
        let busy = false;
        const refresh = async () => {
          if (busy || this.videoPlayer !== player || this.selectedSubtitle !== value) return;
          busy = true;
          this.subtitleLoading = lastText === null;
          const offset = player.streamOffset;
          try {
            const response = await fetch(`/api/stream/transcode/${player.streamId}/subtitles/${value}`);
            if (!response.ok) throw new Error('Unable to read playback subtitles');
            const text = await response.text();
            const returnedOffset = Number(response.headers.get('X-Subtitle-Offset'));
            if (this.videoPlayer !== player || this.selectedSubtitle !== value ||
                player.streamOffset !== offset || returnedOffset !== offset) return;
            if (text !== lastText || lastOffset !== offset) {
              await player.setSubtitleText(text, offset);
              lastText = text;
              lastOffset = offset;
            }
          } catch (error) {
            if (this.videoPlayer === player && this.selectedSubtitle === value) {
              clearInterval(this.subtitleTimer);
              this.subtitleTimer = null;
              this.selectedSubtitle = 'off';
              Alpine.store('notifications')?.error(error.message);
            }
          } finally {
            busy = false;
            if (this.videoPlayer === player) this.subtitleLoading = false;
          }
        };
        this.subtitleTimer = setInterval(refresh, 3000);
        await refresh();
        return;
      }
      this.subtitleLoading = true;
      try {
        const source = this.currentPlayerSource;
        const window = Math.floor(player.currentTime / 240);
        this.subtitleWindow = window;
        const params = new URLSearchParams({ index: value, position: player.currentTime });
        if (source.sourceType === 'local' || source.sourceType === 'strm') params.set('source_id', source.id);
        else params.set('url', source.url);
        const response = await fetch(`/api/stream/subtitle?${params}`);
        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(error.detail || 'Unable to load subtitles');
        }
        const text = await response.text();
        if (this.videoPlayer === player && this.selectedSubtitle === value &&
            this.subtitleWindow === Math.floor(player.currentTime / 240)) {
          await player.setSubtitleText(text);
        }
      } catch (error) {
        if (this.videoPlayer === player) Alpine.store('notifications')?.error(error.message);
      } finally {
        if (this.videoPlayer === player) {
          this.subtitleLoading = false;
          if (this.selectedSubtitle === value && this.subtitleWindow !== Math.floor(player.currentTime / 240)) {
            this.selectSubtitle(value);
          }
        }
      }
    },

    async loadSubtitleFile(event) {
      clearInterval(this.subtitleTimer);
      this.subtitleTimer = null;
      const file = event.target.files?.[0];
      const player = this.videoPlayer;
      if (!file || !player) return;
      try {
        if (!/\.(srt|vtt)$/i.test(file.name)) throw new Error('Choose an SRT or VTT subtitle file');
        const text = await file.text();
        if (this.videoPlayer !== player) return;
        await player.setSubtitleText(text);
        this.selectedSubtitle = 'file';
      } catch (error) { Alpine.store('notifications')?.error(error.message); }
      event.target.value = '';
    },

    formatPlayerTime(value) {
      const seconds = Math.floor(Number.isFinite(value) ? value : 0);
      return `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    },

    async seekPlayer(value) {
      const player = this.videoPlayer;
      if (!player || this.seekBusy) return;
      this.seekBusy = true;
      try {
        await player.seek(Number(value));
        if (this.videoPlayer === player) this.playerPosition = player.currentTime;
      } catch (error) {
        if (this.videoPlayer === player) Alpine.store('notifications')?.error(error.message);
      } finally {
        if (this.videoPlayer === player) {
          this.seekBusy = false;
          this.seekPosition = player.currentTime;
        }
      }
    },

    _onPlayerClosed() {

      this.videoPlayer = null;
      Alpine.store('ui').playerOpen = false;
      this.stopProgressTracking();
      this.audioTracks = [];
      this.subtitleTracks = [];
      this.selectedSubtitle = 'off';
      this.subtitleLoading = false;
      this.seekBusy = false;
      this.selectedAudioTrack = null;
      this.sourceMenuOpen = false;
      this.audioMenuOpen = false;
      if (this.media) document.title = `${this.media.title} - JF-Resolve`;
    },

    async closePlayer() {
      clearInterval(this.subtitleTimer);
      this.subtitleTimer = null;
      this._playRequestId = (this._playRequestId || 0) + 1;
      if (this.videoPlayer) {
        this.currentStreamId = this.currentStreamId || this.videoPlayer.streamId;
        this.videoPlayer.destroy();
      } else {
        this._onPlayerClosed();
      }
      await this.stopCurrentTranscode();
    },

    startProgressTracking() {
      this.stopProgressTracking();
      const video = this.videoPlayer?.art?.video;
      this.progressInterval = setInterval(() => {
        if (video && !video.paused) this.reportProgress();
      }, 30000);
    },

    stopProgressTracking() {
      if (this.progressInterval) {
        clearInterval(this.progressInterval);
        this.progressInterval = null;
      }
    },

    reportProgress() {
      const video = this.videoPlayer?.art?.video;
      if (!video || !this.media) return;
      const currentTime = this.videoPlayer.currentTime;
      const duration = this.videoPlayer.totalDuration;
      if (!duration || isNaN(duration)) return;
      const mediaId = this.media.uuid || `tmdb:${this.media.tmdb_id}`;
      const data = JSON.stringify({
        identifier: mediaId,
        media_type: this.media.media_type,
        season: this.currentPlayerSource?.season || null,
        episode: this.currentPlayerSource?.episode || null,
        position_seconds: currentTime,
        duration_seconds: duration,
        completed: currentTime / duration > 0.9,
        source_type: this.currentPlayerSource?.sourceType || null,
        source_quality: this.currentPlayerSource?.sourceQuality || null,
        audio_track: this.selectedAudioTrack,
      });

      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/playback/update", new Blob([data], { type: 'application/json' }));
      } else {
        fetch("/api/playback/update", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: data,
        });
      }
    },

    async addMedia() {
      if (!this.media || this.processing) return;
      this.processing = true;
      const identifier = this.media.uuid || `tmdb:${this.media.tmdb_id}`;
      try {
        const response = await fetch("/api/library/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            identifier: identifier,
            media_type: this.media.media_type,
          }),
        });
        if (response.ok) {
          const data = await response.json();
          if (data.id)
            window.location.href = `/${this.media.media_type === "tv" ? "series" : "movie"}/${data.id}`;
          else window.location.reload();
        }
      } catch (error) {
      } finally {
        this.processing = false;
      }
    },

    async refreshMedia() {
      if (!this.media || this.processing) return;
      Alpine.store("notifications").confirm(
        "Refresh Media",
        `Update metadata for "${this.media.title}"?`,
        async () => {
          this.processing = true;
          try {
            const response = await fetch(
              `/api/library/refresh/${this.media.id}`,
              { method: "POST" },
            );
            if (response.ok) setTimeout(() => window.location.reload(), 1500);
          } catch (error) {
          } finally {
            this.processing = false;
          }
        },
      );
    },

    async deleteMedia() {
      this.processing = true;
      try {
        const identifier = this.media.uuid || `tmdb:${this.media.tmdb_id}`;
        const response = await fetch(`/api/library/${identifier}`, {
          method: "DELETE",
        });
        if (response.ok) {
          const isFromLibrary = document.referrer?.includes("/library");
          window.location.href = isFromLibrary ? "/library" : "/";
        }
      } catch (error) {
      } finally {
        this.processing = false;
      }
    },

    async loadRequestState() {
      const status = await fetch("/api/requests/status");
      if (!status.ok) return;
      this.requestsEnabled = (await status.json()).enabled;
      if (!this.requestsEnabled || !this.media?.tmdb_id) return;
      const response = await fetch("/api/requests");
      if (!response.ok) return;
      const mine = (await response.json()).find(
        (r) =>
          r.tmdb_id === Number(this.media.tmdb_id) &&
          r.media_type === this.media.media_type,
      );
      this.requestStatus = mine ? mine.status : null;
    },

    async requestForJellyfin() {
      if (!this.media?.tmdb_id || this.processing) return;
      this.processing = true;
      try {
        const response = await fetch("/api/requests", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tmdb_id: Number(this.media.tmdb_id),
            media_type: this.media.media_type,
          }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Request failed");
        this.requestStatus = data.status;
        const messages = {
          approved: "Added to Jellyfin",
          pending: "Request sent to the admin",
          failed: `Could not add to Jellyfin: ${data.note}`,
        };
        Alpine.store("notifications")[data.status === "failed" ? "error" : "success"](
          messages[data.status] || "Request updated",
        );
      } catch (error) {
        Alpine.store("notifications").error(error.message);
      } finally {
        this.processing = false;
      }
    },

    async checkWatchlist() {
      const mediaId = this.media?.id || this.media?.tmdb_id || this.media?.uuid;
      if (!mediaId) {
        this.inWatchlist = false;
        return;
      }
      let identifier =
        typeof mediaId === "number" ||
          (typeof mediaId === "string" && /^\d+$/.test(mediaId))
          ? `tmdb:${mediaId}`
          : mediaId;
      try {
        const response = await fetch(
          `/api/watchlist/check/${identifier}?media_type=${this.media.media_type}`,
        );
        if (response.ok) {
          const data = await response.json();
          this.inWatchlist = data.in_watchlist;
        }
      } catch (error) {
        this.inWatchlist = false;
      }
    },

    async loadEpisodeProgress() {
      const tmdbId = this.media?.tmdb_id;
      const uuid = this.media?.uuid;
      const type = this.media?.media_type;
      if (!tmdbId && !uuid) return;
      try {
        let url = tmdbId
          ? `/api/playback/progress/tmdb:${tmdbId}?media_type=${type}`
          : `/api/playback/progress/${uuid}?media_type=${type}`;
        const response = await fetch(url);
        if (response.ok) {
          const items = await response.json();
          const progressList = Array.isArray(items) ? items : [items];
          this.episodeProgress = {};
          let latestProgress = null;
          progressList.forEach((item) => {
            if (item.season !== null && item.episode !== null) {
              const key = `${item.season}_${item.episode}`;
              this.episodeProgress[key] = item.progress_percentage;
              if (
                !latestProgress ||
                (item.updated_at && item.updated_at > latestProgress.updated_at)
              )
                latestProgress = item;
            } else if (
              item.media_type === "movie" ||
              item.media_type === "video"
            ) {
              this.media.progress_percentage = item.progress_percentage;
              this.media.current_time =
                item.position_seconds || item.current_time;
            }
          });
          if (latestProgress)
            this.lastWatched = {
              season: latestProgress.season,
              episode: latestProgress.episode,
            };
        }
      } catch (error) { }
    },

    async setInitialSelection() {
      if (this.lastWatched) {
        this.selectedSeason = this.lastWatched.season;
        this.selectedEpisode = this.lastWatched.episode;
      } else if (this.media?.seasons?.length > 0) {
        const firstSeason =
          this.media.seasons.find((s) => s.season_number > 0) ||
          this.media.seasons[0];
        this.selectedSeason = firstSeason.season_number;
        this.selectedEpisode =
          firstSeason.episodes?.length > 0
            ? firstSeason.episodes[0].episode_number
            : 1;
      }
      if (this.selectedSeason !== null && this.selectedEpisode !== null) {
        const mediaId =
          this.media?.id || this.media?.tmdb_id || this.media?.uuid;
        await this.loadMedia(mediaId, this.selectedSeason, this.selectedEpisode);
      }
    },

    async selectSeason(num) {
      if (this.selectedSeason === num) return;
      this.selectedSeason = num;
      const season = this.media.seasons.find((s) => s.season_number === num);
      if (season && season.episodes?.length > 0)
        this.selectEpisode(season.episodes[0].episode_number);
      else {
        this.selectedEpisode = 1;
        this.updateSources();
      }
    },

    async selectEpisode(num) {
      this.selectedEpisode = num;
      this.updateSources();
    },

    async updateSources() {
      const mediaId = this.media?.id || this.media?.tmdb_id || this.media?.uuid;
      await this.loadMedia(mediaId, this.selectedSeason, this.selectedEpisode);
    },

    get selectedSeasonEpisodes() {
      if (!this.media?.seasons || this.selectedSeason === null) return [];
      const season = this.media.seasons.find(
        (s) => s.season_number === this.selectedSeason,
      );
      return season ? season.episodes || [] : [];
    },

    async addToWatchlist() {
      this.processing = true;
      try {
        const mediaId =
          this.media?.id || this.media?.tmdb_id || this.media?.uuid;
        if (!mediaId) return;
        let identifier =
          typeof mediaId === "number" ||
            (typeof mediaId === "string" && /^\d+$/.test(mediaId))
            ? `tmdb:${mediaId}`
            : mediaId;
        const requestData = {
          identifier,
          media_type: this.media.media_type,
          title: this.media.title,
          poster_path: this.media.poster_path,
          year: this.media.year,
        };
        const response = await fetch("/api/watchlist/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestData),
        });
        if (response.ok) {
          this.inWatchlist = true;
          Alpine.store("notifications").success(`Added to watchlist`);
        }
      } catch (error) {
      } finally {
        this.processing = false;
      }
    },

    async removeFromWatchlist() {
      this.processing = true;
      try {
        const mediaId =
          this.media?.id || this.media?.tmdb_id || this.media?.uuid;
        if (!mediaId) return;
        let identifier =
          typeof mediaId === "number" ||
            (typeof mediaId === "string" && /^\d+$/.test(mediaId))
            ? `tmdb:${mediaId}`
            : mediaId;
        const response = await fetch(
          `/api/watchlist/${identifier}?media_type=${this.media.media_type}`,
          { method: "DELETE" },
        );
        if (response.ok) {
          this.inWatchlist = false;
          Alpine.store("notifications").success(`Removed from watchlist`);
        }
      } catch (error) {
      } finally {
        this.processing = false;
      }
    },

    findSourceForEpisode(seasonNum, episodeNum) {
      if (this.sources.local) {
        const local = this.sources.local.find(
          (s) => s.season === seasonNum && s.episode === episodeNum,
        );
        if (local) return { type: "local", source: local };
      }
      if (this.sources.streams) {
        for (const addon in this.sources.streams) {
          const stream = this.sources.streams[addon].find(
            (s) => s.season === seasonNum && s.episode === episodeNum,
          );
          if (stream) return { type: "stream", source: stream };
        }
      }
      return null;
    },

    playNextEpisode() {
      if (!this.currentPlayerSource) return;
      const { season, episode } = this.currentPlayerSource;
      if (!season || !episode) return;
      const all = [];
      if (this.media?.seasons)
        this.media.seasons.forEach((s) =>
          s.episodes?.forEach((e) =>
            all.push({ s: s.season_number, e: e.episode_number, t: e.name }),
          ),
        );
      all.sort((a, b) => a.s - b.s || a.e - b.e);
      const next = all.find(
        (e) => e.s > season || (e.s === season && e.e > episode),
      );
      if (next) {
        const res = this.findSourceForEpisode(next.s, next.e);
        if (res) {
          if (res.type === "local") this.playSource(res.source, next.s, next.e);
          else this.playStream(res.source);
        }
      }
    },

    playPreviousEpisode() {
      if (!this.currentPlayerSource) return;
      const { season, episode } = this.currentPlayerSource;
      if (!season || !episode) return;
      const all = [];
      if (this.media?.seasons)
        this.media.seasons.forEach((s) =>
          s.episodes?.forEach((e) =>
            all.push({ s: s.season_number, e: e.episode_number, t: e.name }),
          ),
        );
      all.sort((a, b) => a.s - b.s || a.e - b.e);
      const idx = all.findIndex((e) => e.s === season && e.e === episode);
      if (idx > 0) {
        const prev = all[idx - 1];
        const res = this.findSourceForEpisode(prev.s, prev.e);
        if (res) {
          if (res.type === "local") this.playSource(res.source, prev.s, prev.e);
          else this.playStream(res.source);
        }
      }
    },

    destroy() {
      if (this.keyboardHandler)
        document.removeEventListener("keydown", this.keyboardHandler);
      this.stopProgressTracking();
    },
  };
}
