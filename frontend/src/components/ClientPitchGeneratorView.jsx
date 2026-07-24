import { useCallback, useEffect, useRef, useState } from "react";
import { Lightbulb, Plus, RefreshCw } from "lucide-react";
import githubAPI from "../services/githubAPI";

export default function ClientPitchGeneratorView() {
  const [clients, setClients] = useState([]);
  const [clientId, setClientId] = useState("");
  const [mode, setMode] = useState("auto");
  const [maxPitches, setMaxPitches] = useState(5);
  const [pitches, setPitches] = useState([]);
  const [pitchRunId, setPitchRunId] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [isTriggering, setIsTriggering] = useState(false);
  const [isViewing, setIsViewing] = useState(false);
  const [showNewClient, setShowNewClient] = useState(false);
  const [savingClient, setSavingClient] = useState(false);
  const pollRef = useRef(null);
  const [newClient, setNewClient] = useState({
    client_name: "",
    client_description: "",
  });

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const loadClients = useCallback(async () => {
    const res = await githubAPI.getClients();
    if (!res.success) {
      setError(res.error || "Failed to load clients.");
      return;
    }
    const list = res.clients || [];
    setClients(list);
    setClientId((current) => current || (list[0]?.client_id || ""));
  }, []);

  useEffect(() => {
    loadClients();
    return () => stopPolling();
  }, [loadClients]);

  const saveClient = async () => {
    setError("");
    if (!newClient.client_name.trim()) {
      setError("Client name is required.");
      return;
    }
    setSavingClient(true);
    try {
      const res = await githubAPI.upsertClient(newClient);
      if (!res.success) {
        setError(res.error || "Failed to save client.");
        return;
      }
      setShowNewClient(false);
      setNewClient({ client_name: "", client_description: "" });
      await loadClients();
      setClientId(res.client_id || "");
    } finally {
      setSavingClient(false);
    }
  };

  const fetchRunResults = async (runId) => {
    const data = await githubAPI.getClientPitchesByRun(runId);
    if (!data.success) {
      setError(data.error || "Failed to load pitch results.");
      return;
    }
    setPitchRunId(data.pitch_run_id || runId);
    setPitches(data.pitches || []);
  };

  const startPolling = (runId) => {
    stopPolling();
    let attempts = 0;
    setStatus("Client pitch generator is running...");
    pollRef.current = setInterval(async () => {
      attempts += 1;
      const statusRes = await githubAPI.getClientPitchRunStatus(runId);
      if (!statusRes.success) {
        setStatus(statusRes.error || "Checking pitch status...");
        return;
      }
      if (statusRes.status === "complete") {
        stopPolling();
        setStatus(`Pitch run completed with ${statusRes.rows_written || 0} row(s).`);
        await fetchRunResults(runId);
        return;
      }
      if (statusRes.status === "failed") {
        stopPolling();
        setStatus("");
        setError("Pitch run failed. Check GitHub workflow logs.");
        return;
      }
      if (statusRes.status === "stale") {
        stopPolling();
        setStatus("This is no longer the latest pitch run.");
        return;
      }
      if (attempts % 6 === 0) {
        setStatus("Still running. Qwen generation can take several minutes on GitHub Actions CPU.");
      }
    }, 10000);
  };

  const generate = async () => {
    setError("");
    setPitches([]);
    if (!clientId) {
      setError("Select or add a client first.");
      return;
    }
    setIsTriggering(true);
    try {
      const res = await githubAPI.triggerClientPitches({
        client_id: clientId,
        mode,
        max_pitches: Number(maxPitches || 5),
      });
      if (!res.success) {
        setError(res.error || "Failed to trigger client pitch generator.");
        return;
      }
      setPitchRunId(res.pitch_run_id || "");
      setStatus("Client pitch generator workflow queued.");
      if (res.pitch_run_id) {
        startPolling(res.pitch_run_id);
      }
    } finally {
      setIsTriggering(false);
    }
  };

  const viewLatest = async () => {
    setError("");
    setIsViewing(true);
    try {
      const res = await githubAPI.getLatestClientPitches();
      if (!res.success) {
        setError(res.error || "Failed to load latest pitches.");
        return;
      }
      setPitchRunId(res.pitch_run_id || "");
      setPitches(res.pitches || []);
      setStatus(res.count ? `Loaded ${res.count} latest pitch idea(s).` : "No saved pitch ideas yet.");
    } finally {
      setIsViewing(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[#faf8f3] border-2 border-[#b8860b] mb-4">
          <Lightbulb className="w-8 h-8 text-[#b8860b]" />
        </div>
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Client Pitch Generator</h2>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-5 mb-6">
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Client</label>
          <div className="flex flex-col sm:flex-row gap-2">
            <select
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              className="flex-1 p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b] bg-white"
            >
              {clients.length === 0 && <option value="">No clients yet</option>}
              {clients.map((client) => (
                <option key={client.client_id} value={client.client_id}>
                  {client.client_name}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => setShowNewClient((v) => !v)}
              className="inline-flex items-center justify-center gap-2 px-4 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b]"
            >
              <Plus className="w-4 h-4" />
              Add Client
            </button>
          </div>
        </div>

        {showNewClient && (
          <div className="p-4 border border-gray-200 rounded-lg bg-[#faf8f3]">
            <label className="block text-sm font-semibold text-gray-700 mb-1">Client Name</label>
            <input
              value={newClient.client_name}
              onChange={(e) => setNewClient({ ...newClient, client_name: e.target.value })}
              className="w-full p-2 border border-gray-300 rounded mb-3"
            />
            <label className="block text-sm font-semibold text-gray-700 mb-1">Client Description</label>
            <textarea
              rows={5}
              value={newClient.client_description}
              onChange={(e) => setNewClient({ ...newClient, client_description: e.target.value })}
              className="w-full p-2 border border-gray-300 rounded"
              placeholder="Describe the client, positioning, audience, priorities, and anything to avoid."
            />
            <button
              type="button"
              onClick={saveClient}
              disabled={savingClient}
              className="mt-3 px-4 py-2 bg-[#b8860b] text-black font-semibold rounded disabled:opacity-60"
            >
              {savingClient ? "Saving..." : "Save Client"}
            </button>
          </div>
        )}

        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Evidence Mode</label>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b] bg-white"
          >
            <option value="auto">Auto: use trends, fallback to recent coverage</option>
            <option value="trend_signals">Trend Signals only</option>
            <option value="recent_coverage">Recent Coverage fallback only</option>
          </select>
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Max Pitch Ideas</label>
          <input
            type="number"
            min="1"
            max="8"
            value={maxPitches}
            onChange={(e) => setMaxPitches(e.target.value)}
            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b]"
          />
        </div>

        {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">{error}</div>}
        {status && <div className="text-sm text-blue-700 bg-blue-50 border border-blue-200 rounded p-3">{status}</div>}

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            type="button"
            onClick={generate}
            disabled={isTriggering}
            className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-[#b8860b] text-black font-bold rounded-lg hover:bg-[#8b6914] disabled:opacity-60"
          >
            <Lightbulb className="w-5 h-5" />
            {isTriggering ? "Triggering..." : "Generate Pitches"}
          </button>
          <button
            type="button"
            onClick={viewLatest}
            disabled={isViewing}
            className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] hover:bg-[#faf8f3] disabled:opacity-60"
          >
            <RefreshCw className="w-5 h-5" />
            {isViewing ? "Loading..." : "View Latest"}
          </button>
        </div>
      </div>

      {pitchRunId && <p className="text-sm text-gray-500 mb-3">Run ID: {pitchRunId}</p>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {pitches.map((p, idx) => (
          <div key={`${p.pitch_angle || "pitch"}-${idx}`} className="bg-white rounded-lg shadow-lg border border-gray-200 p-5">
            <div className="flex items-start justify-between gap-3 mb-2">
              <h3 className="text-lg font-semibold text-gray-900">{p.pitch_angle || "Pitch Idea"}</h3>
              {p.mode && (
                <span className="px-2 py-1 text-xs font-bold rounded bg-[#faf8f3] text-[#8b6914] border border-[#b8860b]">
                  {p.mode}
                </span>
              )}
            </div>
            <p className="text-sm text-gray-600 mb-3"><span className="font-semibold">Subject:</span> {p.subject_line}</p>
            <p className="text-sm text-gray-700 mb-2"><span className="font-semibold">Story:</span> {p.suggested_story}</p>
            <p className="text-sm text-gray-700 mb-2"><span className="font-semibold">Evidence:</span> {p.supporting_evidence}</p>
            {p.supporting_urls && (
              <p className="text-sm text-[#b8860b] break-all"><span className="font-semibold text-gray-700">URLs:</span> {p.supporting_urls}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
