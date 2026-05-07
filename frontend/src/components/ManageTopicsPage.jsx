import { useEffect, useMemo, useState } from "react";
import githubAPI from "../services/githubAPI";

export default function ManageTopicsPage({ onBack, onSaved }) {
  const [listName, setListName] = useState("");
  const [sourceInput, setSourceInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState(null);
  const [metaJobId, setMetaJobId] = useState("");
  const [progress, setProgress] = useState({ phase: "idle", current: 0, total: 0, message: "" });

  const percent = useMemo(() => {
    if (!progress.total) return 0;
    return Math.min(100, Math.round((progress.current / progress.total) * 100));
  }, [progress]);

  const parseSourceRows = (raw) =>
    raw
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .map((line) => {
        const firstComma = line.indexOf(",");
        if (firstComma === -1) return { base_url: line, rss_url: "" };
        return {
          base_url: line.slice(0, firstComma).trim(),
          rss_url: line.slice(firstComma + 1).trim(),
        };
      });

  const handleSave = async () => {
    const ln = (listName || "").trim();
    if (!ln) return alert("Enter topic/list name.");
    const sources = parseSourceRows(sourceInput);
    if (!sources.length) return alert("Add at least one source row.");
    setSaving(true);
    try {
      const res = await githubAPI.createSourceList({ list_name: ln, sources });
      if (!res.success) return alert(`Save failed: ${res.error || "unknown error"}`);
      alert(`Added ${res.inserted} rows to ${res.list_name}`);
      onSaved?.(ln);
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    const ln = (listName || "").trim();
    if (!ln) return alert("Enter/select list name.");
    setReport(null);
    setRunning(true);
    try {
      const res = await githubAPI.runSourceMetadata(ln);
      if (!res.success) return alert(`Metadata run failed: ${res.error || "unknown error"}`);
      setMetaJobId(res.job_id);
      setProgress({ phase: "initializing", current: 0, total: 0, message: "Starting metadata check..." });
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => {
    if (!metaJobId) return;
    const id = setInterval(async () => {
      const p = await githubAPI.getSourceMetadataProgress(metaJobId);
      if (!p.success) return;
      setProgress({
        phase: p.phase || "running",
        current: Number(p.current || 0),
        total: Number(p.total || 0),
        message: p.message || "",
      });
      if (p.status === "complete" || p.status === "failed") {
        clearInterval(id);
        if (p.status === "complete") {
          const rep = await githubAPI.getSourceMetadataReport((listName || "").trim());
          if (rep.success) setReport(rep);
        }
      }
    }, 2500);
    return () => clearInterval(id);
  }, [metaJobId, listName]);

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6">
      <div className="w-full mx-auto mb-6 p-4 border-2 border-[#b8860b] rounded-lg bg-[#faf8f3] text-left">
        <h3 className="text-base font-bold mb-3">Add New Category/Topic</h3>
        <label className="block text-sm font-semibold mb-1">List Name</label>
        <input
          value={listName}
          onChange={(e) => setListName(e.target.value)}
          className="w-full p-2 border border-gray-300 rounded mb-2"
          placeholder="e.g. finance_may"
        />
        <label className="block text-sm font-semibold mb-1">Sources (one per line: base_url, rss_url)</label>
        <textarea
          value={sourceInput}
          onChange={(e) => setSourceInput(e.target.value)}
          rows={8}
          className="w-full p-2 border border-gray-300 rounded mb-2"
          placeholder={"https://site1.com, https://site1.com/feed\nhttps://site2.com,"}
        />
        <div className="flex gap-2">
          <button type="button" onClick={handleSave} disabled={saving} className="px-4 py-2 bg-[#b8860b] text-black font-semibold rounded">
            {saving ? "Saving..." : "Save Source List"}
          </button>
          <button type="button" onClick={handleRun} disabled={running} className="px-4 py-2 bg-[#b8860b] text-black font-semibold rounded">
            {running ? "Starting..." : "Run Metadata Check"}
          </button>
          <button type="button" onClick={onBack} className="px-4 py-2 bg-white border border-gray-300 text-black font-semibold rounded">
            Back
          </button>
        </div>

        {(metaJobId || progress.phase !== "idle") && (
          <div className="mt-4">
            <div className="text-sm mb-1">{progress.message} ({progress.current}/{progress.total || 0})</div>
            <div className="w-full h-3 bg-gray-200 rounded">
              <div className="h-3 bg-[#b8860b] rounded transition-all" style={{ width: `${percent}%` }} />
            </div>
            <div className="text-xs mt-1 text-gray-600">Phase: {progress.phase} • {percent}%</div>
          </div>
        )}

        {report?.success && report.summary && (
          <div className="mt-4">
            <div className="p-3 bg-white rounded border border-gray-200 mb-3">
              <h4 className="font-semibold mb-2">Validation Report</h4>
              <p className="text-sm mb-2">{report.summary.summary_text}</p>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-sm">
                <div>Total: <span className="font-semibold">{report.summary.total}</span></div>
                <div>Valid sitemap: <span className="font-semibold">{report.summary.valid_sitemap}</span></div>
                <div>Valid RSS: <span className="font-semibold">{report.summary.valid_rss}</span></div>
                <div>Both valid: <span className="font-semibold">{report.summary.both_valid}</span></div>
                <div>Neither valid: <span className="font-semibold">{report.summary.neither_valid}</span></div>
              </div>
            </div>

            {(report.details || []).length > 0 && (
              <div className="overflow-auto border rounded-lg bg-white">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-100">
                    <tr>
                      <th className="p-2 text-left">Publication</th>
                      <th className="p-2 text-left">Base URL</th>
                      <th className="p-2 text-left">Sitemap URL</th>
                      <th className="p-2 text-left">Sitemap Valid</th>
                      <th className="p-2 text-left">RSS URL</th>
                      <th className="p-2 text-left">RSS Valid</th>
                      <th className="p-2 text-left">Active After</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.details.map((r, idx) => (
                      <tr key={`${r.publication || "pub"}-${idx}`} className="border-t">
                        <td className="p-2">{r.publication || "-"}</td>
                        <td className="p-2">{r.base_url || "-"}</td>
                        <td className="p-2">{r.sitemap_url || "-"}</td>
                        <td className="p-2">{String(r.sitemap_valid || "-")}</td>
                        <td className="p-2">{r.rss_url || "-"}</td>
                        <td className="p-2">{String(r.rss_valid || "-")}</td>
                        <td className="p-2">{r.active_after || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
