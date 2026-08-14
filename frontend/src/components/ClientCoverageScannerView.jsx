import { useEffect, useMemo, useRef, useState } from "react";
import { Download, RefreshCw, Search } from "lucide-react";
import githubAPI from "../services/githubAPI";

export default function ClientCoverageScannerView() {
  const [form, setForm] = useState({
    report_title: "",
    mention_terms: "",
    search_queries: "",
    date_from: "",
    date_to: "",
    backlink_domains: "",
  });
  const [jobId, setJobId] = useState("");
  const [runId, setRunId] = useState("");
  const [job, setJob] = useState(null);
  const [summary, setSummary] = useState(null);
  const [results, setResults] = useState([]);
  const [reviewResults, setReviewResults] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const pollRef = useRef(null);

  const searchQueries = useMemo(
    () => form.search_queries.split(/\r?\n/).map((q) => q.trim()).filter(Boolean),
    [form.search_queries]
  );
  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => () => stopPolling(), []);

  const updateForm = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const pollProgress = (nextJobId) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      const res = await githubAPI.getClientCoverageSearchReportProgress(nextJobId);
      if (!res.success) {
        setError(res.error || "Failed to check report progress.");
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
        setReviewResults(res.review_results || []);
      }

      if (res.status === "failed") {
        stopPolling();
        setLoading(false);
        setError(res.error || res.message || "Coverage report failed.");
      }
    }, 3000);
  };

  const runReport = async () => {
    setError("");
    setSummary(null);
    setResults([]);
    setReviewResults([]);
    setJob(null);

    if (!form.report_title.trim()) {
      setError("Report title is required.");
      return;
    }
    if (!form.mention_terms.trim()) {
      setError("Add at least one mention term.");
      return;
    }
    if (!searchQueries.length) {
      setError("Add at least one search query.");
      return;
    }

    setLoading(true);
    try {
      const res = await githubAPI.runClientCoverageSearchReport(form);

      if (!res.success) {
        setError(res.error || "Coverage report failed to start.");
        setLoading(false);
        return;
      }

      setJobId(res.job_id || "");
      setRunId(res.coverage_run_id || "");
      setJob({
        status: "running",
        phase: "initializing",
        current: 0,
        total: res.available_searches || 0,
        message: "Starting coverage report",
      });
      pollProgress(res.job_id);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  const downloadPdf = async () => {
    if (!jobId) return;
    setError("");
    setDownloading(true);
    try {
      const res = await githubAPI.downloadClientCoverageSearchReport(jobId);
      if (!res.success) {
        setError(res.error || "Failed to download PDF.");
        return;
      }

      const url = URL.createObjectURL(res.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = res.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
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
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Client Coverage Report</h2>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-5 mb-6">
        <input
          className="w-full p-3 border border-gray-300 rounded-lg"
          placeholder="Report title"
          value={form.report_title}
          onChange={(e) => updateForm("report_title", e.target.value)}
        />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <textarea
            className="w-full p-3 border border-gray-300 rounded-lg min-h-28"
            placeholder="Mention terms, comma-separated"
            value={form.mention_terms}
            onChange={(e) => updateForm("mention_terms", e.target.value)}
          />
          <textarea
            className="w-full p-3 border border-gray-300 rounded-lg min-h-28"
            placeholder="Backlink domains, optional"
            value={form.backlink_domains}
            onChange={(e) => updateForm("backlink_domains", e.target.value)}
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Search Query</label>
          <textarea
            className="w-full p-3 border border-gray-300 rounded-lg min-h-36"
            placeholder={"One query per line\nTobias Kormind Cristiano Ronaldo engagement ring\n77 Diamonds Georgina Rodriguez engagement ring"}
            value={form.search_queries}
            onChange={(e) => updateForm("search_queries", e.target.value)}
          />
          <p className="text-xs text-gray-500 mt-2">Using one focused query gives us the best chance of searching all available result pages.</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="space-y-2">
            <span className="block text-sm font-semibold text-gray-700">From</span>
            <input
              type="date"
              className="w-full p-3 border border-gray-300 rounded-lg"
              value={form.date_from}
              onChange={(e) => updateForm("date_from", e.target.value)}
            />
          </label>
          <label className="space-y-2">
            <span className="block text-sm font-semibold text-gray-700">To</span>
            <input
              type="date"
              className="w-full p-3 border border-gray-300 rounded-lg"
              value={form.date_to}
              onChange={(e) => updateForm("date_to", e.target.value)}
            />
          </label>
        </div>

        {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">{error}</div>}

        <div className="flex flex-col sm:flex-row gap-3">
          <button
            type="button"
            onClick={runReport}
            disabled={loading}
            className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-[#b8860b] text-black font-bold rounded-lg disabled:opacity-60"
          >
            {loading ? <RefreshCw className="w-5 h-5 animate-spin" /> : <Search className="w-5 h-5" />}
            {loading ? "Generating..." : "Generate Report"}
          </button>
          {job?.status === "complete" && (
            <button
              type="button"
              onClick={downloadPdf}
              disabled={downloading}
              className="inline-flex items-center justify-center gap-2 px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] disabled:opacity-60"
            >
              <Download className="w-5 h-5" />
              {downloading ? "Downloading..." : "Download PDF"}
            </button>
          )}
        </div>

        {job && (
          <div className="border border-blue-100 bg-blue-50 rounded-lg p-4">
            <div className="flex items-center justify-between gap-4 mb-2">
              <p className="text-sm font-semibold text-blue-800">{job.message || "Coverage report running"}</p>
              <p className="text-xs text-blue-700 whitespace-nowrap">{progressCurrent}/{progressTotal}</p>
            </div>
            <div className="h-2 bg-white rounded-full overflow-hidden">
              <div className="h-full bg-[#b8860b]" style={{ width: `${progressPct}%` }} />
            </div>
          </div>
        )}
      </div>

      {summary && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4 mb-6">
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Coverage</p>
            <p className="text-2xl font-bold text-green-700">{summary.total_coverage || results.length || 0}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Google Pages Searched</p>
            <p className="text-2xl font-bold text-gray-800">{summary.searches_used || 0}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Results Checked</p>
            <p className="text-2xl font-bold text-gray-800">{summary.searched_results || 0}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Searches Remaining</p>
            <p className="text-2xl font-bold text-gray-800">{summary.searches_remaining || 0}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Needs Review</p>
            <p className="text-2xl font-bold text-amber-700">{summary.needs_review || 0}</p>
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
          <h3 className="font-semibold text-gray-900">Coverage Results</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="text-left p-3">Publication</th>
                <th className="text-left p-3">Article</th>
                <th className="text-left p-3">Type</th>
                <th className="text-left p-3">Visits</th>
                <th className="text-left p-3">Matched Terms</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row, index) => (
                <tr key={`${row.article_url || row.publication}-${index}`} className="border-t border-gray-100">
                  <td className="p-3">{row.publication}</td>
                  <td className="p-3">
                    {row.article_url ? (
                      <a href={row.article_url} target="_blank" rel="noreferrer" className="text-[#b8860b]">
                        {row.article_title || row.article_url}
                      </a>
                    ) : "-"}
                  </td>
                  <td className="p-3">{row.coverage_type}</td>
                  <td className="p-3">{row.monthly_visits_display || "N/A"}</td>
                  <td className="p-3 text-gray-600">{row.matched_terms}</td>
                </tr>
              ))}
              {results.length === 0 && (
                <tr>
                  <td className="p-4 text-gray-500" colSpan={5}>
                    {summary?.searched_results === 0
                      ? "Google returned no organic results for this query and date range."
                      : "No confirmed client mentions were found in the checked results."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {reviewResults.length > 0 && (
        <div className="bg-white border border-amber-200 rounded-lg shadow-sm overflow-hidden mt-6">
          <div className="p-4 border-b border-amber-200 bg-amber-50">
            <h3 className="font-semibold text-gray-900">Needs Review</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="text-left p-3">Publication</th>
                  <th className="text-left p-3">Article</th>
                  <th className="text-left p-3">Reason</th>
                  <th className="text-left p-3">Extraction</th>
                </tr>
              </thead>
              <tbody>
                {reviewResults.map((row, index) => (
                  <tr key={`${row.article_url || row.publication}-review-${index}`} className="border-t border-gray-100">
                    <td className="p-3">{row.publication}</td>
                    <td className="p-3">
                      {row.article_url ? (
                        <a href={row.article_url} target="_blank" rel="noreferrer" className="text-[#b8860b]">
                          {row.article_title || row.article_url}
                        </a>
                      ) : "-"}
                    </td>
                    <td className="p-3 text-gray-600">{row.verification_reason}</td>
                    <td className="p-3 text-gray-600">{row.extraction_method}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
