
const getPosterUrl = (path, mediaTypeOrItem = "movie", tmdbId = null) => {
  let type = mediaTypeOrItem;
  let id = tmdbId;

  if (typeof mediaTypeOrItem === "object" && mediaTypeOrItem !== null) {
    type = mediaTypeOrItem.media_type || "movie";

    if (type === "video" || type === "video_series") {
      id =
        mediaTypeOrItem.video_uuid ||
        mediaTypeOrItem.uuid ||
        mediaTypeOrItem.id;
    } else {

      id =
        mediaTypeOrItem.cache_key ||
        mediaTypeOrItem.tmdb_id ||
        mediaTypeOrItem.id;
    }
  }

  if ((!path || path === "null" || path === "") && type !== "video" && type !== "video_series")
    return "/static/images/default_poster.png";
  if (
    path.startsWith("http") ||
    path.startsWith("/static/") ||
    path.startsWith("/api/")
  )
    return path;

  if (type && id) {
    return `/api/media/images/${type}/${id}/poster?path=${encodeURIComponent(path)}`;
  }

  return `https://image.tmdb.org/t/p/w500${path}`;
};

const getBackdropUrl = (path, mediaType = "movie", tmdbId = null) => {
  if (!path) return "";
  if (path.startsWith("http") || path.startsWith("/api/")) return path;

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

  alert(`${title}\n\n${message}`);
}

window.getPosterUrl = getPosterUrl;
window.getBackdropUrl = getBackdropUrl;
window.fetchWithAuth = fetchWithAuth;
window.showAlert = showAlert;
