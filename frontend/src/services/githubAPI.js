/**
 * Pipeline API Service (backend-only, no direct GitHub calls from frontend)
 */

const PIPELINE_API_BASE = import.meta.env.VITE_PIPELINE_API_BASE || "http://localhost:8000";
const TOKEN_KEY = "access_token";

class PipelineService {
  normalizeUrlForId(url) {
    try {
      const u = new URL(url);
      const host = u.hostname.replace(/^www\./i, "").toLowerCase();
      const path = u.pathname.replace(/\/+$/, "") || "/";
      const params = new URLSearchParams(u.search);
      const kept = [];
      for (const [k, v] of params.entries()) {
        const lk = k.toLowerCase();
        if (lk.startsWith("utm_") || ["gclid", "fbclid", "mc_cid", "mc_eid"].includes(lk)) continue;
        kept.push([k, v]);
      }
      const qs = kept.length ? "?" + new URLSearchParams(kept).toString() : "";
      return `${u.protocol.toLowerCase()}//${host}${path}${qs}`;
    } catch {
      return (url || "").trim();
    }
  }

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
        credentials: "include",
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

  async refreshAccessToken() {
    try {
      const res = await fetch(`${PIPELINE_API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json"
        },
        body: JSON.stringify({})
      });

      if (!res.ok) return false;
      const data = await res.json().catch(() => ({}));
      if (!data?.access_token) return false;

      this.setToken(data.access_token);
      return true;
    } catch (_err) {
      return false;
    }
  }

  async fetchWithAuthRetry(url, init = {}) {
    const first = await fetch(url, {
      ...init,
      credentials: "include",
      headers: {
        ...(init.headers || {}),
        ...this.getHeaders()
      }
    });

    if (first.status !== 401) return first;

    const refreshed = await this.refreshAccessToken();
    if (!refreshed) {
      this.handleUnauthorized();
      return first;
    }

    const second = await fetch(url, {
      ...init,
      credentials: "include",
      headers: {
        ...(init.headers || {}),
        ...this.getHeaders()
      }
    });

    if (second.status === 401) {
      this.handleUnauthorized();
    }
    return second;
  }

  handleUnauthorized() {
    this.clearToken();
    window.dispatchEvent(new Event("auth:expired"));
  }

  async logout() {
    try {
      await fetch(`${PIPELINE_API_BASE}/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: this.getHeaders()
      });
    } finally {
      this.clearToken();
    }
  }

  async triggerPipeline(payload = {}) {
    try {
      const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/pipeline/trigger`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });

      const data = await res.json().catch(() => ({}));
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
      const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/pipeline/status`, {
        method: "GET"
      });

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
      const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/pipeline/latest-artifact`, {
        method: "GET"
      });

      if (!res.ok) return null;
      const data = await res.json();

      if (!data || !data.downloadURL) return null;
      return data;
    } catch (error) {
      console.error("Error fetching latest artifact URL:", error);
      return null;
    }
  }

  async triggerTrendAnalysis(payload = {}) {
    try {
      const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/trends/trigger`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return { success: false, error: data.detail || data.error || `HTTP ${res.status}` };
      }
      return { success: true, ...data };
    } catch (err) {
      return { success: false, error: err.message };
    }
  }

  async downloadLatestArtifactZip() {
    const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/pipeline/download-latest-artifact`, {
      method: "GET"
    });

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

  async getCurrentWeekStars() {
    try {
      const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/stars/current-week`, { method: "GET" });
      if (!res.ok) return { success: false, stars: [] };
      const data = await res.json();
      return { success: true, stars: data.stars || [] };
    } catch (err) {
      return { success: false, error: err.message, stars: [] };
    }
  }

  async addStar(article) {
    try {
      const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/stars`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: article.title,
          url: article.url,
          publication: article.publication,
          summary: article.summary,
          author: article.journalist || "Unknown",
          score: Number(article.score || 0),
        })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return { success: false, error: data.detail || `HTTP ${res.status}` };
      return { success: true, ...data };
    } catch (err) {
      return { success: false, error: err.message };
    }
  }

  async removeStarById(starId) {
    try {
      const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/stars/${encodeURIComponent(starId)}`, {
        method: "DELETE"
      });
      if (!res.ok) return { success: false, error: `HTTP ${res.status}` };
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  }

  async removeStarByArticleId(articleId) {
    try {
      const res = await this.fetchWithAuthRetry(`${PIPELINE_API_BASE}/stars`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ article_id: articleId })
      });
      if (!res.ok) return { success: false, error: `HTTP ${res.status}` };
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  }

  // Backward compatibility if anything else still calls this
  async getWorkflowRuns() {
    return [];
  }
}

export default new PipelineService();
