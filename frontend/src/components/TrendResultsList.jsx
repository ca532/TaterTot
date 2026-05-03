import { ExternalLink, RefreshCw, Calendar, Hash } from "lucide-react";

function splitUrls(raw) {
  return String(raw || "")
    .split("|")
    .map((x) => x.trim())
    .filter(Boolean);
}

export default function TrendResultsList({ trends = [], weekKey = "", onRunAgain }) {
  return (
    <div className="w-full h-full flex flex-col px-4 sm:px-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-6 gap-4">
        <div>
          <h2 className="text-3xl font-bold text-gray-900">Trend Analysis Results</h2>
          <p className="text-base text-gray-600 mt-2">
            {trends.length} trends {weekKey ? `for ${weekKey}` : "for current week"}
          </p>
        </div>

        <button
          onClick={onRunAgain}
          className="inline-flex items-center justify-center gap-2 w-full sm:w-auto px-6 py-3 bg-[#b8860b] text-black font-semibold rounded-lg hover:bg-[#8b6914] transition-colors shadow-md hover:shadow-lg"
        >
          <RefreshCw className="w-5 h-5" />
          Home
        </button>
      </div>

      <div className="mb-8 p-4 sm:p-5 bg-[#faf8f3] border-2 border-[#b8860b] rounded-lg">
        <p className="font-bold text-lg text-gray-900">Trend analysis completed</p>
        <p className="text-base text-gray-700">
          Showing highest-signal trend keywords with baseline and supporting coverage.
        </p>
      </div>

      {trends.length === 0 && (
        <div className="text-center py-12">
          <p className="text-gray-600 text-lg">No trends found for this week.</p>
          <p className="text-gray-500 mt-2">Try another date or reduce filtering constraints.</p>
        </div>
      )}

      {trends.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {trends.map((t, idx) => {
            const urls = splitUrls(t.supporting_urls);
            return (
              <div key={`${t.keyword}-${idx}`} className="bg-white rounded-lg shadow-lg border border-gray-200 p-5">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <h3 className="text-lg font-semibold text-gray-900">{t.keyword}</h3>
                  <span className="px-2 py-1 text-xs font-bold rounded bg-[#faf8f3] text-[#8b6914] border border-[#b8860b]">
                    {t.status || "trending"}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-3 mb-4 text-sm">
                  <div className="bg-gray-50 rounded p-2">
                    <p className="text-gray-500">Current</p>
                    <p className="font-semibold text-gray-900">{t.count_current}</p>
                  </div>
                  <div className="bg-gray-50 rounded p-2">
                    <p className="text-gray-500">Baseline (4wk)</p>
                    <p className="font-semibold text-gray-900">{t.baseline_4wk}</p>
                  </div>
                  <div className="bg-gray-50 rounded p-2">
                    <p className="text-gray-500">Change</p>
                    <p className="font-semibold text-gray-900">{t.pct_change}%</p>
                  </div>
                  <div className="bg-gray-50 rounded p-2">
                    <p className="text-gray-500">Trend Score</p>
                    <p className="font-semibold text-gray-900">{t.trend_score}</p>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3 text-sm text-gray-600 mb-3">
                  <span className="inline-flex items-center gap-1">
                    <Calendar className="w-4 h-4" />
                    {t.week_key || weekKey}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Hash className="w-4 h-4" />
                    Publications: {t.publication_count}
                  </span>
                </div>

                {urls.length > 0 && (
                  <div>
                    <p className="text-sm font-semibold text-gray-800 mb-2">Supporting Articles</p>
                    <div className="space-y-2">
                      {urls.map((u, uidx) => (
                        <a
                          key={`${u}-${uidx}`}
                          href={u}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-2 text-sm text-[#b8860b] hover:text-[#8b6914] break-all"
                        >
                          <ExternalLink className="w-4 h-4 shrink-0" />
                          {u}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
