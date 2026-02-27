/**
 * Pipeline API Service (backend-only, no direct GitHub calls from frontend)
 */

const PIPELINE_API_BASE = import.meta.env.VITE_PIPELINE_API_BASE || "http://localhost:8000";
const TOKEN_KEY = "access_token";

class PipelineService {
  getToken() {
    return sessionStorage.getItem(TOKEN_KEY);
  }

  setToken(token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  }

  clearToken() {
    sessionStorage.removeItem(TOKEN_KEY);
  }

  getHeaders() {
    const headers = {
      "Accept": "application/json"
    };

    const token = this.getToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    return headers;
  }

  async login(password) {
    try {
      const res = await fetch(`${PIPELINE_API_BASE}/auth/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json"
        },
        body: JSON.stringify({ password })
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return { success: false, error: data.detail || `HTTP ${res.status}` };
      }

      this.setToken(data.access_token);
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  }

  handleUnauthorized() {
    this.clearToken();
    window.dispatchEvent(new Event("auth:expired"));
  }

  async triggerPipeline(payload = {}) {
    try {
      const res = await fetch(`${PIPELINE_API_BASE}/pipeline/trigger`, {
        method: "POST",
        headers: {
          ...this.getHeaders(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });

      const data = await res.json().catch(() => ({}));
      if (res.status === 401) {
        this.handleUnauthorized();
      }

      if (!res.ok) {
        return { success: false, error: data.detail || data.error || `HTTP ${res.status}` };
      }

      return { success: true, ...data };
    } catch (err) {
      return { success: false, error: err.message };
    }
  }

  async getLatestRunStatus() {
    try {
      const res = await fetch(`${PIPELINE_API_BASE}/pipeline/status`, {
        method: "GET",
        headers: this.getHeaders()
      });

      if (res.status === 401) {
        this.handleUnauthorized();
      }
      if (!res.ok) return null;
      return await res.json();
    } catch (error) {
      console.error("Error fetching latest run status:", error);
      return null;
    }
  }

  async isWorkflowRunning() {
    const status = await this.getLatestRunStatus();
    if (!status) return false;
    return status.status === "queued" || status.status === "in_progress" || status.status === "running";
  }

  async getLatestArtifactDownloadURL() {
    try {
      const res = await fetch(`${PIPELINE_API_BASE}/pipeline/latest-artifact`, {
        method: "GET",
        headers: this.getHeaders()
      });

      if (res.status === 401) {
        this.handleUnauthorized();
      }
      if (!res.ok) return null;
      const data = await res.json();

      if (!data || !data.downloadURL) return null;
      return data;
    } catch (error) {
      console.error("Error fetching latest artifact URL:", error);
      return null;
    }
  }

  async downloadLatestArtifactZip() {
    const res = await fetch(`${PIPELINE_API_BASE}/pipeline/download-latest-artifact`, {
      method: "GET",
      headers: this.getHeaders()
    });

    if (res.status === 401) {
      this.handleUnauthorized();
      return { success: false, error: "Unauthorized" };
    }

    if (res.status === 404) {
      return { success: false, error: "No PDF available yet. Run the pipeline first to generate a PDF." };
    }

    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try {
        const data = await res.json();
        message = data.detail || message;
      } catch (_e) {
        // no-op
      }
      return { success: false, error: message };
    }

    const blob = await res.blob();
    const contentDisposition = res.headers.get("content-disposition") || "";
    const match = contentDisposition.match(/filename="([^"]+)"/i);
    const filename = match?.[1] || "latest-artifact.zip";
    return { success: true, blob, filename };
  }

  // Backward compatibility if anything else still calls this
  async getWorkflowRuns() {
    return [];
  }
}

export default new PipelineService();
