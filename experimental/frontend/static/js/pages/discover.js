function discoverPage() {
    return {
        activeTab: 'popular_movies',
        items: [],
        page: 1,
        loading: false,
        hasMore: true,
        observer: null,

        init() {
            // Restore state if navigating back
            const state = history.state;
            if (state && state.discoverTab) {
                this.activeTab = state.discoverTab;
            }

            this.loadItems(true);
            this.setupInfiniteScroll();
        },

        async loadItems(reset = false) {
            if (this.loading || (!this.hasMore && !reset)) return;

            this.loading = true;
            if (reset) {
                this.page = 1;
                this.items = [];
                this.hasMore = true;
            }

            try {
                const response = await fetch(`/api/discover/${this.activeTab}?page=${this.page}`);
                if (!response.ok) throw new Error("Failed to load");

                const data = await response.json();

                if (data.items && data.items.length > 0) {
                    // Filter duplicates just in case
                    const newItems = data.items.filter(newItem =>
                        !this.items.some(existing => existing.uuid === newItem.uuid)
                    );
                    this.items = [...this.items, ...newItems];
                    this.page++;
                } else {
                    this.hasMore = false;
                }
            } catch (e) {
                console.error("Discover load failed:", e);
                this.hasMore = false;
            } finally {
                this.loading = false;
            }
        },

        switchTab(tab) {
            if (this.activeTab === tab) return;
            this.activeTab = tab;

            // Save state
            const url = new URL(window.location);
            history.replaceState({ discoverTab: tab }, '', url);

            this.loadItems(true);
        },

        setupInfiniteScroll() {
            const options = {
                root: null,
                rootMargin: '200px',
                threshold: 0.1
            };

            this.observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting && !this.loading && this.hasMore) {
                        this.loadItems();
                    }
                });
            }, options);

            const trigger = document.getElementById('infinite-scroll-trigger');
            if (trigger) this.observer.observe(trigger);
        },

        getPosterUrl(item) {
            const posterPath = item.poster_path || item.image_paths?.poster;
            if (!posterPath) return '/static/images/default_poster.png';

            const type = item.media_type || 'movie';
            const tmdbId = item.tmdb_id || item.id;

            // Use the global helper if available, otherwise construct backend URL
            if (window.getPosterUrl) return window.getPosterUrl(posterPath, type, tmdbId);

            return `/api/media/images/poster/${item.uuid}`;
        },

        showDetail(item) {
            // Save scroll position and tab
            const scrollPos = window.scrollY;
            history.replaceState({
                discoverTab: this.activeTab,
                scroll: scrollPos
            }, '', window.location);

            const type = item.media_type || 'movie';
            const identifier = item.uuid || `tmdb:${item.tmdb_id}`;
            window.location.href = `/media/${type}/${identifier}`;
        }
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.data('discoverPage', discoverPage);
});
