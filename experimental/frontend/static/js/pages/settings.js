/**
 * Settings Page
 */

function settingsPage() {
  return {
    tab: "general",
    saving: false,
    settings: {
      // General
      operation_profile: "standalone",
      web_player_enabled: true,
      strm_generation_enabled: false,
      jellyfin_integration_enabled: false,
      jellyfin_scan_enabled: false,
      local_library_enabled: true,
      torrent_playback_enabled: true,
      torrent_allow_upload: false,
      torrent_upload_limit_kbps: 0,
      default_play_action: "web",
      jfresolve_public_url: "http://127.0.0.1:8765",
      jellyfin_server_url: "",
      jellyfin_api_key: "",
      jellyfin_movie_path: "",
      jellyfin_tv_path: "",
      tmdb_api_key: "",
      metadata_cache_days: 30,

      show_stream_details: false,
      disable_discover_backdrop: false,
      screensaver_enabled: true,

      // Library Scanning
      library_scanner_mode: "lazy",

      // Advanced
      enable_failover: true,
      failover_grace_period: 45,
      failover_reset_window: 120,

      // Activity Tracking
      log_user_logins: true,
      log_playback_events: true,
      activity_retention_days: 90,

      // Server
      host: "0.0.0.0",
      port: 8765,
      session_timeout: 1440,
    },

    // Library Paths
    libraryPaths: [],
    showAddPathModal: false,
    newPath: {
      name: "",
      path: "",
      media_type: "movie",
    },
    editingPath: null,

    // Scanner status
    scannerStatus: null,

    // Resource monitoring
    resources: null,
    checkingResources: false,

    // Plugins
    plugins: [],

    // Users
    users: [],
    showCreateUserModal: false,
    showEditUserModal: false,
    newUser: {
      username: "",
      password: "",
      confirmPassword: "",
      is_superuser: false,
    },
    editingUser: null,

    // Toast notifications
    toast: {
      show: false,
      message: "",
      type: "info",
    },

    requests: [],

    async init() {
      await this.loadSettings();
      await this.loadLibraryPaths();
      await this.loadPlugins();
      await this.loadUsers();
      await this.loadScannerStatus();
    },

    async loadRequests() {
      try {
        const response = await fetch("/api/requests?all_users=true");
        if (!response.ok) throw new Error("Failed to load requests");
        this.requests = await response.json();
      } catch (error) {
        this.showToast(error.message, "error");
      }
    },

    async decideRequest(req, action) {
      try {
        const response = await fetch(`/api/requests/${req.id}/${action}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `Failed to ${action} request`);
        if (data.status === "failed") this.showToast(data.note, "error");
        await this.loadRequests();
      } catch (error) {
        this.showToast(error.message, "error");
      }
    },

    async loadSettings() {
      try {
        const response = await fetch("/api/settings/");
        if (!response.ok) throw new Error("Failed to load settings");

        const data = await response.json();
        // Merge loaded settings with defaults
        this.settings = { ...this.settings, ...data.settings };
      } catch (error) {
        console.error("Error loading settings:", error);
        this.showToast("Failed to load settings", "error");
      }
    },

    async saveSettings() {
      this.saving = true;
      try {
        const response = await fetch("/api/settings/", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ settings: this.settings }),
        });

        if (!response.ok) throw new Error("Failed to save settings");

        this.showToast("Settings saved successfully", "success");
      } catch (error) {
        console.error("Error saving settings:", error);
        this.showToast("Failed to save settings", "error");
      } finally {
        this.saving = false;
      }
    },

    async applyOperationProfile(profile) {
      this.saving = true;
      try {
        const response = await fetch("/api/settings/operation-profile", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile }),
        });
        if (!response.ok) throw new Error("Failed to apply operation profile");

        const data = await response.json();
        this.settings = { ...this.settings, ...data.settings };
        this.showToast(`${profile[0].toUpperCase()}${profile.slice(1)} profile applied`, "success");
      } catch (error) {
        console.error("Error applying operation profile:", error);
        this.showToast("Failed to apply operation profile", "error");
        await this.loadSettings();
      } finally {
        this.saving = false;
      }
    },

    async loadLibraryPaths() {
      try {
        const response = await fetch("/api/library-paths/");
        if (!response.ok) throw new Error("Failed to load library paths");

        const data = await response.json();
        this.libraryPaths = data;
      } catch (error) {
        console.error("Error loading library paths:", error);
        this.showToast("Failed to load library paths", "error");
      }
    },

    async addLibraryPath() {
      if (!this.newPath.name || !this.newPath.path) {
        this.showToast("Please fill in all fields", "error");
        return;
      }

      // If we are editing, call update instead
      if (this.editingPath) {
        await this.updateLibraryPath();
        return;
      }

      try {
        const response = await fetch("/api/library-paths/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: this.newPath.name,
            path: this.newPath.path,
            media_type: this.newPath.media_type,
            scan_interval: 0,
          }),
        });

        if (!response.ok) throw new Error("Failed to add library path");

        this.showToast("Library path added successfully", "success");
        this.showAddPathModal = false;
        this.resetPathForm();
        await this.loadLibraryPaths();
      } catch (error) {
        console.error("Error adding library path:", error);
        this.showToast("Failed to add library path", "error");
      }
    },

    async updateLibraryPath() {
      try {
        const response = await fetch(
          `/api/library-paths/${this.editingPath.id}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: this.newPath.name,
              path: this.newPath.path,
              scan_interval: 0,
            }),
          },
        );

        if (!response.ok) throw new Error("Failed to update library path");

        this.showToast("Library path updated successfully", "success");
        this.showAddPathModal = false;
        this.resetPathForm();
        await this.loadLibraryPaths();
      } catch (error) {
        console.error("Error updating library path:", error);
        this.showToast("Failed to update library path", "error");
      }
    },

    resetPathForm() {
      this.newPath = { name: "", path: "", media_type: "movie" };
      this.editingPath = null;
    },

    async deleteLibraryPath(id) {
      console.log("Delete library path clicked:", id);

      // Use notifications modal
      const notificationStore = Alpine.store("notifications");
      if (notificationStore && notificationStore.confirm) {
        console.log("Using notifications confirm");
        notificationStore.confirm(
          "Remove Library Path",
          "Are you sure you want to remove this library path? This will remove all media entries from this path.",
          async () => {
            console.log("Notifications confirm callback executed");
            await this.performDeleteLibraryPath(id);
          },
        );
      } else {
        console.log("Notifications store not available, using native confirm");
        // Fallback to native confirm
        if (
          confirm(
            "Are you sure you want to remove this library path? This will remove all media entries from this path.",
          )
        ) {
          await this.performDeleteLibraryPath(id);
        }
      }
    },

    async performDeleteLibraryPath(id) {
      console.log("Performing delete for path:", id);
      try {
        console.log("Sending DELETE request to:", `/api/library-paths/${id}`);
        const response = await fetch(`/api/library-paths/${id}`, {
          method: "DELETE",
        });

        console.log("Delete response status:", response.status);
        console.log("Delete response ok:", response.ok);

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          console.log("Error response data:", errorData);
          throw new Error(
            errorData.detail ||
              `HTTP ${response.status}: Failed to delete library path`,
          );
        }

        this.showToast("Library path removed successfully", "success");
        await this.loadLibraryPaths();
      } catch (error) {
        console.error("Error deleting library path:", error);
        this.showToast(
          "Failed to remove library path: " + error.message,
          "error",
        );
      }
    },

    editLibraryPath(path) {
      this.editingPath = path;
      this.newPath = {
        name: path.name,
        path: path.path,
        media_type: path.media_type,
      };
      this.showAddPathModal = true;
    },

    async restartServer() {
      Alpine.store("notifications").confirm(
        "Restart Server",
        "Are you sure you want to restart the server? This will disconnect all users.",
        async () => {
          await this.performRestartServer();
        },
      );
    },

    async performRestartServer() {
      try {
        const response = await fetch("/api/system/restart", { method: "POST" });
        if (!response.ok) throw new Error("Failed to restart server");

        this.showToast("Server restarting...", "info");
        // Reload page after 5 seconds
        setTimeout(() => window.location.reload(), 5000);
      } catch (error) {
        console.error("Error restarting server:", error);
        this.showToast("Failed to restart server", "error");
      }
    },

    // Plugin Management
    async loadPlugins() {
      try {
        const response = await fetch("/api/addons/");
        if (!response.ok) throw new Error("Failed to load plugins");

        this.plugins = await response.json();
      } catch (error) {
        console.error("Error loading plugins:", error);
        this.showToast("Failed to load plugins", "error");
      }
    },

    configurePlugin(plugin) {
      // If plugin has custom UI, navigate to it or open modal
      if (plugin.has_ui) {
        window.location.href = `/addons/${plugin.id}/configure`;
      } else {
        this.showToast("This plugin has no configuration UI", "info");
      }
    },

    async uninstallPlugin(plugin) {
      Alpine.store("notifications").confirm(
        "Uninstall Plugin",
        `Are you sure you want to uninstall "${plugin.manifest?.name || plugin.id}"?`,
        async () => {
          await this.performUninstallPlugin(plugin);
        },
      );
    },

    async performUninstallPlugin(plugin) {
      try {
        const response = await fetch(`/api/addons/${plugin.id}`, {
          method: "DELETE",
        });

        if (!response.ok) throw new Error("Failed to uninstall plugin");

        this.showToast("Plugin uninstalled successfully", "success");
        await this.loadPlugins();
      } catch (error) {
        console.error("Error uninstalling plugin:", error);
        this.showToast("Failed to uninstall plugin", "error");
      }
    },

    // User Management
    async loadUsers() {
      try {
        const response = await fetch("/api/users/");
        if (!response.ok) throw new Error("Failed to load users");

        this.users = await response.json();
      } catch (error) {
        console.error("Error loading users:", error);
        this.showToast("Failed to load users", "error");
      }
    },

    async createUser() {
      if (!this.newUser.username || !this.newUser.password) {
        this.showToast("Please fill in all required fields", "error");
        return;
      }

      if (this.newUser.password !== this.newUser.confirmPassword) {
        this.showToast("Passwords do not match", "error");
        return;
      }

      try {
        const response = await fetch("/api/users/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: this.newUser.username,
            password: this.newUser.password,
            is_superuser: this.newUser.is_superuser,
          }),
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || "Failed to create user");
        }

        this.showToast("User created successfully", "success");
        this.showCreateUserModal = false;
        this.resetNewUser();
        await this.loadUsers();
      } catch (error) {
        console.error("Error creating user:", error);
        this.showToast(error.message || "Failed to create user", "error");
      }
    },

    editUser(user) {
      this.editingUser = {
        ...user,
        permissions: user.permissions || { allowed_paths: [] },
      };
      this.showEditUserModal = true;
    },

    async updateUser() {
      if (!this.editingUser) return;

      try {
        const body = {
          username: this.editingUser.username,
          is_active: this.editingUser.is_active,
          is_superuser: this.editingUser.is_superuser,
          permissions: this.editingUser.permissions || null,
        };

        if (this.editingUser.new_password) {
          body.password = this.editingUser.new_password;
        }

        const response = await fetch(`/api/users/${this.editingUser.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        if (!response.ok) throw new Error("Failed to update user");

        this.showToast("User updated successfully", "success");
        this.showEditUserModal = false;
        this.editingUser = null;
        await this.loadUsers();
      } catch (error) {
        console.error("Error updating user:", error);
        this.showToast("Failed to update user", "error");
      }
    },

    async deleteUser(user) {
      Alpine.store("notifications").confirm(
        "Delete User",
        `Are you sure you want to delete user "${user.username}"?`,
        async () => {
          await this.performDeleteUser(user);
        },
      );
    },

    async performDeleteUser(user) {
      try {
        const response = await fetch(`/api/users/${user.id}`, {
          method: "DELETE",
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || "Failed to delete user");
        }

        this.showToast("User deleted successfully", "success");
        await this.loadUsers();
      } catch (error) {
        console.error("Error deleting user:", error);
        this.showToast(error.message || "Failed to delete user", "error");
      }
    },

    toggleUserPath(pathId) {
      if (!this.editingUser.permissions) {
        this.editingUser.permissions = { allowed_paths: [] };
      }
      if (!this.editingUser.permissions.allowed_paths) {
        this.editingUser.permissions.allowed_paths = [];
      }

      const index = this.editingUser.permissions.allowed_paths.indexOf(pathId);
      if (index > -1) {
        this.editingUser.permissions.allowed_paths.splice(index, 1);
      } else {
        this.editingUser.permissions.allowed_paths.push(pathId);
      }
    },

    resetNewUser() {
      this.newUser = {
        username: "",
        password: "",
        confirmPassword: "",
        is_superuser: false,
      };
    },

    // Library Scanning
    scanning: false,
    showRescanModal: false,
    rescanPath: null,

    async startScan(force = false) {
      if (this.scanning) return;
      this.scanning = true;
      await this.performScan({ force_rescan: force });
      // Reset scanning state after a delay or let user refresh manually
      // Since we don't poll anymore, we just show a toast
      setTimeout(() => {
        this.scanning = false;
      }, 2000);
    },

    openRescanModal(path) {
      this.rescanPath = path;
      this.showRescanModal = true;
    },

    async startPathScan(force) {
      if (this.scanning) {
        this.showToast("Scan already in progress", "warning");
        return;
      }
      this.scanning = true;
      const pathId = this.rescanPath.id;
      this.showRescanModal = false;
      this.rescanPath = null;
      await this.performScan({ path_id: pathId, force_rescan: force });
      setTimeout(() => {
        this.scanning = false;
      }, 2000);
    },

    async performScan(payload) {
      try {
        const response = await fetch("/api/library/scan/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        if (response.ok) {
          this.showToast("Library scan started", "success");
        } else {
          const err = await response.json();
          this.showToast("Failed to start scan: " + err.detail, "error");
        }
      } catch (e) {
        this.showToast("Scan error: " + e.message, "error");
      }
    },

    async syncCatalogues() {
      if (this.saving) return;
      this.saving = true;
      try {
        const response = await fetch("/api/system/tasks/catalogue-sync", {
          method: "POST",
        });

        if (response.ok) {
          const data = await response.json();
          this.showToast(data.message || "Catalogue sync complete", "success");
        } else {
          const err = await response.json();
          this.showToast(err.detail || "Failed to start sync", "error");
        }
      } catch (e) {
        this.showToast("Sync error: " + e.message, "error");
      } finally {
        this.saving = false;
      }
    },

    async loadScannerStatus() {
      try {
        const response = await fetch("/api/library/scan/status");
        if (response.ok) {
          this.scannerStatus = await response.json();
        }
      } catch (error) {
        console.error("Error loading scanner status:", error);
      }
    },

    async updateScannerMode() {
      try {
        const response = await fetch("/api/settings/scanner-mode", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: this.settings.library_scanner_mode }),
        });

        if (response.ok) {
          this.showToast("Scanner mode updated", "success");
          // Reload scanner status to show updated watchers
          await this.loadScannerStatus();
        } else {
          const err = await response.json();
          this.showToast("Failed to update mode: " + err.detail, "error");
        }
      } catch (error) {
        this.showToast(
          "Error updating scanner mode: " + error.message,
          "error",
        );
      }
    },

    async checkResources() {
      try {
        this.checkingResources = true;
        const response = await fetch("/api/system/resources");
        if (response.ok) {
          this.resources = await response.json();
        } else {
          this.showToast("Failed to get system resources", "error");
        }
      } catch (error) {
        console.error("Error checking resources:", error);
        this.showToast("Error checking resources: " + error.message, "error");
      } finally {
        this.checkingResources = false;
      }
    },

    showToast(message, type = "info") {
      this.toast = { show: true, message, type };
      setTimeout(() => {
        this.toast.show = false;
      }, 3000);
    },
  };
}
