import { useEffect, useMemo, useRef, useState } from "react";
import { Download, Pencil, RefreshCw, Search, X } from "lucide-react";
import githubAPI from "../services/githubAPI";

const RUNNING_MESSAGE = "A coverage run is already in progress. Wait for it to finish before starting another run.";
const ACTION_PROGRESS_MESSAGES = {
  discover: "Discovering and verifying coverage",
  country: "Checking publication countries",
  finalize: "Generating coverage report",
};

const formatStage = (status = "") => {
  const label = status.replaceAll("_", " ");
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : "-";
};

export default function ClientCoverageScannerView() {
  const [form, setForm] = useState({
    report_title: "",
    mention_terms: "",
    search_queries: "",
    date_from: "",
    date_to: "",
    backlink_domains: "",
  });
  const [jobId, setJobId] = useState(() => window.localStorage.getItem("coverage_job_id") || "");
  const [snapshot, setSnapshot] = useState(null);
  const [loadingJob, setLoadingJob] = useState(Boolean(jobId));
  const [runningAction, setRunningAction] = useState("");
  const [dispatching, setDispatching] = useState(false);
  const [error, setError] = useState("");
  const [selectedReview, setSelectedReview] = useState([]);
  const [selectedCountries, setSelectedCountries] = useState([]);
  const [countryEdit, setCountryEdit] = useState(null);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const pollRef = useRef(null);

  const queries = useMemo(
    () => form.search_queries.split(/\r?\n|\|\|/).map((item) => item.trim()).filter(Boolean),
    [form.search_queries]
  );
  const job = snapshot?.job || null;
  const results = snapshot?.results || [];
  const reviewResults = snapshot?.review_results || [];
  const countryReviewResults = snapshot?.country_review_results || [];
  const summary = snapshot?.summary || {};
  const suggestedQueries = useMemo(() => {
    try {
      const parsed = JSON.parse(job?.suggested_queries || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }, [job?.suggested_queries]);

  const stopPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };

  const updateForm = (field, value) => setForm((current) => ({ ...current, [field]: value }));

  const refreshJob = async (id = jobId) => {
    if (!id) return false;
    const response = await githubAPI.getCoverageJob(id);
    if (!response.success) {
      setError(response.error || "Unable to load the coverage job.");
      return false;
    }
    setSnapshot(response);
    return true;
  };

  useEffect(() => {
    if (!jobId) return;
    let active = true;
    setLoadingJob(true);
    githubAPI.getCoverageJob(jobId).then((response) => {
      if (!active) return;
      if (response.success) {
        setSnapshot(response);
        if (response.job?.active_action) {
          setRunningAction(response.job.active_action);
        }
      }
      else setError(response.error || "Unable to load the coverage job.");
    }).finally(() => {
      if (active) setLoadingJob(false);
    });
    return () => {
      active = false;
    };
  }, [jobId]);

  const startNewJob = () => {
    stopPolling();
    window.localStorage.removeItem("coverage_job_id");
    setJobId("");
    setSnapshot(null);
    setLoadingJob(false);
    setRunningAction("");
    setDispatching(false);
    setSelectedReview([]);
    setSelectedCountries([]);
    setCountryEdit(null);
    setError("");
  };

  useEffect(() => {
    if (!jobId || !runningAction) return undefined;
    let active = true;
    const checkProgress = async () => {
      const response = await githubAPI.getClientCoverageSearchReportProgress(jobId, runningAction);
      if (!active) return;
      if (!response.success) {
        setRunningAction("");
        setError(response.error || "Unable to check coverage progress.");
        return;
      }
      if (response.status === "complete" || response.status === "failed") {
        stopPolling();
        setRunningAction("");
        if (response.job) {
          setSnapshot(response);
        } else {
          const refreshed = await githubAPI.getCoverageJob(jobId);
          if (active && refreshed.success) setSnapshot(refreshed);
        }
        if (response.status === "failed") {
          setError(response.error || response.message || "Coverage action failed.");
        } else {
          setError("");
        }
      }
    };
    checkProgress();
    pollRef.current = setInterval(checkProgress, 60 * 1000);
    return () => {
      active = false;
      stopPolling();
    };
  }, [jobId, runningAction]);

  const handleActionFailure = (response) => {
    setRunningAction("");
    setDispatching(false);
    if (response.status === 409 || response.error === RUNNING_MESSAGE) {
      setError(RUNNING_MESSAGE);
    } else {
      setError(response.error || "Coverage action failed to start.");
    }
  };

  const createJob = async () => {
    setError("");
    if (!form.report_title.trim()) return setError("Report title is required.");
    if (!form.mention_terms.trim()) return setError("Add at least one mention term.");
    if (!queries.length) return setError("Add at least one search query.");

    setDispatching(true);
    const response = await githubAPI.runClientCoverageSearchReport(form);
    if (!response.success) return handleActionFailure(response);
    setDispatching(false);
    setRunningAction("discover");
    setJobId(response.job_id);
    window.localStorage.setItem("coverage_job_id", response.job_id);
  };

  const runAction = async (action, payload = {}) => {
    setError("");
    setDispatching(true);
    const response = await githubAPI.runCoverageJobAction(jobId, action, payload);
    if (!response.success) return handleActionFailure(response);
    setDispatching(false);
    setRunningAction(action);
  };

  const runAnotherDiscovery = () => {
    if (!queries.length) return setError("Add at least one search query.");
    runAction("discover", { search_queries: queries.join("\n") });
  };

  const acceptSelected = async () => {
    if (!reviewResults.length || !selectedReview.length) return;
    setSaving(true);
    setError("");
    const accepted = new Set(selectedReview);
    const response = await githubAPI.reviewCoverageCandidates(
      jobId,
      reviewResults.map((row) => ({
        url_key: row.url_key,
        decision: accepted.has(row.url_key) ? "approved" : "rejected",
      }))
    );
    setSaving(false);
    if (!response.success) return setError(response.error || "Unable to save review decisions.");
    setSnapshot(response);
    setSelectedReview([]);
    await runAction("country");
  };

  const savePublication = async (row, publication) => {
    const value = publication.trim();
    if (!value || value === row.publication) return;
    const response = await githubAPI.updateCoveragePublication(jobId, {
      url_key: row.url_key,
      publication: value,
    });
    if (!response.success) setError(response.error || "Unable to update publication.");
    else setSnapshot(response);
  };

  const confirmSelectedCountries = async () => {
    if (!selectedCountries.length) return;
    setSaving(true);
    const response = await githubAPI.confirmCoverageCountries(jobId, selectedCountries);
    setSaving(false);
    if (!response.success) return setError(response.error || "Unable to confirm countries.");
    setSnapshot(response);
    setSelectedCountries([]);
  };

  const saveCountry = async (notApplicable = false) => {
    if (!countryEdit) return;
    if (!notApplicable && !countryEdit.country.trim()) return setError("Country is required.");
    setSaving(true);
    const response = await githubAPI.setClientCoverageCountryOverride({
      job_id: jobId,
      coverage_run_id: `coverage-search-${jobId}`,
      lookup_key: countryEdit.country_lookup_key,
      publication: countryEdit.publication,
      article_url: countryEdit.article_url,
      country: countryEdit.country.trim(),
      country_code: countryEdit.country_code.trim().toUpperCase(),
      not_applicable: notApplicable,
    });
    setSaving(false);
    if (!response.success) return setError(response.error || "Unable to save country.");
    setCountryEdit(null);
    await refreshJob();
  };

  const downloadPdf = async () => {
    setDownloading(true);
    const response = await githubAPI.downloadClientCoverageSearchReport(jobId);
    setDownloading(false);
    if (!response.success) return setError(response.error || "Unable to download the PDF.");
    const url = URL.createObjectURL(response.blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = response.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const running = Boolean(dispatching || runningAction || job?.active_action);
  const activeAction = runningAction || job?.active_action || "";
  const progressMessage = dispatching && !activeAction
    ? "Starting coverage workflow"
    : ACTION_PROGRESS_MESSAGES[activeAction] || "Processing coverage";
  const reportComplete = job?.status === "complete";
  const coverageCount = Number(summary.total_coverage || 0);
  const searchesRemaining = job?.searches_remaining;
  const stageLabel = formatStage(job?.status);
  const canCountry = job && !running && reviewResults.length === 0 && results.length > 0 && !["country_review", "complete"].includes(job.status);
  const canFinalize = job && !running && results.length > 0 && reviewResults.length === 0 && countryReviewResults.length === 0;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 space-y-6">
      <header className="flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-lg border border-[#b8860b] bg-[#faf8f3]">
          <Search className="h-5 w-5 text-[#8a6508]" />
        </div>
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Client Coverage Report</h2>
          {jobId && <p className="text-xs text-gray-500">Job {jobId}</p>}
        </div>
        {jobId && <button type="button" onClick={startNewJob} disabled={running} className="ml-auto rounded border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 disabled:opacity-50">New job</button>}
      </header>

      {!jobId && (
        <section className="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-4">
          <input className="w-full p-3 border border-gray-300 rounded-lg" placeholder="Report title" value={form.report_title} onChange={(event) => updateForm("report_title", event.target.value)} />
          <div className="grid gap-4 md:grid-cols-2">
            <textarea className="min-h-24 w-full p-3 border border-gray-300 rounded-lg" placeholder="Mention terms, comma-separated" value={form.mention_terms} onChange={(event) => updateForm("mention_terms", event.target.value)} />
            <textarea className="min-h-24 w-full p-3 border border-gray-300 rounded-lg" placeholder="Backlink domains, optional" value={form.backlink_domains} onChange={(event) => updateForm("backlink_domains", event.target.value)} />
          </div>
          <textarea className="min-h-32 w-full p-3 border border-gray-300 rounded-lg" placeholder="One search query per line" value={form.search_queries} onChange={(event) => updateForm("search_queries", event.target.value)} />
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm font-semibold text-gray-700">From<input type="date" className="mt-2 w-full p-3 border border-gray-300 rounded-lg" value={form.date_from} onChange={(event) => updateForm("date_from", event.target.value)} /></label>
            <label className="text-sm font-semibold text-gray-700">To<input type="date" className="mt-2 w-full p-3 border border-gray-300 rounded-lg" value={form.date_to} onChange={(event) => updateForm("date_to", event.target.value)} /></label>
          </div>
          <button type="button" onClick={createJob} disabled={running} className="inline-flex items-center gap-2 px-5 py-3 rounded-lg bg-[#b8860b] font-semibold text-black disabled:opacity-50">
            {running ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            Run discovery
          </button>
        </section>
      )}

      {error && <div role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>}
      {jobId && loadingJob && !snapshot && (
        <section role="status" className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 p-4 font-semibold text-blue-800">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading coverage job
        </section>
      )}
      {(running || reportComplete) && (
        <section
          role="status"
          className="rounded-lg border border-blue-200 bg-blue-50 p-4"
        >
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2 font-semibold text-blue-800">
              {running && <RefreshCw className="h-4 w-4 animate-spin" />}
              <span>
                {running
                  ? progressMessage
                  : "Coverage report complete"}
              </span>
            </div>

            {reportComplete && !running && (
              <span className="text-sm text-blue-600">
                {coverageCount}/{coverageCount}
              </span>
            )}
          </div>

          <div className="mt-3 h-2 overflow-hidden rounded bg-gray-200">
            <div
              className={`h-full bg-[#b88a25] transition-all duration-500 ${
                reportComplete && !running ? "w-full" : "w-0"
              }`}
            />
          </div>
        </section>
      )}

      {jobId && snapshot && (
        <>
          <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {[
              ["Coverage", summary.total_coverage],
              ["Under review", summary.needs_review],
              ["Countries to review", summary.countries_need_review],
              [
                "Searches remaining",
                searchesRemaining === "" || searchesRemaining == null
                  ? "-"
                  : searchesRemaining,
              ],
              ["Stage", stageLabel],
            ].map(([label, value]) => (
              <div key={label} className="border-b border-gray-200 bg-white p-4">
                <p className="text-xs text-gray-500">{label}</p><p className="mt-1 text-lg font-semibold text-gray-900">{value === "" || value == null ? 0 : value}</p>
              </div>
            ))}
          </section>

          <section className="flex flex-wrap items-end gap-3 border-y border-gray-200 bg-white py-4">
            <label className="min-w-72 flex-1 text-sm font-semibold text-gray-700">Discovery queries
              <textarea className="mt-2 min-h-20 w-full rounded-lg border border-gray-300 p-3 font-normal" value={form.search_queries} onChange={(event) => updateForm("search_queries", event.target.value)} />
              {suggestedQueries.length > 0 && <span className="mt-2 flex flex-wrap gap-2">{suggestedQueries.map((query) => <button key={query} type="button" onClick={() => updateForm("search_queries", query)} className="max-w-full truncate rounded border border-gray-300 px-2 py-1 text-xs font-normal text-gray-700" title={query}>{query}</button>)}</span>}
            </label>
            <button type="button" onClick={runAnotherDiscovery} disabled={running} className="px-4 py-2 rounded-lg border border-[#b8860b] text-[#8a6508] font-semibold disabled:opacity-50">Run discovery</button>
            {canCountry && <button type="button" onClick={() => runAction("country")} className="px-4 py-2 rounded-lg bg-[#b8860b] text-black font-semibold">Run country check</button>}
            <button type="button" onClick={() => runAction("finalize")} disabled={!canFinalize} className="px-4 py-2 rounded-lg bg-[#b8860b] text-black font-semibold disabled:opacity-40">Finalize report</button>
            {job.status === "complete" && <button type="button" onClick={downloadPdf} disabled={downloading} className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-[#b8860b] text-[#8a6508] font-semibold"><Download className="h-4 w-4" />{downloading ? "Downloading" : "Download PDF"}</button>}
          </section>

          {reviewResults.length > 0 && (
            <ReviewTable rows={reviewResults} selected={selectedReview} setSelected={setSelectedReview} onAccept={acceptSelected} onPublication={savePublication} saving={saving} />
          )}

          {countryReviewResults.length > 0 && (
            <CountryTable rows={countryReviewResults} selected={selectedCountries} setSelected={setSelectedCountries} onConfirm={confirmSelectedCountries} onEdit={setCountryEdit} saving={saving} />
          )}

          {countryEdit && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
              <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
                <div className="flex items-center justify-between"><h3 className="font-semibold">Edit country</h3><button title="Close" onClick={() => setCountryEdit(null)}><X className="h-5 w-5" /></button></div>
                <div className="mt-4 grid grid-cols-[1fr_90px] gap-3">
                  <input className="rounded border border-gray-300 p-3" placeholder="Country" value={countryEdit.country} onChange={(event) => setCountryEdit((current) => ({ ...current, country: event.target.value }))} />
                  <input className="rounded border border-gray-300 p-3 uppercase" placeholder="Code" maxLength={2} value={countryEdit.country_code} onChange={(event) => setCountryEdit((current) => ({ ...current, country_code: event.target.value }))} />
                </div>
                <div className="mt-4 flex gap-2"><button onClick={() => saveCountry(false)} disabled={saving} className="px-4 py-2 rounded bg-[#b8860b] font-semibold">Save</button><button onClick={() => saveCountry(true)} disabled={saving} className="px-4 py-2 rounded border border-gray-300">Not applicable</button></div>
              </div>
            </div>
          )}

          {job.status !== "country_review" && (
            <CoverageTable rows={results} />
          )}
        </>
      )}
    </div>
  );
}

function ReviewTable({ rows, selected, setSelected, onAccept, onPublication, saving }) {
  const toggleAll = () => setSelected(selected.length === rows.length ? [] : rows.map((row) => row.url_key));
  return (
    <section className="overflow-hidden rounded-lg border border-amber-200 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50 p-4"><h3 className="mr-auto font-semibold">Article review</h3><button disabled={!selected.length || saving} onClick={onAccept} className="px-3 py-2 rounded bg-green-700 text-white disabled:opacity-40">Accept selected</button></div>
      <div className="overflow-x-auto"><table className="min-w-full text-sm"><thead className="bg-gray-50"><tr><th className="p-3"><input type="checkbox" aria-label="Select all review articles" checked={selected.length === rows.length} onChange={toggleAll} /></th><th className="p-3 text-left">Publication</th><th className="p-3 text-left">Article</th><th className="p-3 text-left">Date</th><th className="p-3 text-left">Reason</th><th className="p-3 text-left">Extraction</th></tr></thead><tbody>{rows.map((row) => <tr key={row.url_key} className="border-t border-gray-100"><td className="p-3"><input type="checkbox" aria-label={`Select ${row.article_title}`} checked={selected.includes(row.url_key)} onChange={() => setSelected((current) => current.includes(row.url_key) ? current.filter((key) => key !== row.url_key) : [...current, row.url_key])} /></td><td className="p-3"><input className="w-40 rounded border border-transparent p-2 hover:border-gray-300 focus:border-gray-400" defaultValue={row.publication} onBlur={(event) => onPublication(row, event.target.value)} /></td><td className="p-3"><a className="text-[#8a6508] underline" href={row.article_url} target="_blank" rel="noreferrer">{row.article_title || row.article_url}</a></td><td className="p-3">{row.published_date || "Unknown"}</td><td className="p-3 text-gray-600">{row.verification_reason}</td><td className="p-3 text-gray-600">{row.extraction_method}</td></tr>)}</tbody></table></div>
    </section>
  );
}

function CountryTable({ rows, selected, setSelected, onConfirm, onEdit, saving }) {
  const eligible = [...new Set(rows.filter((row) => row.country && row.country_reviewed !== "TRUE").map((row) => row.country_lookup_key))];
  return (
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white">
      <div className="flex items-center border-b border-gray-200 p-4"><h3 className="mr-auto font-semibold">Country review</h3><button disabled={!selected.length || saving} onClick={onConfirm} className="px-3 py-2 rounded bg-green-700 text-white disabled:opacity-40">Confirm selected</button></div>
      <div className="overflow-x-auto"><table className="min-w-full text-sm"><thead className="bg-gray-50"><tr><th className="p-3"><input type="checkbox" aria-label="Select all resolved countries" checked={eligible.length > 0 && selected.length === eligible.length} onChange={() => setSelected(selected.length === eligible.length ? [] : eligible)} /></th><th className="p-3 text-left">Publication</th><th className="p-3 text-left">Country</th><th className="p-3 text-left">Source</th><th className="p-3 text-left">Confidence</th><th className="p-3" /></tr></thead><tbody>{rows.map((row) => <tr key={row.url_key} className="border-t border-gray-100"><td className="p-3"><input type="checkbox" disabled={!row.country || row.country_reviewed === "TRUE"} checked={selected.includes(row.country_lookup_key)} onChange={() => setSelected((current) => current.includes(row.country_lookup_key) ? current.filter((key) => key !== row.country_lookup_key) : [...current, row.country_lookup_key])} /></td><td className="p-3"><a href={row.article_url} target="_blank" rel="noreferrer" className="text-[#8a6508] underline">{row.publication}</a></td><td className="p-3">{row.country || "Unresolved"}</td><td className="p-3">{row.country_source || "-"}</td><td className="p-3">{row.country_confidence || "-"}</td><td className="p-3"><button title="Edit country" onClick={() => onEdit({ ...row, country: row.country || "", country_code: row.country_code || "" })} className="p-2 text-[#8a6508]"><Pencil className="h-4 w-4" /></button></td></tr>)}</tbody></table></div>
    </section>
  );
}

function CoverageTable({ rows }) {
  return (
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white"><div className="border-b border-gray-200 p-4"><h3 className="font-semibold">Approved coverage</h3></div><div className="overflow-x-auto"><table className="min-w-full text-sm"><thead className="bg-gray-50"><tr><th className="p-3 text-left">Publication</th><th className="p-3 text-left">Country</th><th className="p-3 text-left">Article</th><th className="p-3 text-left">Date</th><th className="p-3 text-left">Visits</th></tr></thead><tbody>{rows.map((row) => <tr key={row.url_key} className="border-t border-gray-100"><td className="p-3">{row.publication}</td><td className="p-3">{row.country || "-"}</td><td className="p-3"><a className="text-[#8a6508] underline" href={row.article_url} target="_blank" rel="noreferrer">{row.article_title || row.article_url}</a>{row.manually_approved === "TRUE" && <span className="ml-2 text-xs text-gray-500">Manual</span>}</td><td className="p-3">{row.published_date || "-"}</td><td className="p-3">{row.monthly_visits_display || "-"}</td></tr>)}</tbody></table></div></section>
  );
}
