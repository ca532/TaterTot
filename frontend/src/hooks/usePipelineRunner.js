import { useEffect, useRef, useState } from "react";
import githubAPI from "../services/githubAPI";

function parseKeywords(value) {
  return value
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
}

function validateKeywords(keywords) {
  if (keywords.length === 0) {
    return { valid: true, error: null };
  }
  if (keywords.length < 5) {
    return { valid: false, error: "Please enter at least 5 keywords, or leave it blank to use defaults." };
  }
  if (keywords.length > 25) {
    return { valid: false, error: "Please enter no more than 25 keywords." };
  }
  return { valid: true, error: null };
}

function mapStatus(status, conclusion) {
  if (status === "queued") return "queued";
  if (status === "running" || status === "in_progress") return "running";
  if (status === "success" || (status === "completed" && conclusion === "success")) return "success";
  if (status === "failed") return "failed";
  if (status === "completed" && ["failure", "cancelled", "timed_out"].includes(conclusion)) return "failed";
  return "idle";
}

export default function usePipelineRunner({ onSuccess, onFailure }) {
  const [runStatus, setRunStatus] = useState("idle");
  const [errorMessage, setErrorMessage] = useState(null);
  const [keywordsInput, setKeywordsInput] = useState("");
  const [activeRunId, setActiveRunId] = useState(null);
  const activeRunIdRef = useRef(null);
  const hasSeenActiveStatusRef = useRef(false);
  const pollRef = useRef(null);
  const failedStreakRef = useRef(0);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const pollStatus = async () => {
    try {
      const data = await githubAPI.getLatestRunStatus();
      if (!data) return;

      const backendStatus = String(data.status || "").toLowerCase();
      const backendRunId = data.runId ? String(data.runId) : null;
      const expectedRunId = activeRunIdRef.current
        ? String(activeRunIdRef.current)
        : null;
      const isActiveStatus = ["queued", "running", "in_progress"].includes(backendStatus);

      if (expectedRunId && backendRunId !== expectedRunId) {
        return;
      }

      if (!expectedRunId && backendRunId && isActiveStatus) {
        activeRunIdRef.current = backendRunId;
        setActiveRunId(backendRunId);
      }

      if (isActiveStatus) {
        hasSeenActiveStatusRef.current = true;
      }

      const normalized = mapStatus(data.status, data.conclusion);
      const resolvedRunId = activeRunIdRef.current
        ? String(activeRunIdRef.current)
        : null;

      if (
        ["success", "failed"].includes(normalized)
        && (
          !resolvedRunId
          || backendRunId !== resolvedRunId
          || !hasSeenActiveStatusRef.current
        )
      ) {
        return;
      }

      if (normalized === "failed") {
        failedStreakRef.current += 1;
      } else {
        failedStreakRef.current = 0;
      }
      setRunStatus(normalized);

      if (normalized === "success") {
        stopPolling();
        await onSuccess?.();
      } else if (normalized === "failed") {
        if (failedStreakRef.current < 2) {
          return;
        }
        stopPolling();
        const msg = "Pipeline failed. Check logs and try again.";
        setErrorMessage(msg);
        onFailure?.(msg);
      }
    } catch (error) {
      // keep polling on transient network failures
      console.error("Status polling error:", error);
    }
  };

  const startPolling = () => {
    stopPolling();
    pollRef.current = setInterval(pollStatus, 10000);
  };

  const triggerRun = async ({ canRun, reason, listName }) => {
    if (!canRun) {
      setErrorMessage(reason || "Pipeline cannot run right now.");
      return { success: false, error: reason || "Rate limit active" };
    }

    const keywords = parseKeywords(keywordsInput);
    const validation = validateKeywords(keywords);
    if (!validation.valid) {
      setErrorMessage(validation.error);
      return { success: false, error: validation.error };
    }
    setErrorMessage(null);

    if (!listName || !String(listName).trim()) {
      const msg = "Please select a publication list.";
      setErrorMessage(msg);
      return { success: false, error: msg };
    }

    const topic = String(listName).trim();
    const payload = keywords.length > 0
      ? { keywords, topic, list_name: topic }
      : { topic, list_name: topic };
    const result = await githubAPI.triggerPipeline(payload);
    if (!result.success) {
      setRunStatus("idle");
      setErrorMessage(result.error || "Failed to trigger pipeline.");
      return result;
    }

    failedStreakRef.current = 0;
    hasSeenActiveStatusRef.current = false;
    setErrorMessage(null);

    const triggeredRunId = result.runId || result.activeRunId || null;
    activeRunIdRef.current = triggeredRunId;
    setActiveRunId(triggeredRunId);

    setRunStatus("queued");
    startPolling();
    return result;
  };

  useEffect(() => {
    return () => stopPolling();
  }, []);

  return {
    runStatus,
    setRunStatus,
    errorMessage,
    keywordsInput,
    setKeywordsInput,
    triggerRun,
    activeRunId,
  };
}
