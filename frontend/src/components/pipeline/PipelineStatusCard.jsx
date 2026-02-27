import { AlertCircle, Circle } from "lucide-react";

const LABELS = {
  idle: "Ready",
  queued: "Queued",
  running: "Running",
  success: "Success",
  failed: "Failed",
};

export default function PipelineStatusCard({ runStatus, errorMessage }) {
  const isActive = runStatus === "queued" || runStatus === "running";
  const isFailed = runStatus === "failed";

  return (
    <div className="mb-4 flex flex-col items-center gap-2">
      <div className="inline-flex items-center gap-2 px-3 py-1 bg-gray-50 rounded-full border border-gray-200">
        <Circle
          className={`w-2 h-2 fill-current ${
            isFailed ? "text-red-500" : isActive ? "text-[#b8860b] animate-pulse" : "text-gray-400"
          }`}
        />
        <span className="text-xs font-semibold text-gray-700">{LABELS[runStatus] || "Ready"}</span>
      </div>

      {errorMessage && (
        <div className="w-full max-w-3xl p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}
    </div>
  );
}

