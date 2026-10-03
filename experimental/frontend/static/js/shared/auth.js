
async function logout() {
  try {

    await fetch("/api/auth/logout", { method: "POST" });
  } catch (e) {
    console.error("Logout error:", e);
  }

  window.location.href = "/login";
}

let _currentUser = null;
let _currentUserPromise = null;

async function getCurrentUser(forceRefresh = false) {
  if (_currentUser && !forceRefresh) {
    return _currentUser;
  }
  if (_currentUserPromise && !forceRefresh) {
    return _currentUserPromise;
  }

  _currentUserPromise = fetch("/api/auth/status")
    .then((res) => {
      if (!res.ok) {
        throw new Error("Not authenticated");
      }
      return res.json();
    })
    .then((data) => {
      if (data.is_authenticated) {
        _currentUser = data.user;
        return _currentUser;
      }
      _currentUser = null;
      return null;
    })
    .catch(() => {
      _currentUser = null;
      return null;
    })
    .finally(() => {
      _currentUserPromise = null;
    });
  return _currentUserPromise;
}

window.logout = logout;
window.getCurrentUser = getCurrentUser;
