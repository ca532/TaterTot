import { useState, useEffect } from 'react';
import { Loader2, CheckCircle, Clock } from 'lucide-react';

function LoadingScreen() {
  const API_BASE = import.meta.env.VITE_PIPELINE_API_BASE || "http://localhost:8000";
  const WS_BASE = API_BASE.replace("https://", "wss://").replace("http://", "ws://");
  const [live, setLive] = useState({ status: "queued", phase: "initializing" });

  const phaseToStep = {
    initializing: "Initializing pipeline...",
    collecting: "Collecting articles from publications...",
    summarizing: "Running AI summarization...",
    saving: "Running AI summarization...",
    complete: "Complete",
    failed: "Failed",
    idle: "Idle",
  };

  const steps = [
    { key: "initializing", label: "Initializing pipeline..." },
    { key: "collecting", label: "Collecting articles from publications..." },
    { key: "summarizing", label: "Running AI summarization..." },
  ];

  useEffect(() => {
    let ws = null;
    let retry = 0;
    let retryTimer = null;
    let isClosed = false;

    const connect = () => {
      if (isClosed) return;
      ws = new WebSocket(`${WS_BASE}/pipeline/ws`);

      ws.onopen = () => {
        retry = 0;
      };

      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          setLive((prev) => ({ ...prev, ...data }));
        } catch (_e) {
          // no-op
        }
      };

      ws.onclose = () => {
        if (isClosed) return;
        const backoffMs = Math.min(15000, 1000 * (2 ** retry));
        retry += 1;
        retryTimer = setTimeout(connect, backoffMs);
      };

      ws.onerror = () => {
        try {
          ws.close();
        } catch (_e) {
          // no-op
        }
      };
    };

    connect();

    return () => {
      isClosed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        ws.close();
      }
    };
  }, [WS_BASE]);

  const phaseOrder = ["initializing", "collecting", "summarizing"];
  const phaseIndex = phaseOrder.indexOf(live.phase);
  const currentStep = phaseIndex >= 0 ? phaseIndex : 0;
  const completedSteps = live.phase === "complete" ? steps.length : Math.max(0, currentStep);

  const progress = (() => {
    if (live.phase === "complete") return 100;
    if (live.phase === "failed") return 100;
    if (live.phase === "idle") return 5;
    if (phaseIndex < 0) return 10;
    return Math.min(90, 10 + phaseIndex * 25);
  })();

  return (
    <div className="w-full h-full flex items-center justify-center px-4 sm:px-6 py-4">
      {/* Main Loading Card - Compact */}
      <div className="w-full max-w-2xl bg-white rounded-lg shadow-2xl border-2 border-[#b8860b] p-4 sm:p-8 text-center">
        {/* Animated Icon */}
        <div className="inline-flex items-center justify-center w-16 h-16 sm:w-20 sm:h-20 rounded-full bg-[#faf8f3] border-2 border-[#b8860b] mb-4">
          <Loader2 className="w-8 h-8 sm:w-10 sm:h-10 text-[#b8860b] animate-spin" />
        </div>

        {/* Status Text */}
        <h2 className="text-xl sm:text-2xl font-bold text-gray-900 mb-2">
          Pipeline Running
        </h2>
        <p className="text-sm sm:text-base text-gray-600 mb-6">
          {phaseToStep[live.phase] || "Processing..."}
        </p>

        {/* Progress Bar */}
        <div className="mb-6">
          <div className="w-full bg-gray-200 rounded-full h-3 mb-2">
            <div 
              className="bg-[#b8860b] h-3 rounded-full transition-all duration-300 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-xs sm:text-sm text-gray-500 font-semibold">{progress}% complete</p>
        </div>

        {/* Step Progress - Compact */}
        <div className="space-y-3">
          {steps.map((step, index) => (
            <div 
              key={index}
              className={`flex items-center gap-3 p-3 rounded-lg transition-all ${
                index === currentStep && live.phase !== "complete"
                  ? 'bg-[#faf8f3] border-2 border-[#b8860b]' 
                  : index < completedSteps
                  ? 'bg-green-50 border-2 border-green-500'
                  : 'bg-white border-2 border-gray-200'
              }`}
            >
              {index < completedSteps ? (
                <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0" />
              ) : index === currentStep && live.phase !== "complete" ? (
                <Loader2 className="w-5 h-5 text-[#b8860b] flex-shrink-0 animate-spin" />
              ) : (
                <Clock className="w-5 h-5 text-gray-400 flex-shrink-0" />
              )}
              <span className={`text-xs sm:text-sm font-semibold ${
                index === currentStep && live.phase !== "complete"
                  ? 'text-gray-900' 
                  : index < completedSteps
                  ? 'text-green-900'
                  : 'text-gray-500'
              }`}>
                {step.label}
              </span>
            </div>
          ))}
        </div>

        {/* Fun fact - Compact */}
        <div className="mt-6 p-3 bg-gradient-to-r from-[#faf8f3] to-[#f5f1e6] rounded-lg border-2 border-[#b8860b]">
          <p className="text-sm text-gray-700">
            ✨ <span className="font-bold">Did you know?</span> Our AI reads and summarizes articles 100x faster than a human!
          </p>
        </div>
      </div>
    </div>
  );
}

export default LoadingScreen;
