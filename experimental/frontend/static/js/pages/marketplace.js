function marketplacePage() {
  return {
    repositories: [],
    allPlugins: [],
    filteredPlugins: [],
    customManifestUrl: "",
    installing: false,
    loading: true,
    showAddRepositoryModal: false,
    searchQuery: "",
    selectedRepository: null,

    async init() {
      await this.loadMarketplace();
    },

    async loadMarketplace() {
      this.loading = true;
      try {
        const response = await window.fetchWithAuth("/api/addons/marketplace");
        if (response.ok) {
          const data = await response.json();
          this.repositories = data.repositories || [];
          this.allPlugins = [];
          this.repositories.forEach(repo => {
            (repo.plugins || []).forEach(plugin => {
              plugin.tags = plugin.tags || [];
              plugin.repository = repo;
              this.allPlugins.push(plugin);
            });
          });
          this.filterPlugins();
        } else {
          Alpine.store("notifications").error("Failed to load marketplace");
        }
      } catch (e) {
        Alpine.store("notifications").error("Error loading marketplace: " + e.message);
      } finally {
        this.loading = false;
      }
    },

    filterPlugins() {
      if (!this.searchQuery) {
        this.filteredPlugins = [...this.allPlugins];
      } else {
        const query = this.searchQuery.toLowerCase();
        this.filteredPlugins = this.allPlugins.filter(plugin =>
          (plugin.name || "").toLowerCase().includes(query) ||
          (plugin.description || "").toLowerCase().includes(query) ||
          (plugin.tags || []).some(tag => tag.toLowerCase().includes(query))
        );
      }
    },

    async installPlugin(plugin) {
      if (this.installing) return;

      this.installing = true;
      try {
        const params = new URLSearchParams({
          plugin_id: plugin.id,
          repository_url: plugin.repository.url
        });

        const response = await window.fetchWithAuth(`/api/addons/install?${params}`, {
          method: "POST",
        });

        if (response.ok) {
          const data = await response.json();
          Alpine.store("notifications").success("Plugin installed successfully!");
          // Reload marketplace to update status
          await this.loadMarketplace();
        } else {
          const error = await response.json();
          Alpine.store("notifications").error(`Failed to install plugin: ${error.detail}`);
        }
      } catch (e) {
        Alpine.store("notifications").error(`Failed to install plugin: ${e.message}`);
      } finally {
        this.installing = false;
      }
    },

    async updatePlugin(plugin) {
      if (this.installing) return;

      this.installing = true;
      try {
        const params = new URLSearchParams({
          repository_url: plugin.repository.url
        });

        const response = await window.fetchWithAuth(`/api/addons/update/${plugin.id}?${params}`, {
          method: "POST",
        });

        if (response.ok) {
          const data = await response.json();
          Alpine.store("notifications").success(`Plugin updated to v${data.new_version}!`);
          // Reload marketplace to update status
          await this.loadMarketplace();
        } else {
          const error = await response.json();
          Alpine.store("notifications").error(`Failed to update plugin: ${error.detail}`);
        }
      } catch (e) {
        Alpine.store("notifications").error(`Failed to update plugin: ${e.message}`);
      } finally {
        this.installing = false;
      }
    },

    async uninstallPlugin(plugin) {
      if (this.installing) return;

      if (!confirm(`Are you sure you want to uninstall ${plugin.name}?`)) {
        return;
      }

      const deleteData = confirm("Do you want to delete the plugin data/settings as well?");

      this.installing = true;
      try {
        const response = await window.fetchWithAuth(`/api/addons/${plugin.id}?delete_data=${deleteData}`, {
          method: "DELETE",
        });

        if (response.ok) {
          Alpine.store("notifications").success("Plugin uninstalled successfully");
          await this.loadMarketplace();
        } else {
          Alpine.store("notifications").error("Failed to uninstall plugin");
        }
      } catch (e) {
        Alpine.store("notifications").error("Error uninstalling plugin: " + e.message);
      } finally {
        this.installing = false;
      }
    },

    async addRepository() {
      if (!this.customManifestUrl) {
        Alpine.store("notifications").error("Please enter a repository URL");
        return;
      }

      this.customManifestUrl = this.customManifestUrl.trim();
      this.loading = true;
      try {
        // The server validates manifests; browser fetches can fail due to CORS.
        // Get current repositories
        const getResponse = await window.fetchWithAuth("/api/addons/repositories");
        if (!getResponse.ok) throw new Error("Failed to get repositories");

        const data = await getResponse.json();
        const repositories = data.repositories || [];

        // Add new repository if not already present
        if (!repositories.includes(this.customManifestUrl)) {
          repositories.push(this.customManifestUrl);

          // Save updated list
          const postResponse = await window.fetchWithAuth("/api/addons/repositories", {
            method: "POST",
            body: JSON.stringify(repositories),
          });

          if (postResponse.ok) {
            Alpine.store("notifications").success("Repository added successfully!");
            this.showAddRepositoryModal = false;
            this.customManifestUrl = "";
            // Reload marketplace to include new repository
            await this.loadMarketplace();
          } else {
            const error = await postResponse.json().catch(() => ({}));
            Alpine.store("notifications").error(error.detail || "Failed to save repository");
            this.loading = false; // Reset loading if we didn't reload
          }
        } else {
          Alpine.store("notifications").info("Repository already added");
          this.loading = false;
        }

      } catch (e) {
        Alpine.store("notifications").error(`Failed to add repository: ${e.message}`);
        this.loading = false;
      }
    },


  };
}
