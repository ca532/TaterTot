import { useMemo, useState } from "react";
import { BarChart3 } from "lucide-react";
import githubAPI from "../services/githubAPI";

function isValidWeekKey(v) {
  if (!v) return true;
  return /^\d{4}-W(0[1-9]|[1-4][0-9]|5[0-3])$/.test(v.trim());
}

export default function TrendAnalysisView() {
  const [topic, setTopic] = useState("luxury");
  const [targetWeekKey, setTargetWeekKey] = useState("");
  const [extraStopwords, setExtraStopwords] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const weekKeyValid = useMemo(() => isValidWeekKey(targetWeekKey), [targetWeekKey]);

  const onRun = async () => {
    setError("");
    setMessage("");
    if (!weekKeyValid) {
      setError("Target week must be blank or in format YYYY-Www (example: 2026-W18).");
      return;
    }

    setIsRunning(true);
    const res = await githubAPI.triggerTrendAnalysis({
      topic,
      target_week_key: targetWeekKey.trim(),
      extra_stopwords: extraStopwords.trim(),
    });
    setIsRunning(false);

    if (!res.success) {
      setError(res.error || "Failed to trigger trend analysis workflow.");
      return;
    }
    setMessage("Trend analysis workflow queued successfully.");
  };

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[#faf8f3] border-2 border-[#b8860b] mb-4">
          <BarChart3 className="w-8 h-8 text-[#b8860b]" />
        </div>
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Trend Analysis</h2>
        <p className="text-gray-600">Run Phase 4A trend extraction workflow for a selected topic and week.</p>
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
          <label className="block text-sm font-semibold text-gray-700 mb-2">Target Week (optional)</label>
          <input
            type="text"
            value={targetWeekKey}
            onChange={(e) => setTargetWeekKey(e.target.value)}
            placeholder="YYYY-Www (example: 2026-W18)"
            className={`w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b] ${
              weekKeyValid ? "border-gray-300" : "border-red-400"
            }`}
          />
          <p className="text-xs text-gray-500 mt-1">Leave blank to use current ISO week.</p>
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
