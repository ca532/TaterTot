import { useEffect, useMemo, useState } from "react";
import { BarChart3 } from "lucide-react";
import githubAPI from "../services/githubAPI";
import TrendResultsList from "./TrendResultsList";

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
  const [viewStatus, setViewStatus] = useState("idle"); // idle|complete
  const [trendRunState, setTrendRunState] = useState("idle"); // idle|queued|running|completed|empty|failed
  const [trendRunMessage, setTrendRunMessage] = useState("");
  const [topic, setTopic] = useState("");
  const [sourceLists, setSourceLists] = useState([]);
  const [windowMode, setWindowMode] = useState("current_week"); // current_week|current_month|custom
  const [windowStartDate, setWindowStartDate] = useState("");
  const [windowEndDate, setWindowEndDate] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [isViewingResults, setIsViewingResults] = useState(false);
  const [trends, setTrends] = useState([]);
  const [weekKey, setWeekKey] = useState("");
  const [trendRunId, setTrendRunId] = useState("");
  const [lastWindowMode, setLastWindowMode] = useState("");
  const [lastWindowStartDate, setLastWindowStartDate] = useState("");
  const [lastWindowEndDate, setLastWindowEndDate] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const now = useMemo(() => new Date(), []);
  const currentWeekDate = useMemo(() => {
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }, [now]);
  const currentWeekKey = useMemo(() => toIsoWeekKeyFromDateInput(currentWeekDate), [currentWeekDate]);

  const currentMonthBounds = useMemo(() => {
    const y = now.getFullYear();
    const m = now.getMonth();
    const start = new Date(y, m, 1);
    const end = new Date(y, m + 1, 0);
    const fmt = (dt) =>
      `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
    return { start: fmt(start), end: fmt(end) };
  }, [now]);

  const rangeValid = !windowStartDate || !windowEndDate || windowStartDate <= windowEndDate;

  useEffect(() => {
    const loadSourceLists = async () => {
      const res = await githubAPI.getSourceLists();
      if (!res.success) return;
      setSourceLists(res.lists || []);
      if (!topic && (res.lists || []).length > 0) {
        setTopic(res.lists[0].list_name);
      }
    };
    loadSourceLists();
  }, [topic]);

  const onRun = async () => {
    setError("");
    setMessage("");
    if (windowMode === "custom" && !rangeValid) {
      setError("End date must be on or after start date.");
      return;
    }

    let targetWeekKey = "";
    let payloadStart = "";
    let payloadEnd = "";

    if (windowMode === "current_week") {
      targetWeekKey = currentWeekKey;
      payloadStart = "";
      payloadEnd = "";
    } else if (windowMode === "current_month") {
      targetWeekKey = "";
      payloadStart = "";
      payloadEnd = "";
    } else {
      if (!windowStartDate || !windowEndDate) {
        setError("For custom range, please select both start and end date.");
        return;
      }
      targetWeekKey = "";
      payloadStart = windowStartDate;
      payloadEnd = windowEndDate;
    }

    setIsRunning(true);
    console.log("[TREND_UI_TRIGGER]", {
      topic,
      windowMode,
      targetWeekKey,
      windowStartDate: payloadStart,
      windowEndDate: payloadEnd,
    });
    const res = await githubAPI.triggerTrendAnalysis({
      topic,
      target_week_key: targetWeekKey,
      window_start_date: payloadStart,
      window_end_date: payloadEnd,
      window_mode: windowMode,
    });
    setIsRunning(false);
    console.log("[TREND_UI_TRIGGER_RESULT]", res);

    if (!res.success) {
      setError(res.error || "Failed to trigger trend analysis workflow.");
      setTrendRunState("failed");
      setTrendRunMessage(res.error || "Failed to trigger trend analysis workflow.");
      return;
    }
    setLastWindowMode(windowMode);
    setLastWindowStartDate(payloadStart);
    setLastWindowEndDate(payloadEnd);
    setTrendRunId(res.trend_run_id || "");
    let suffix = " for current week";
    if (windowMode === "current_month") suffix = " for current month";
    if (windowMode === "custom") {
      suffix = ` for custom range ${formatDdMmYyyy(windowStartDate)} to ${formatDdMmYyyy(windowEndDate)}`;
    }
    const queuedMsg = `Trend analysis workflow queued successfully${suffix}.`;
    setMessage(queuedMsg);
    setTrendRunState("queued");
    setTrendRunMessage(queuedMsg);
    if (res.trend_run_id) {
      startTrendRunPolling(res.trend_run_id);
    }
  };

  const startTrendRunPolling = (runId) => {
    let attempts = 0;
    const maxAttempts = 60;
    const intervalMs = 5000;

    setTrendRunState("running");
    setTrendRunMessage("Trend analysis is running...");

    const timer = setInterval(async () => {
      attempts += 1;
      try {
        console.log("[TREND_UI_POLL]", { trendRunId: runId, attempt: attempts });
        const data = await githubAPI.getTrendsByRun(runId);
        const list = Array.isArray(data?.trends) ? data.trends : [];
        const hasSentinel = list.some((t) => t.keyword === "__NO_TRENDS__" || t.status === "no_trends");
        console.log("[TREND_UI_POLL_RESULT]", { count: list.length, hasSentinel });

        if (list.length > 0) {
          if (hasSentinel) {
            setTrendRunState("empty");
            setTrendRunMessage("Run completed, but no trends passed thresholds for this window.");
          } else {
            setTrendRunState("completed");
            setTrendRunMessage(`Run completed with ${list.length} trend result(s).`);
          }
          clearInterval(timer);
          return;
        }

        if (attempts >= maxAttempts) {
          setTrendRunState("failed");
          setTrendRunMessage("Timed out waiting for trend results. Please check workflow logs.");
          clearInterval(timer);
        }
      } catch (e) {
        setTrendRunState("failed");
        setTrendRunMessage(`Trend status check failed: ${e?.message || "unknown error"}`);
        clearInterval(timer);
      }
    }, intervalMs);
  };

  const onViewResults = async () => {
    setError("");
    setMessage("");
    setIsViewingResults(true);
    console.log("[TREND_UI_FETCH_RESULTS]", { trendRunId });
    const res = trendRunId
      ? await githubAPI.getTrendsByRun(trendRunId)
      : await githubAPI.getLatestTrends();
    setIsViewingResults(false);
    console.log("[TREND_UI_FETCH_RESULTS_RESULT]", {
      success: res.success,
      trend_run_id: res.trend_run_id,
      count: (res.trends || []).length,
    });
    if (!res.success) {
      setError(res.error || "Failed to load trend results.");
      return;
    }
    setTrends((res.trends || []).filter((t) => t.keyword !== "__NO_TRENDS__"));
    setWeekKey(res.week_key || "");
    setViewStatus("complete");
  };

  if (viewStatus === "complete") {
    return (
      <TrendResultsList
        trends={trends}
        weekKey={weekKey}
        trendRunId={trendRunId}
        windowMode={lastWindowMode}
        windowStartDate={lastWindowStartDate}
        windowEndDate={lastWindowEndDate}
        onRunAgain={() => {
          setViewStatus("idle");
          setTrends([]);
          setWeekKey("");
          setTrendRunId("");
          setLastWindowMode("");
          setLastWindowStartDate("");
          setLastWindowEndDate("");
          setError("");
          setMessage("");
        }}
      />
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[#faf8f3] border-2 border-[#b8860b] mb-4">
          <BarChart3 className="w-8 h-8 text-[#b8860b]" />
        </div>
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Trend Analysis</h2>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-6 space-y-5">
        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Topic</label>
          <select
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b] bg-white"
          >
            {sourceLists.map((s) => (
              <option key={s.list_name} value={s.list_name}>
                {s.list_name} ({s.active_rows}/{s.total_rows} active)
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-semibold text-gray-700 mb-2">Window</label>
          <select
            value={windowMode}
            onChange={(e) => setWindowMode(e.target.value)}
            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b] bg-white"
          >
            <option value="current_week">Current Week</option>
            <option value="current_month">Current Month</option>
            <option value="custom">Custom</option>
          </select>
          <p className="text-xs text-gray-500 mt-1">
            {windowMode === "current_month" && "Uses current calendar month."}
            {windowMode === "custom" && "Select a start and end date."}
          </p>
        </div>

        {windowMode === "custom" && (
          <>
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">Start Date</label>
              <input
                type="date"
                value={windowStartDate}
                onChange={(e) => setWindowStartDate(e.target.value)}
                className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b]"
              />
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">End Date</label>
              <input
                type="date"
                value={windowEndDate}
                onChange={(e) => setWindowEndDate(e.target.value)}
                className={`w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b] ${
                  rangeValid ? "border-gray-300" : "border-red-400"
                }`}
              />
            </div>
          </>
        )}

        {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">{error}</div>}
        {message && <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded p-3">{message}</div>}
        {trendRunState !== "idle" && (
          <div
            className={`text-sm rounded p-3 border ${
              trendRunState === "completed"
                ? "text-green-700 bg-green-50 border-green-200"
                : trendRunState === "empty"
                  ? "text-amber-700 bg-amber-50 border-amber-200"
                  : trendRunState === "failed"
                    ? "text-red-700 bg-red-50 border-red-200"
                    : "text-blue-700 bg-blue-50 border-blue-200"
            }`}
          >
            {trendRunMessage}
          </div>
        )}

        <button
          type="button"
          onClick={onRun}
          disabled={isRunning}
          className="inline-flex items-center justify-center px-6 py-3 bg-[#b8860b] text-black font-bold rounded-lg hover:bg-[#8b6914] disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {isRunning ? "Triggering..." : "Run Trend Analysis"}
        </button>
        <button
          type="button"
          onClick={onViewResults}
          disabled={isViewingResults}
          className="ml-0 sm:ml-3 mt-3 sm:mt-0 inline-flex items-center justify-center px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] hover:bg-[#faf8f3] disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {isViewingResults ? "Loading Results..." : "View Trend Results"}
        </button>
      </div>
    </div>
  );
}
