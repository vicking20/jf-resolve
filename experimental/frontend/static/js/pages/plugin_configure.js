document.addEventListener("alpine:init", () => {
    Alpine.data("pluginConfig", (pluginId) => ({
        pluginId: pluginId,
        loading: true,
        saving: false,
        config: {},

        async init() {
            await this.loadPluginData();
        },

        async loadPluginData() {
            this.loading = true;
            try {
                // 1. Load UI HTML
                const uiResponse = await fetch(`/api/addons/${this.pluginId}/ui`);
                if (!uiResponse.ok) throw new Error("Failed to load plugin UI");
                const uiHtml = await uiResponse.text();

                // Inject HTML
                this.$refs.uiContainer.innerHTML = uiHtml;

                // 2. Load Current Config
                const configResponse = await fetch(`/api/addons/${this.pluginId}/config`);
                if (!configResponse.ok) throw new Error("Failed to load plugin config");
                this.config = await configResponse.json();

                // 3. Populate Fields
                this.populateFields();
            } catch (error) {
                console.error("Error loading plugin data:", error);
                Alpine.store("notifications").error(error.message);
            } finally {
                this.loading = false;
            }
        },

        populateFields() {
            // Find all inputs, selects, textareas in the container
            const container = this.$refs.uiContainer;
            const inputs = container.querySelectorAll("input, select, textarea");

            inputs.forEach((input) => {
                const name = input.getAttribute("name");
                if (name && this.config[name] !== undefined) {
                    if (input.type === "checkbox") {
                        input.checked = !!this.config[name];
                    } else {
                        input.value = this.config[name];
                    }
                }
            });
        },

        async saveConfig() {
            this.saving = true;

            try {
                // Collect data from fields
                const container = this.$refs.uiContainer;
                const inputs = container.querySelectorAll("input, select, textarea");
                const newConfig = { ...this.config };

                inputs.forEach((input) => {
                    const name = input.getAttribute("name");
                    if (name) {
                        if (input.type === "checkbox") {
                            newConfig[name] = input.checked;
                        } else if (input.type === "number") {
                            newConfig[name] = parseFloat(input.value);
                        } else {
                            newConfig[name] = input.value;
                        }
                    }
                });

                const response = await fetch(`/api/addons/${this.pluginId}/config`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(newConfig),
                });

                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || "Failed to save configuration");
                }

                Alpine.store("notifications").success("Configuration saved successfully");

                this.config = newConfig;
            } catch (error) {
                console.error("Error saving config:", error);
                Alpine.store("notifications").error(error.message);
            } finally {
                this.saving = false;
            }
        },
    }));
});
