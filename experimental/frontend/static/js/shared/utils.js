// Common utility functions

const getPosterUrl = (path, mediaTypeOrItem = "movie", tmdbId = null) => {
  let type = mediaTypeOrItem;
  let id = tmdbId;

  // Handle being passed (path, item) where item has media_type and tmdb_id/uuid
  if (typeof mediaTypeOrItem === "object" && mediaTypeOrItem !== null) {
    type = mediaTypeOrItem.media_type || "movie";

    // For user content (video/video_series), use uuid
    // For TMDB content (movie/tv), use tmdb_id
    if (type === "video" || type === "video_series") {
      id =
        mediaTypeOrItem.video_uuid ||
        mediaTypeOrItem.uuid ||
        mediaTypeOrItem.id;
    } else {
      // Priority: cache_key > tmdb_id > id
      id =
        mediaTypeOrItem.cache_key ||
        mediaTypeOrItem.tmdb_id ||
        mediaTypeOrItem.id;
    }
  }

  // Return default if no path, UNLESS it's user content (which might have local file without DB path)
  if ((!path || path === "null" || path === "") && type !== "video" && type !== "video_series")
    return "/static/images/default_poster.png";
  if (
    path.startsWith("http") ||
    path.startsWith("/static/") ||
    path.startsWith("/api/")
  )
    return path;

  // If we have id and type, use local proxy
  if (type && id) {
    return `/api/media/images/${type}/${id}/poster?path=${encodeURIComponent(path)}`;
  }

  return `https://image.tmdb.org/t/p/w500${path}`;
};

const getBackdropUrl = (path, mediaType = "movie", tmdbId = null) => {
  if (!path) return ""; // Backdrops are often optional, keep empty for CSS overlay logic
  if (path.startsWith("http") || path.startsWith("/api/")) return path;

  // If we have id and type, use local proxy
  if (mediaType && tmdbId) {
    return `/api/media/images/${mediaType}/${tmdbId}/backdrop?path=${encodeURIComponent(path)}`;
  }

  return `https://image.tmdb.org/t/p/original${path}`;
};

async function fetchWithAuth(url, options = {}) {
  const token = localStorage.getItem("authToken");
  const headers = {
    "Content-Type": "application/json",
    ...options.headers,
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  return fetch(url, { ...options, headers });
}

function showAlert(title, message) {
  // Generic alert - pages can override
  alert(`${title}\n\n${message}`);
}

// Make globally available
window.getPosterUrl = getPosterUrl;
window.getBackdropUrl = getBackdropUrl;
window.fetchWithAuth = fetchWithAuth;
window.showAlert = showAlert;
