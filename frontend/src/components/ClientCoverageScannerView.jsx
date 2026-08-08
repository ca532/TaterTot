import { useEffect, useRef, useState } from "react";
import { Plus, RefreshCw, Search, X } from "lucide-react";
import githubAPI from "../services/githubAPI";

export default function ClientCoverageScannerView() {
  const [clientName, setClientName] = useState("");
  const [author, setAuthor] = useState("");
  const [keywords, setKeywords] = useState("");
  const [publications, setPublications] = useState([
    { publication: "", publication_url: "" },
  ]);
  const [jobId, setJobId] = useState("");
  const [runId, setRunId] = useState("");
  const [job, setJob] = useState(null);
  const [summary, setSummary] = useState(null);
  const [results, setResults] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const pollRef = useRef(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => () => stopPolling(), []);

  const updatePublication = (index, field, value) => {
    setPublications((current) =>
      current.map((row, i) => (i === index ? { ...row, [field]: value } : row))
    );
  };

  const addPublication = () => {
    setPublications((current) => [...current, { publication: "", publication_url: "" }]);
  };

  const removePublication = (index) => {
    setPublications((current) => current.filter((_, i) => i !== index));
  };

  const loadReport = async (coverageRunId) => {
    const res = await githubAPI.getClientCoverageReport(coverageRunId);
    if (!res.success) {
      setError(res.error || "Failed to load coverage report.");
      return;
    }
    setSummary(res.summary || null);
    setResults(res.results || []);
  };

  const pollProgress = (nextJobId, coverageRunId) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      const res = await githubAPI.getClientCoverageScanProgress(nextJobId);
      if (!res.success) {
        setError(res.error || "Failed to check coverage scan progress.");
        stopPolling();
        setLoading(false);
        return;
      }

      setJob(res);

      if (res.status === "complete") {
        stopPolling();
        setLoading(false);
        setSummary(res.summary || null);
        setResults(res.results || []);
        if (coverageRunId) await loadReport(coverageRunId);
      }

      if (res.status === "failed") {
        stopPolling();
        setLoading(false);
        setError(res.error || res.message || "Coverage scan failed.");
      }
    }, 3000);
  };

  const scan = async () => {
    setError("");
    setSummary(null);
    setResults([]);
    setJob(null);

    const cleanPublications = publications.filter(
      (p) => p.publication.trim() && p.publication_url.trim()
    );

    if (!clientName.trim()) {
      setError("Client name is required.");
      return;
    }

    if (!cleanPublications.length) {
      setError("Add at least one publication and URL.");
      return;
    }

    setLoading(true);
    try {
      const res = await githubAPI.runClientCoverageScan({
        client_name: clientName,
        author,
        keywords,
        publications: cleanPublications,
      });

      if (!res.success) {
        setError(res.error || "Coverage scan failed to start.");
        setLoading(false);
        return;
      }

      setJobId(res.job_id || "");
      setRunId(res.coverage_run_id || "");
      setJob({
        status: "running",
        phase: "initializing",
        current: 0,
        total: cleanPublications.length,
        message: "Starting coverage scan",
      });
      pollProgress(res.job_id, res.coverage_run_id);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  const badgeClass = (status) => {
    if (status === "found") return "bg-green-50 text-green-700 border-green-200";
    if (status === "possible_match") return "bg-yellow-50 text-yellow-800 border-yellow-200";
    if (status === "error") return "bg-red-50 text-red-700 border-red-200";
    return "bg-gray-50 text-gray-700 border-gray-200";
  };

  const progressTotal = Number(job?.total || 0);
  const progressCurrent = Number(job?.current || 0);
  const progressPct = progressTotal > 0 ? Math.min(100, Math.round((progressCurrent / progressTotal) * 100)) : 0;

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[#faf8f3] border-2 border-[#b8860b] mb-4">
          <Search className="w-8 h-8 text-[#b8860b]" />
        </div>
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Client Coverage Scanner</h2>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-5 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <input
            className="p-3 border border-gray-300 rounded-lg"
            placeholder="Client name"
            value={clientName}
            onChange={(e) => setClientName(e.target.value)}
          />
          <input
            className="p-3 border border-gray-300 rounded-lg"
            placeholder="Author/contact, optional"
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
          />
          <input
            className="p-3 border border-gray-300 rounded-lg"
            placeholder="Keywords, optional"
            value={keywords}
            onChange={(e) => setKeywords(e.target.value)}
          />
        </div>

        <div className="space-y-3">
          {publications.map((pub, index) => (
            <div key={index} className="grid grid-cols-1 md:grid-cols-[1fr_1.5fr_auto] gap-3">
              <input
                className="p-3 border border-gray-300 rounded-lg"
                placeholder="Publication"
                value={pub.publication}
                onChange={(e) => updatePublication(index, "publication", e.target.value)}
              />
              <input
                className="p-3 border border-gray-300 rounded-lg"
                placeholder="Publication URL"
                value={pub.publication_url}
                onChange={(e) => updatePublication(index, "publication_url", e.target.value)}
              />
              <button
                type="button"
                onClick={() => removePublication(index)}
                className="inline-flex items-center justify-center px-3 py-2 border border-gray-300 rounded-lg text-gray-600 disabled:opacity-50"
                disabled={publications.length === 1 || loading}
                title="Remove publication"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            type="button"
            onClick={addPublication}
            disabled={loading}
            className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] disabled:opacity-60"
          >
            <Plus className="w-4 h-4" />
            Add Publication
          </button>
          <button
            type="button"
            onClick={scan}
            disabled={loading}
            className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-[#b8860b] text-black font-bold rounded-lg disabled:opacity-60"
          >
            {loading ? <RefreshCw className="w-5 h-5 animate-spin" /> : <Search className="w-5 h-5" />}
            {loading ? "Scanning..." : "Scan Coverage"}
          </button>
        </div>

        {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">{error}</div>}

        {job && (
          <div className="border border-blue-100 bg-blue-50 rounded-lg p-4">
            <div className="flex items-center justify-between gap-4 mb-2">
              <p className="text-sm font-semibold text-blue-800">{job.message || "Coverage scan running"}</p>
              <p className="text-xs text-blue-700 whitespace-nowrap">{progressCurrent}/{progressTotal}</p>
            </div>
            <div className="h-2 bg-white rounded-full overflow-hidden">
              <div className="h-full bg-[#b8860b]" style={{ width: `${progressPct}%` }} />
            </div>
          </div>
        )}
      </div>

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Found</p>
            <p className="text-2xl font-bold text-green-700">{summary.found || 0}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Possible</p>
            <p className="text-2xl font-bold text-yellow-700">{summary.possible_match || 0}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Not Found</p>
            <p className="text-2xl font-bold text-gray-700">{summary.not_found || 0}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Errors</p>
            <p className="text-2xl font-bold text-red-700">{summary.error || 0}</p>
          </div>
        </div>
      )}

      {(runId || jobId) && (
        <p className="text-sm text-gray-500 mb-3">
          {runId ? `Run ID: ${runId}` : ""}{jobId ? ` | Job ID: ${jobId}` : ""}
        </p>
      )}

      <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
        <div className="p-4 border-b border-gray-200">
          <h3 className="font-semibold text-gray-900">Coverage Report</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="text-left p-3">Status</th>
                <th className="text-left p-3">Publication</th>
                <th className="text-left p-3">Match</th>
                <th className="text-left p-3">Author</th>
                <th className="text-left p-3">Score</th>
                <th className="text-left p-3">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row, index) => (
                <tr key={`${row.matched_url || row.publication}-${index}`} className="border-t border-gray-100">
                  <td className="p-3">
                    <span className={`inline-flex px-2 py-1 text-xs font-semibold rounded border ${badgeClass(row.status)}`}>
                      {row.status}
                    </span>
                  </td>
                  <td className="p-3">{row.publication}</td>
                  <td className="p-3">
                    {row.matched_url ? (
                      <a href={row.matched_url} target="_blank" rel="noreferrer" className="text-[#b8860b]">
                        {row.matched_title || row.matched_url}
                      </a>
                    ) : "-"}
                  </td>
                  <td className="p-3">{row.matched_author || row.author || "-"}</td>
                  <td className="p-3">{row.confidence_score}</td>
                  <td className="p-3 text-gray-600">{row.evidence}</td>
                </tr>
              ))}
              {results.length === 0 && (
                <tr>
                  <td className="p-4 text-gray-500" colSpan={6}>No scan results yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
