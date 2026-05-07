import { Play, FileText, Download } from "lucide-react";

export default function RunActions({
  keywordsInput,
  setKeywordsInput,
  onRunPipeline,
  onViewResults,
  onDownloadPDF,
  hasPDF,
  runDisabled,
  runDisabledReason,
  viewResultsDisabled = false,
  viewResultsLabel = "View Results",
}) {
  const keywordCount = keywordsInput
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean).length;

  const hasKeywords = keywordCount > 0;
  const belowMin = hasKeywords && keywordCount < 5;
  const aboveRecommended = keywordCount > 15;
  const aboveMax = keywordCount > 25;

  const helperClass = aboveMax || belowMin ? "text-red-600" : aboveRecommended ? "text-yellow-700" : "text-gray-500";
  const helperText = !hasKeywords
    ? "Leave blank to use default pipeline keywords. Recommended 8-15 when overriding."
    : aboveMax
    ? "Too many keywords. Maximum is 25."
    : belowMin
    ? "Too few keywords. Minimum is 5 when overriding."
    : aboveRecommended
    ? "Outside recommended range (8-15). Results may be over-constrained."
    : "Good range.";

  return (
    <>
      <div className="w-full max-w-3xl mx-auto mb-4">
        <label className="block text-sm font-semibold text-gray-700 mb-2">Keywords (optional)</label>
        <textarea
          value={keywordsInput}
          onChange={(e) => setKeywordsInput(e.target.value)}
          placeholder="Comma-separated keywords (recommended 8-15)"
          className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b] focus:border-[#b8860b] text-sm sm:text-base"
          rows={3}
        />
        <div className="mt-1 flex items-center justify-between gap-2">
          <p className={`text-xs ${helperClass}`}>{helperText}</p>
          <p className={`text-xs font-semibold ${helperClass}`}>{keywordCount} keyword{keywordCount === 1 ? "" : "s"}</p>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-center gap-3 w-full">
        <button
          onClick={onRunPipeline}
          disabled={runDisabled}
          className="inline-flex items-center justify-center gap-3 w-full sm:w-auto px-6 sm:px-8 py-3 bg-[#b8860b] text-black font-bold rounded-lg hover:bg-[#8b6914] transition-all duration-200 shadow-xl hover:shadow-2xl disabled:bg-gray-300 disabled:cursor-not-allowed"
          title={runDisabledReason || "Run pipeline"}
        >
          <Play className="w-5 h-5" />
          <span className="text-base">Run Pipeline</span>
        </button>

        <button
          onClick={onViewResults}
          disabled={viewResultsDisabled}
          className="inline-flex items-center justify-center gap-2 w-full sm:w-auto px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] hover:bg-[#faf8f3] transition-all shadow-md hover:shadow-lg disabled:opacity-60 disabled:cursor-not-allowed"
        >
          <FileText className="w-5 h-5" />
          <span>{viewResultsLabel}</span>
        </button>

        {hasPDF && (
          <button
            onClick={onDownloadPDF}
            className="inline-flex items-center justify-center gap-2 w-full sm:w-auto px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] hover:bg-[#faf8f3] transition-all shadow-md hover:shadow-lg"
          >
            <Download className="w-5 h-5" />
            <span>Download PDF</span>
          </button>
        )}
      </div>
    </>
  );
}
