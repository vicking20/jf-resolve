function homePage() {
  return {

    disableBackdrop: false,
    catalogues: [],
    catalogueItems: {},
    isReady: false,

    heroItems: [],
    heroIndex: 0,
    heroVisible: true,
    heroTransitionTimer: null,
    slideshowTimer: null,
    previewItem: null,

    loading: true,
    processing: false,
    selectedItem: null,
    successMessage: "",
    continueWatching: [],
    watchlist: [],
    recentlyAdded: [],
    heroTimeout: null,

    refreshTimer: null,

    destroy() {
      clearInterval(this.refreshTimer);
      clearInterval(this.slideshowTimer);
      clearTimeout(this.heroTransitionTimer);
    },

    async init() {
      try {

        this.slideshowTimer = setInterval(() => {
          if (this.heroItems.length > 1 && !this.previewItem) {
            this.setHero((this.heroIndex + 1) % this.heroItems.length);
          }
        }, 8000);

        const settingsRes = await fetch("/api/settings/");
        if (settingsRes.ok) {
          const data = await settingsRes.json();
          this.disableBackdrop =
            data.settings.disable_discover_backdrop ||
            data.settings.disable_backdrop ||
            false;
        }

        await Promise.all([
          this.fetchCatalogues(),
          this.loadContinueWatching(),
          this.loadWatchlist(),
          this.loadRecentlyAdded(),
        ]);

        if (this.catalogues.length > 0) {
          await Promise.all(
            this.catalogues.map((cat) => this.loadCatalogueItems(cat.id)),
          );

          for (const cat of this.catalogues) {
            const items = this.catalogueItems[cat.id];
            if (items && items.length > 0) {
              this.heroItems = items.slice(0, 6);
              break;
            }
          }

          if (this.heroItems.length > 0 && !this.disableBackdrop) {

            setTimeout(() => {
              this.dispatchBackgroundUpdate();
            }, 100);
          }
        }

        this.isReady = true;
        this.refreshTimer = setInterval(async () => {
          if (document.hidden) return;
          await this.fetchCatalogues();
          await Promise.all(this.catalogues.map(cat => this.loadCatalogueItems(cat.id)));
          const first = this.catalogues.find(cat => this.catalogueItems[cat.id]?.length);
          if (first) {
            this.heroItems = this.catalogueItems[first.id].slice(0, 6);
            this.heroIndex %= this.heroItems.length;
          }
        }, 60000);
      } catch (error) {
        console.error("Failed to initialize home page:", error);
        Alpine.store("notifications").error(
          "Failed to load content. Please try refreshing.",
        );
      } finally {
        this.loading = false;
      }
    },

    async fetchCatalogues() {
      try {
        const res = await fetch("/api/catalogues/");
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data)) {

            this.catalogues = data.map((cat) => ({ ...cat, id: cat.slug }));
            this.catalogues.forEach((cat) => {
              if (cat && cat.id) {
                if (!(cat.id in this.catalogueItems)) {
                  this.catalogueItems[cat.id] = [];
                }
              }
            });
          } else {
            this.catalogues = [];
          }
        } else {
          this.catalogues = [];
        }
      } catch (e) {
        console.error("Error fetching catalogues:", e);
        this.catalogues = [];
      }
    },

    async loadCatalogueItems(catalogueId) {
      if (!catalogueId) return;
      try {
        const res = await fetch(`/api/catalogues/${catalogueId}/items`);
        if (res.ok) {
          const items = await res.json();
          this.catalogueItems[catalogueId] = Array.isArray(items)
            ? items.map((item) => ({
              ...item,
              media_type: item.media_type || "movie",
            }))
            : [];
        } else {
          this.catalogueItems[catalogueId] = [];
        }
      } catch (e) {
        this.catalogueItems[catalogueId] = [];
      }
    },

    async loadContinueWatching() {
      try {
        const response = await fetch(
          "/api/playback/continue-watching?limit=10",
        );
        if (response.ok) {
          const data = await response.json();
          this.continueWatching = data.items || [];
        }
      } catch (e) {
        console.error("Failed to load continue watching:", e);
      }
    },

    async loadWatchlist() {
      try {
        const response = await fetch("/api/watchlist/");
        if (response.ok) {
          const data = await response.json();
          this.watchlist = data.map((item) => ({
            ...item,
            watchlist_id: item.id,
          }));
        }
      } catch (e) {
        console.error("Failed to load watchlist:", e);
      }
    },

    async loadRecentlyAdded() {
      try {
        const response = await fetch("/api/library/items?limit=10");
        if (response.ok) {
          const data = await response.json();
          this.recentlyAdded = data.items || [];
        }
      } catch (e) {
        console.error("Failed to load recently added:", e);
      }
    },

    formatProgress(item) {

      const media = item.media || item;
      const mediaType = media.media_type || item.media_type;

      if (mediaType === "tv" && item.season && item.episode) {
        return `S${item.season}E${item.episode} • ${Math.round(item.progress_percentage)}%`;
      }
      return `${Math.round(item.progress_percentage)}% complete`;
    },

    resumePlayback(item) {
      const time = Math.floor(item.position_seconds || item.current_time || 0);

      const media = item.media || item;
      const type = media.media_type || item.media_type || "movie";
      const identifier =
        media.uuid ||
        item.uuid ||
        media.tmdb_id ||
        item.tmdb_id ||
        media.id ||
        item.id;

      if (identifier) {
        window.location.href = `/media/${type}/${identifier}?autoPlay=true&time=${time}`;
      }
    },

    async removeContinueWatching(item) {
      try {
        await fetch(`/api/playback/progress/${item.id}`, { method: "DELETE" });
        await this.loadContinueWatching();
      } catch (e) {
        console.error("Failed to remove item:", e);
      }
    },

    async loadLegacyList(endpoint, targetProp) {
      try {
        const res = await fetch(endpoint);
        if (res.ok) {
          const data = await res.json();
          this[targetProp] = (data.results || []).map((item) => {
            let type = item.media_type;
            if (!type) {
              if (targetProp.includes("Movies")) type = "movie";
              if (targetProp.includes("TV")) type = "tv";
            }
            return { ...item, media_type: type };
          });
        }
      } catch (e) {
        console.error(`Error loading ${targetProp}:`, e);
        this[targetProp] = [];
      }
    },

    updateHero(item) {
      if (this.heroTimeout) clearTimeout(this.heroTimeout);
      this.heroTimeout = setTimeout(() => {
        this.previewItem = item;
        if (!this.disableBackdrop) {
          this.dispatchBackgroundUpdate();
        }
      }, 50);
    },

    setHero(index) {
      if (index === this.heroIndex || !this.heroItems.length) return;
      clearTimeout(this.heroTransitionTimer);
      this.heroVisible = false;
      this.heroTransitionTimer = setTimeout(() => {
        this.heroIndex = index;
        this.previewItem = null;
        this.heroVisible = true;
        if (!this.disableBackdrop) this.dispatchBackgroundUpdate();
      }, 220);
    },

    previousHero() {
      if (!this.heroItems.length) return;
      this.setHero((this.heroIndex - 1 + this.heroItems.length) % this.heroItems.length);
    },

    nextHero() {
      if (!this.heroItems.length) return;
      this.setHero((this.heroIndex + 1) % this.heroItems.length);
    },

    getUpcomingItems() {
      const candidates = this.catalogues.flatMap((catalogue) => this.catalogueItems[catalogue.id] || []);
      const today = new Date().toISOString().slice(0, 10);
      const seen = new Set();
      return candidates.filter((item) => {
        const key = item.uuid || `${item.media_type}:${item.tmdb_id || item.id}`;
        if (!key || seen.has(key)) return false;
        seen.add(key);
        const releaseDate = item.release_date || item.first_air_date || item.metadata?.release_date;
        return releaseDate && releaseDate >= today;
      }).sort((left, right) => {
        const leftDate = left.release_date || left.first_air_date || left.metadata?.release_date;
        const rightDate = right.release_date || right.first_air_date || right.metadata?.release_date;
        return leftDate.localeCompare(rightDate);
      }).slice(0, 5);
    },

    formatReleaseLabel(item) {
      const raw = item.release_date || item.first_air_date || item.metadata?.release_date;
      if (!raw) return item.media_type === "tv" ? "Series" : "Movie";
      const date = new Date(`${raw}T00:00:00`);
      if (Number.isNaN(date.getTime())) return item.year || "Coming soon";
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    },

    async addToWatchlist(item) {
      if (!item) return;
      const mediaType = item.media_type || "movie";
      const tmdbId = item.tmdb_id || item.id;
      const identifier = item.uuid || (tmdbId ? `tmdb:${tmdbId}` : null);

      try {
        const response = await fetch("/api/watchlist/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            identifier,
            tmdb_id: tmdbId,
            media_type: mediaType,
            title: item.title || item.name,
            year: item.year,
            poster_path: item.poster_path || item.poster_url || item.image_paths?.poster,
            overview: item.overview,
          }),
        });
        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(error.detail || "Could not save this title");
        }
        Alpine.store("notifications").success(
          `“${item.title || item.name}” is saved to your watchlist.`,
        );
        await this.loadWatchlist();
      } catch (error) {
        Alpine.store("notifications").error(error.message);
      }
    },

    getCurrentHero() {
      return this.previewItem || this.heroItems[this.heroIndex];
    },

    getHeroImage(itemOverride) {
      const item = itemOverride || this.getCurrentHero();
      if (!item) return "";

      const meta = item.metadata || item;
      const images = item.image_paths || {};

      const backdrop = images.backdrop || meta.backdrop_path || item.backdrop_path || meta.backdrop_url;
      const poster = images.poster || meta.poster_path || item.poster_path || meta.poster_url;

      const type = item.media_type || "movie";
      const tmdbId = item.tmdb_id || item.id;

      if (backdrop) return getBackdropUrl(backdrop, type, tmdbId);
      if (poster) return getPosterUrl(poster, type, tmdbId);
      return "";
    },

    getPosterUrl(path, itemSource = null) {
      const item = itemSource || {};
      const media = item.media || item;
      const uuid = media.uuid || item.uuid;

      if (uuid) {
        return `/api/media/images/poster/${uuid}`;
      }

      const meta = media.metadata || media;
      const images = media.image_paths || {};

      const posterPath =
        path ||
        (media.image_paths && media.image_paths.poster) ||
        (item.image_paths && item.image_paths.poster) ||
        meta.poster_path ||
        media.poster_path ||
        media.poster_url ||
        item.poster_path ||
        item.poster_url;
      if (!posterPath) return "/static/images/default_poster.png";

      const type = media.media_type || item.media_type || "movie";
      const id =
        item.media_id || media.id || item.id || media.tmdb_id || item.tmdb_id;
      return window.getPosterUrl(posterPath, type, id);
    },

    dispatchBackgroundUpdate() {
      const url = this.getHeroImage();
      if (url)
        window.dispatchEvent(
          new CustomEvent("update-background", { detail: url }),
        );
    },

    async showDetail(item, event) {
      if (!item) return;
      if (event) event.preventDefault();

      const type = item.media_type || "movie";
      const identifier = item.uuid || item.tmdb_id || item.id;

      if (identifier) {
        window.location.href = `/media/${type}/${identifier}`;
      }
    },

    async addToLibrary() {
      if (!this.selectedItem) return;

      this.processing = true;

      try {
        const response = await fetch("/api/library/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tmdb_id: this.selectedItem.tmdb_id || this.selectedItem.id,
            media_type: this.selectedItem.media_type,
          }),
        });

        if (response.ok) {
          Alpine.store("notifications").success(
            `"${this.selectedItem.title || this.selectedItem.name}" has been added!`,
          );
          this.selectedItem.in_library = true;
          this.selectedItem = null;
        } else {
          const error = await response.json();
          Alpine.store("notifications").error(
            error.detail || "Failed to add to library",
          );
        }
      } catch (e) {
        Alpine.store("notifications").error(
          "Failed to add to library: " + e.message,
        );
      } finally {
        this.processing = false;
      }
    },

    async removeFromLibrary() {
      if (!this.selectedItem || !this.selectedItem.in_library) return;
      const itemId = this.selectedItem.id;
      if (!itemId) {
        Alpine.store("notifications").error(
          "Could not find library ID for this item.",
        );
        return;
      }

      Alpine.store("notifications").confirm(
        "Remove from Library",
        `Are you sure you want to remove "${this.selectedItem.title || this.selectedItem.name}" from your library?`,
        async () => {
          this.processing = true;
          try {
            const response = await fetch(`/api/library/items/${itemId}`, {
              method: "DELETE",
            });

            if (response.ok) {
              Alpine.store("notifications").success("Removed from library.");
              this.selectedItem.in_library = false;
              this.selectedItem = null;

              await this.fetchCatalogues();
            } else {
              const error = await response.json();
              Alpine.store("notifications").error(
                error.detail || "Failed to remove item.",
              );
            }
          } catch (e) {
            Alpine.store("notifications").error("Error: " + e.message);
          } finally {
            this.processing = false;
          }
        },
      );
    },

  };
}

document.addEventListener("alpine:init", () => {
  Alpine.data("homePage", homePage);
});
