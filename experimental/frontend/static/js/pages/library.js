function libraryPage() {
  return {
    items: [],
    selectedItem: null,
    selectedItems: [],
    totalItems: 0,
    page: 1,
    pageSize: 20,
    totalPages: 1,
    filter: "all",
    viewMode: "grid",
    loading: true,
    refreshing: null,
    deleting: null,
    watchlistItems: [],

    async init() {

      const urlParams = new URLSearchParams(window.location.search);
      const filterParam = urlParams.get("filter");
      if (filterParam) {
        this.filter = filterParam;
      }

      await this.loadLibrary();
    },

    async loadLibrary() {
      this.loading = true;
      try {

        if (this.filter === "watchlist") {
          await this.loadWatchlist();
          return;
        }

        const params = new URLSearchParams({
          page: this.page,
          limit: this.pageSize,
        });

        if (this.filter !== "all") {
          params.append("type", this.filter === "movies" ? "movie" : "tv");
        }

        const response = await fetch(`/api/library/items?${params}`);
        const data = await response.json();
        this.items = data.items;
        this.totalItems = data.total;
        this.totalPages = Math.ceil(this.totalItems / this.pageSize);

        if (this.items.length === 0 && this.page > 1) {
          this.page--;
          await this.loadLibrary();
        }
      } catch (e) {
        console.error("Failed to load library items:", e);
      } finally {
        this.loading = false;
      }
    },

    async loadWatchlist() {
      this.loading = true;
      try {
        const response = await fetch("/api/watchlist/");
        if (response.ok) {
          const data = await response.json();
          this.watchlistItems = data;
          this.items = data;
          this.totalItems = this.items.length;
          this.totalPages = 1;
        } else {
          this.items = [];
          this.totalItems = 0;
        }
      } catch (e) {
        console.error("Failed to load watchlist:", e);
        this.items = [];
      } finally {
        this.loading = false;
      }
    },

    changePage(newPage) {
      if (newPage >= 1 && newPage <= this.totalPages) {
        this.page = newPage;
        this.loadLibrary();
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    },

    getPageNumbers() {
      const pages = [];
      const radius = 2;

      for (let i = 1; i <= this.totalPages; i++) {
        if (
          i === 1 ||
          i === this.totalPages ||
          (i >= this.page - radius && i <= this.page + radius)
        ) {
          pages.push(i);
        } else if (
          i === this.page - radius - 1 ||
          i === this.page + radius + 1
        ) {
          pages.push("...");
        }
      }

      return pages.filter((v, i, a) => v !== "..." || a[i - 1] !== "...");
    },

    getPosterUrl(path, itemSource = null) {
      const item = itemSource || {};

      const media = item.media || item;
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
      if (!posterPath) return "";
      const type = media.media_type || item.media_type || "movie";
      const tmdbId = media.tmdb_id || media.id || item.tmdb_id || item.id;
      return window.getPosterUrl(posterPath, type, tmdbId);
    },

    getQualities(item) {
      try {
        return JSON.parse(item.quality_versions || "[]");
      } catch {
        return [];
      }
    },

    async showDetail(item) {

      const mediaType = item.media_type || "movie";
      const identifier = item.uuid || item.tmdb_id || item.id;

      if (identifier) {
        window.location.href = `/media/${mediaType}/${identifier}`;
      } else {
        this.selectedItem = item;
      }
    },

    refreshItem(item) {
      Alpine.store("notifications").confirm(
        "Refresh Item",
        `Check for new episodes of "${item.title}"?`,
        async () => {
          this.refreshing = item.id;
          try {
            const response = await fetch(`/api/library/refresh/${item.id}`, {
              method: "POST",
            });
            const data = await response.json();
            Alpine.store("notifications").success(data.message);
            await this.loadLibrary();
            this.selectedItem = null;
          } catch (e) {
            Alpine.store("notifications").error(
              "Failed to refresh: " + e.message,
            );
          } finally {
            this.refreshing = null;
          }
        },
      );
    },

    removeItem(item) {
      Alpine.store("notifications").confirm(
        "Delete Item",
        `Delete "${item.title}" from library?`,
        async () => {
          this.deleting = item.id;
          try {
            await fetch(`/api/library/items/${item.id}`, { method: "DELETE" });
            await this.loadLibrary();
            await this.loadStats();
            this.selectedItem = null;
          } catch (e) {
            Alpine.store("notifications").error(
              "Failed to delete: " + e.message,
            );
          } finally {
            this.deleting = null;
          }
        },
      );
    },

    confirmPurge() {
      Alpine.store("notifications").confirm(
        "Purge Library",
        "Delete ALL items? This cannot be undone.",
        async () => {
          try {
            const response = await fetch("/api/library/purge", {
              method: "POST",
            });
            const data = await response.json();
            Alpine.store("notifications").success(data.message);
            await this.loadLibrary();
            await this.loadStats();
          } catch (e) {
            Alpine.store("notifications").error(
              "Failed to purge: " + e.message,
            );
          }
        },
      );
    },

    toggleSelection(item) {
      const index = this.selectedItems.findIndex(i => i.uuid === item.uuid);
      if (index === -1) {
        this.selectedItems.push(item);
      } else {
        this.selectedItems.splice(index, 1);
      }
    },

    isSelected(item) {
      return this.selectedItems.some(i => i.uuid === item.uuid);
    },

    toggleSelectAll() {
      if (this.selectedItems.length === this.items.length) {
        this.selectedItems = [];
      } else {
        this.selectedItems = [...this.items];
      }
    },

    get allSelected() {
      return this.items.length > 0 && this.selectedItems.length === this.items.length;
    },

    async deleteSelected() {
      if (this.selectedItems.length === 0) return;

      Alpine.store("notifications").confirm(
        "Delete Items",
        `Delete ${this.selectedItems.length} item(s) from library?`,
        async () => {
          try {

            for (const item of this.selectedItems) {
              await fetch(`/api/library/${item.uuid}`, { method: "DELETE" });
            }
            Alpine.store("notifications").success(
              `Deleted ${this.selectedItems.length} item(s)`
            );
            this.selectedItems = [];
            await this.loadLibrary();
          } catch (e) {
            Alpine.store("notifications").error(
              "Failed to delete: " + e.message
            );
          }
        }
      );
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
      const identifier = media.uuid || item.uuid || media.tmdb_id || item.tmdb_id || media.id || item.id;

      if (identifier) {
        window.location.href = `/media/${type}/${identifier}?autoPlay=true&time=${time}`;
      }
    },

    async removeContinueWatching(item) {
      try {
        await fetch(`/api/playback/progress/${item.id}`, { method: "DELETE" });
        await this.loadContinueWatching();
      } catch (e) {
        console.error("Failed to remove from continue watching:", e);
        Alpine.store("notifications").error(
          "Failed to remove item: " + e.message,
        );
      }
    },
  };
}

document.addEventListener("alpine:init", () => {
  Alpine.data("libraryPage", libraryPage);
});
