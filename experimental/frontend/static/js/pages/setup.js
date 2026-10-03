function setupWizard() {
  return {
    step: 1,
    loading: false,
    syncing: false,
    syncMessage: "",
    error: "",
    profiles: [
      { id: "standalone", title: "Standalone", description: "Browse and play directly in JF-Resolve." },
      { id: "jellyfin", title: "Jellyfin", description: "Create STRM libraries for Jellyfin." },
      { id: "hybrid", title: "Hybrid", description: "Use the web player and Jellyfin together." },
    ],
    config: {
      username: "",
      password: "",
      passwordConfirm: "",
      tmdb_api_key: "",
      operation_profile: "standalone",
      jfresolve_public_url: window.location.origin,
      jellyfin_server_url: "",
      jellyfin_api_key: "",
      jellyfin_movie_path: "",
      jellyfin_tv_path: "",
    },

    validatePassword() {
      if (this.config.password !== this.config.passwordConfirm) {
        this.error = "Passwords do not match";
        return;
      }
      if (this.config.password.length < 6) {
        this.error = "Password must be at least 6 characters";
        return;
      }
      this.error = "";
      this.step++;
    },

    complete() {
      this.loading = true;
      this.error = "";

      fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: this.config.username,
          password: this.config.password,
        }),
      })
        .then((response) => {
          if (!response.ok) {
            return response.json().then((err) => {
              throw new Error(err.detail || "Failed to create user");
            });
          }
          return response.json();
        })

        .then(() => {
          return fetch("/api/settings/operation-profile", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profile: this.config.operation_profile }),
          });
        })
        .then((response) => {
          if (!response.ok) throw new Error("Failed to save operating mode");

          return fetch("/api/settings/", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              settings: {
                tmdb_api_key: this.config.tmdb_api_key,
                jfresolve_public_url: this.config.jfresolve_public_url,
                jellyfin_server_url: this.config.jellyfin_server_url,
                jellyfin_api_key: this.config.jellyfin_api_key,
                jellyfin_movie_path: this.config.jellyfin_movie_path,
                jellyfin_tv_path: this.config.jellyfin_tv_path,
              },
            }),
          });
        })
        .then((response) => {
          if (!response.ok) {
            return response.json().then((err) => {
              throw new Error(err.detail || "Failed to save settings");
            });
          }
          return response.json();
        })

        .then(() => {
          this.step = 5;
          this.loading = false;
          if (!this.config.tmdb_api_key) {
            this.syncMessage = "Setup is complete. Add a TMDB key later to load online catalogues.";
            return null;
          }
          this.syncing = true;
          this.syncMessage = "Syncing initial catalogues...";

          return fetch("/api/catalogues/sync", { method: "POST" });
        })
        .then((response) => {
          if (!response) return;
          if (response.ok) {
            this.syncMessage = "Catalogue sync completed successfully!";
          } else {
            return response.json().then((err) => {
              this.syncMessage = `Catalogue sync failed: ${err.detail || "Unknown error"}. You can re-run it from Settings later.`;
            });
          }
        })
        .catch((e) => {
          this.error = e.message;
          this.loading = false;
          this.syncing = false;
        })
        .finally(() => {
          this.syncing = false;
        });
    },
  };
}
