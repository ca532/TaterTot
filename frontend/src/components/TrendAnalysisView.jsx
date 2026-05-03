import { useMemo, useState } from "react";
import { BarChart3 } from "lucide-react";
import githubAPI from "../services/githubAPI";

function toIsoWeekKeyFromDateInput(yyyyMmDd) {
  if (!yyyyMmDd) return "";
  const [y, m, d] = yyyyMmDd.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));

  const dayNum = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - dayNum);

  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil((((date - yearStart) / 86400000) + 1) / 7);
  const week = String(weekNo).padStart(2, "0");

  return `${date.getUTCFullYear()}-W${week}`;
}

function formatDdMmYyyy(yyyyMmDd) {
  if (!yyyyMmDd) return "";
  const [y, m, d] = yyyyMmDd.split("-");
  return `${d}/${m}/${y}`;
}

export default function TrendAnalysisView() {
  const [topic, setTopic] = useState("luxury");
  const [targetDate, setTargetDate] = useState("");
  const [extraStopwords, setExtraStopwords] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const targetWeekKey = useMemo(() => toIsoWeekKeyFromDateInput(targetDate), [targetDate]);
  const targetDateDisplay = useMemo(() => formatDdMmYyyy(targetDate), [targetDate]);

  const onRun = async () => {
    setError("");
    setMessage("");
    setIsRunning(true);
    const res = await githubAPI.triggerTrendAnalysis({
      topic,
      target_week_key: targetWeekKey,
      extra_stopwords: extraStopwords.trim(),
    });
    setIsRunning(false);

    if (!res.success) {
      setError(res.error || "Failed to trigger trend analysis workflow.");
      return;
    }
    const suffix = targetDateDisplay
      ? ` for ${targetDateDisplay} (${targetWeekKey})`
      : " for current week";
    setMessage(`Trend analysis workflow queued successfully${suffix}.`);
  };

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[#faf8f3] border-2 border-[#b8860b] mb-4">
          <BarChart3 className="w-8 h-8 text-[#b8860b]" />
        </div>
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Trend Analysis</h2>
        <p className="text-gray-600">Run Phase 4A trend extraction workflow for a selected topic and date.</p>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-5">
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Topic</label>
          <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden">
            <button
              type="button"
              onClick={() => setTopic("luxury")}
              className={`px-4 py-2 text-sm font-semibold ${topic === "luxury" ? "bg-[#b8860b] text-black" : "bg-white text-gray-700 hover:bg-gray-50"}`}
            >
              Luxury
            </button>
            <button
              type="button"
              onClick={() => setTopic("finance")}
              className={`px-4 py-2 text-sm font-semibold border-l border-gray-300 ${topic === "finance" ? "bg-[#b8860b] text-black" : "bg-white text-gray-700 hover:bg-gray-50"}`}
            >
              Finance
            </button>
          </div>
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Target Date (optional)</label>
          <input
            type="date"
            value={targetDate}
            onChange={(e) => setTargetDate(e.target.value)}
            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b]"
          />
          <p className="text-xs text-gray-500 mt-1">
            {targetDate
              ? `Selected: ${targetDateDisplay} (mapped to ${targetWeekKey})`
              : "Leave blank to use current week."}
          </p>
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Extra Stopwords (optional)</label>
          <textarea
            value={extraStopwords}
            onChange={(e) => setExtraStopwords(e.target.value)}
            placeholder="comma-separated stopwords"
            rows={3}
            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b]"
          />
        </div>

        {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">{error}</div>}
        {message && <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded p-3">{message}</div>}

        <button
          type="button"
          onClick={onRun}
          disabled={isRunning}
          className="inline-flex items-center justify-center px-6 py-3 bg-[#b8860b] text-black font-bold rounded-lg hover:bg-[#8b6914] disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {isRunning ? "Triggering..." : "Run Trend Analysis"}
        </button>
      </div>
    </div>
  );
}
