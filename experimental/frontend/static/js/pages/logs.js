function logsPage() {
  return {
    logType: "error",
    scanJobs: [],
    logs: [],

    async init() {
      await this.loadLogs();
    },

    async changeLogType(type) {
      this.logType = type;
      await this.loadLogs();
    },

    async loadLogs() {
      if (this.logType === 'scan_jobs') {
        await this.loadScanJobs();
        return;
      }

      const response = await fetch(
        `/api/system/logs?type=${this.logType}&limit=100`,
      );
      const data = await response.json();
      this.logs = data.lines;
    },

    async loadScanJobs() {
      try {
        const response = await fetch(`/api/library/scan/jobs?limit=50`);
        if (response.ok) {
          this.scanJobs = await response.json();
        }
      } catch (e) {
        console.error("Failed to load scan jobs:", e);
      }
    },

    async cancelJob(jobId) {
      if (!confirm("Are you sure you want to cancel this scan job?")) return;

      try {
        const response = await fetch(`/api/library/scan/jobs/${jobId}`, {
          method: "DELETE",
        });

        if (response.ok) {
          alert("Scan job cancelled successfully");
          this.loadLogs(); // Refresh the list
        } else {
          alert("Failed to cancel scan job");
        }
      } catch (error) {
        console.error("Error cancelling job:", error);
        alert("Error cancelling scan job");
      }
    },

    calculateDuration(job) {
      if (!job.started_at || !job.completed_at) return "-";
      const start = new Date(job.started_at);
      const end = new Date(job.completed_at);
      const diffMs = end - start;
      const diffSecs = Math.floor(diffMs / 1000);

      if (diffSecs < 60) return `${diffSecs}s`;
      return `${Math.floor(diffSecs / 60)}m ${diffSecs % 60}s`;
    },

    async downloadLogs() {
      window.open(`/api/system/logs/download?type=${this.logType}`, "_blank");
    },
  };
}

// Register with Alpine
document.addEventListener("alpine:init", () => {
  Alpine.data("logsPage", logsPage);
});
