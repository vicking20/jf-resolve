function searchPage() {
  return {
    query: "",
    results: [],
    movieResults: [],
    tvResults: [],
    videoResults: [],
    videoSeriesResults: [],
    selectedItem: null,
    adding: false,
    successMessage: "",

    async init() {
      // Auto-focus search input
      this.$nextTick(() => {
        const searchInput = document.querySelector('input[x-model="query"]');
        if (searchInput) {
          searchInput.focus();
        }
      });
    },



    async search() {
      if (this.query.length < 2) {
        this.results = [];
        this.movieResults = [];
        this.tvResults = [];
        this.videoResults = [];
        this.videoSeriesResults = [];
        return;
      }

      try {
        const response = await fetch(
          `/api/search/?query=${encodeURIComponent(this.query)}`,
        );
        if (!response.ok) {
          throw new Error(`Search failed: ${response.status}`);
        }
        const data = await response.json();
        this.results = data.results || [];

        // Split by type
        this.movieResults = this.results.filter((r) => r.media_type === "movie");
        this.tvResults = this.results.filter((r) => r.media_type === "tv");
        this.videoResults = this.results.filter((r) => r.media_type === "video");
        this.videoSeriesResults = this.results.filter(
          (r) => r.media_type === "video_series",
        );
      } catch (error) {
        console.error("Search error:", error);
        this.results = [];
        this.movieResults = [];
        this.tvResults = [];
        this.videoResults = [];
        this.videoSeriesResults = [];
      }
    },

    getPosterUrl(path, itemSource = null) {
      const item = itemSource || {};
      const uuid = item.uuid || (item.media && item.media.uuid);

      if (uuid) {
        return `/api/media/images/poster/${uuid}`;
      }

      const posterPath = path || (item.image_paths && item.image_paths.poster) || item.poster_path || item.poster_url;
      if (!posterPath) return "";
      const type = item.media_type || "movie";
      const tmdbId = item.id || item.tmdb_id;
      return window.getPosterUrl(posterPath, type, tmdbId);
    },

    updateHero(item) {
      if (!item) return;

      const uuid = item.uuid || (item.media && item.media.uuid);
      let url;

      if (uuid) {
        url = `/api/media/images/backdrop/${uuid}`;
      } else {
        const backdrop = item.backdrop_path || item.backdrop_url;
        const type = item.media_type || "movie";
        const tmdbId = item.id || item.tmdb_id;
        url = window.getBackdropUrl(backdrop, type, tmdbId);
      }

      if (url) {
        window.dispatchEvent(
          new CustomEvent("update-background", { detail: url }),
        );
      }
    },

    // Unified notifications used via Alpine.store("notifications")

    async showDetail(item) {
      if (!item) return;



      const type = item.media_type || "movie";
      const identifier = item.uuid || item.tmdb_id || item.id;

      if (identifier) {
        window.location.href = `/media/${type}/${identifier}`;
      }
    },

    async addToLibrary() {
      this.adding = true;
      try {
        const response = await fetch("/api/library/add", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            identifier: `tmdb:${this.selectedItem.tmdb_id}`,
            media_type: this.selectedItem.media_type,
          }),
        });

        if (response.ok) {
          Alpine.store("notifications").success(
            `"${this.selectedItem.title}" has been added to your library!`,
          );
          this.selectedItem.in_library = true;
          this.selectedItem = null;
          await this.search(); // Keep the search call
        } else {
          const error = await response.json();
          Alpine.store("notifications").error(
            "Failed to add: " + (error.detail || "Unknown error"),
          );
        }
      } catch (error) {
        Alpine.store("notifications").error(
          "Failed to add to library: " + error.message,
        );
      } finally {
        this.adding = false;
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
        `Are you sure you want to remove "${this.selectedItem.title}" from your library?`,
        async () => {
          this.adding = true;
          try {
            const response = await fetch(`/api/library/items/${itemId}`, {
              method: "DELETE",
            });

            if (response.ok) {
              Alpine.store("notifications").success("Removed from library.");
              this.selectedItem.in_library = false;
              this.selectedItem = null;
              await this.search();
            } else {
              const error = await response.json();
              Alpine.store("notifications").error(
                error.detail || "Failed to remove item.",
              );
            }
          } catch (e) {
            Alpine.store("notifications").error("Error: " + e.message);
          } finally {
            this.adding = false;
          }
        },
      );
    },
  };
}

// Register with Alpine
document.addEventListener("alpine:init", () => {
  Alpine.data("searchPage", searchPage);
});
