import { useEffect, useState } from "react";
import { AlertCircle, Clock } from "lucide-react";
import LoadingScreen from "./LoadingScreen";
import ArticlesList from "./ArticlesList";
import googleSheetsAPI from "../services/googleSheetsAPI";
import githubAPI from "../services/githubAPI";
import rateLimitService from "../services/rateLimitService";
import usePipelineRunner from "../hooks/usePipelineRunner";
import RunActions from "./pipeline/RunActions";
import PipelineStatusCard from "./pipeline/PipelineStatusCard";
import StatsCards from "./pipeline/StatsCards";
import logo from "../assets/ca-circle.png";

function SummariesView() {
  const [viewStatus, setViewStatus] = useState("idle"); // idle|running|complete
  const [isViewingResults, setIsViewingResults] = useState(false);
  const [articles, setArticles] = useState([]);
  const [lastRunTime, setLastRunTime] = useState(null);
  const [pdfLink, setPdfLink] = useState(null);
  const [rateLimitInfo, setRateLimitInfo] = useState(null);

  const updateRateLimitInfo = () => {
    const info = rateLimitService.canRunPipeline();
    setRateLimitInfo(info);
  };

  const syncRateLimitFromBackend = async () => {
    try {
      const status = await githubAPI.getLatestRunStatus();
      if (status) {
        rateLimitService.syncFromBackendStatus(status);
      }
    } catch (error) {
      console.error("Error syncing cooldown from backend:", error);
    } finally {
      updateRateLimitInfo();
    }
  };

  const loadLastRunDate = async () => {
    try {
      const articlesData = await googleSheetsAPI.getArticles();
      if (articlesData && articlesData.length > 0) {
        const dates = articlesData.map((a) => new Date(a.collectedDate));
        const mostRecent = new Date(Math.max(...dates));
        setLastRunTime(mostRecent);
      } else {
        setLastRunTime(null);
      }
    } catch (error) {
      console.error("Error loading last run date:", error);
      setLastRunTime(null);
    }
  };

  const checkForPDF = async () => {
    try {
      const runInfo = await googleSheetsAPI.getLatestRunInfo();
      if (runInfo) {
        setPdfLink({
          runNumber: runInfo.runNumber,
          runUrl: runInfo.runUrl,
          artifactName: `roundup-files-${runInfo.runNumber}`,
        });
      }
    } catch (error) {
      console.error("Error checking for PDF:", error);
    }
  };

  const loadResultsAfterPipeline = async () => {
    try {
      const articlesData = await googleSheetsAPI.getArticles();
      if (articlesData.length > 0) {
        setArticles(articlesData);
        const dates = articlesData.map((a) => new Date(a.collectedDate));
        setLastRunTime(new Date(Math.max(...dates)));

        const runInfo = await googleSheetsAPI.getLatestRunInfo();
        if (runInfo) {
          setPdfLink({
            runNumber: runInfo.runNumber,
            runUrl: runInfo.runUrl,
          });
        } else {
          setPdfLink({ available: true });
        }
        setViewStatus("complete");
      } else {
        alert("No new articles yet. Pipeline may still be running.");
        setViewStatus("idle");
      }
    } catch (error) {
      console.error("Error loading results:", error);
      setViewStatus("idle");
    }
  };

  const {
    runStatus,
    setRunStatus,
    errorMessage,
    keywordsInput,
    setKeywordsInput,
    triggerRun,
  } = usePipelineRunner({
    onSuccess: async () => {
      rateLimitService.recordPipelineComplete();
      updateRateLimitInfo();
      await loadResultsAfterPipeline();
    },
    onFailure: () => {
      rateLimitService.manualClearRunning();
      updateRateLimitInfo();
      setViewStatus("idle");
    },
  });

  useEffect(() => {
    checkForPDF();
    loadLastRunDate();
    syncRateLimitFromBackend();
  }, []);

  useEffect(() => {
    const interval = setInterval(syncRateLimitFromBackend, 10000);
    return () => clearInterval(interval);
  }, []);

const handleRunPipeline = async () => {
  const rateLimitCheck = rateLimitService.canRunPipeline();
  if (!rateLimitCheck.canRun) {
    alert(`Rate Limit\n\n${rateLimitCheck.reason}`);
    return;
  }

  const confirmed = window.confirm(
    "Are you sure you want to run the pipeline?\n\nThis process usually takes 15-20 minutes."
  );
  if (!confirmed) return;

  setViewStatus("running");

  const result = await triggerRun({
    canRun: rateLimitCheck.canRun,
    reason: rateLimitCheck.reason,
  });

  if (!result.success) {
    rateLimitService.manualClearRunning();
    updateRateLimitInfo();
    setViewStatus("idle");
    alert(`Failed to trigger pipeline.\n\nError: ${result.error}`);
    return;
  }

  // Start cooldown ONLY after successful trigger
  rateLimitService.recordPipelineStart();
  updateRateLimitInfo();
};


  const handleViewResults = async () => {
    setIsViewingResults(true);
    try {
      await loadResultsAfterPipeline();
    } finally {
      setIsViewingResults(false);
    }
  };

  const handleDownloadPDF = async () => {
    try {
      const result = await githubAPI.downloadLatestArtifactZip();
      if (!result.success) {
        alert(result.error || "No PDF available yet. Run the pipeline first to generate a PDF.");
        return;
      }

      const url = window.URL.createObjectURL(result.blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = result.filename || "latest-artifact.zip";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error("Error downloading PDF:", error);
      alert("Failed to download latest PDF.");
    }
  };

  if (viewStatus === "running" || runStatus === "queued" || runStatus === "running") {
    return <LoadingScreen />;
  }

  if (viewStatus === "complete") {
    return (
      <ArticlesList
        articles={articles}
        onRunAgain={() => {
          setViewStatus("idle");
          setRunStatus("idle");
          setArticles([]);
          updateRateLimitInfo();
        }}
        lastRunTime={lastRunTime}
        onDownloadPDF={handleDownloadPDF}
        hasPDF={!!pdfLink}
      />
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6">
      {rateLimitInfo && !rateLimitInfo.canRun && (
        <div className="mb-6 p-4 bg-yellow-50 border-2 border-yellow-400 rounded-lg flex items-start gap-3">
          <AlertCircle className="w-6 h-6 text-yellow-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="font-bold text-yellow-900 mb-1">Cooldown Period Active</p>
            <p className="text-sm text-yellow-800">{rateLimitInfo.reason}</p>
            {rateLimitInfo.nextAvailableTime && (
              <p className="text-xs text-yellow-700 mt-1">
                Next available: {rateLimitInfo.nextAvailableTime.toLocaleTimeString()}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-20 h-20 mb-4">
          <img src={logo} alt="CA Logo" className="w-20 h-20" />
        </div>
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Article Pipeline Ready</h2>
        <p className="text-base text-gray-600 mb-6">
          Click the button below to collect and summarize the latest articles from your publications.
        </p>

        <PipelineStatusCard runStatus={runStatus} errorMessage={errorMessage} />

        <RunActions
          keywordsInput={keywordsInput}
          setKeywordsInput={setKeywordsInput}
          onRunPipeline={handleRunPipeline}
          onViewResults={handleViewResults}
          onDownloadPDF={handleDownloadPDF}
          hasPDF={!!pdfLink}
          runDisabled={rateLimitInfo && !rateLimitInfo.canRun}
          runDisabledReason={rateLimitInfo?.reason}
          viewResultsDisabled={isViewingResults}
          viewResultsLabel={isViewingResults ? "Loading Results..." : "View Results"}
        />

        {rateLimitInfo && !rateLimitInfo.canRun && (
          <p className="mt-3 text-sm text-gray-600">
            Time remaining:{" "}
            <span className="font-bold text-[#b8860b]">{rateLimitService.formatRemainingTime()}</span>
          </p>
        )}
      </div>

      <StatsCards lastRunTime={lastRunTime} />

      <div className="bg-gradient-to-br from-[#faf8f3] to-[#f5f1e6] rounded-lg p-4 sm:p-6 border-2 border-[#b8860b] shadow-lg">
        <h3 className="text-lg font-bold text-gray-900 mb-3">How it works</h3>
        <ol className="space-y-2">
          <li className="text-sm text-gray-700">1. Collect articles from 40+ publications</li>
          <li className="text-sm text-gray-700">2. AI summarizes each article</li>
          <li className="text-sm text-gray-700">3. Results saved to Google Sheets and PDF uploaded</li>
        </ol>
        <div className="mt-4 pt-4 border-t-2 border-[#b8860b]">
          <p className="text-xs text-gray-600 flex items-center gap-2">
            <Clock className="w-3 h-3" />
            <span>Rate limit: Maximum one run every {rateLimitService.COOLDOWN_MINUTES} minutes.</span>
          </p>
        </div>
      </div>
    </div>
  );
}

export default SummariesView;
