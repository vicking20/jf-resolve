function loginPage() {
  return {
    username: "",
    password: "",
    loading: false,
    error: "",

    async login() {
      this.loading = true;
      this.error = "";

      try {
        const response = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: this.username,
            password: this.password,
          }),
        });

        if (!response.ok) {
          const data = await response.json();
          throw new Error(data.detail || "Login failed");
        }

        // On successful login, the backend sets a session cookie.
        // Redirect to the main page.
        window.location.href = "/";
      } catch (e) {
        this.error = e.message;
      } finally {
        this.loading = false;
      }
    },
  };
}

// Check if setup is needed
fetch("/api/auth/status")
  .then((r) => r.json())
  .then((data) => {
    if (data.setup_needed) {
      window.location.href = "/setup";
    }
  });
