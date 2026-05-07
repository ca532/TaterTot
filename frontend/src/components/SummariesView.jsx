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
  const [starredByArticleId, setStarredByArticleId] = useState({});
  const [weekStars, setWeekStars] = useState([]);
  const [showStarredOnly, setShowStarredOnly] = useState(false);
  const [lastRunTime, setLastRunTime] = useState(null);
  const [pdfLink, setPdfLink] = useState(null);
  const [rateLimitInfo, setRateLimitInfo] = useState(null);
  const [sourceLists, setSourceLists] = useState([]);
  const [selectedListName, setSelectedListName] = useState("");
  const [newListName, setNewListName] = useState("");
  const [sourceRowsText, setSourceRowsText] = useState("");
  const [sourceReportSummary, setSourceReportSummary] = useState(null);
  const [sourceReportDetails, setSourceReportDetails] = useState([]);
  const [isRunningSourceMetadata, setIsRunningSourceMetadata] = useState(false);

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

  const articleIdFromUrl = (url) => githubAPI.normalizeUrlForId(url);

  const loadSourceLists = async () => {
    const res = await githubAPI.getSourceLists();
    if (!res.success) return;
    setSourceLists(res.lists || []);
    if (!selectedListName && (res.lists || []).length > 0) {
      setSelectedListName(res.lists[0].list_name);
    }
  };

  const parseSourceRows = (text) => {
    const lines = (text || "").split("\n").map((l) => l.trim()).filter(Boolean);
    const out = [];
    for (const line of lines) {
      const firstComma = line.indexOf(",");
      if (firstComma === -1) {
        out.push({ base_url: line.trim(), rss_url: "" });
      } else {
        out.push({
          base_url: line.slice(0, firstComma).trim(),
          rss_url: line.slice(firstComma + 1).trim(),
        });
      }
    }
    return out;
  };

  const handleCreateSourceList = async () => {
    const listName = (newListName || "").trim();
    if (!listName) {
      alert("Enter list name.");
      return;
    }
    const sources = parseSourceRows(sourceRowsText);
    if (!sources.length) {
      alert("Enter at least one source row: base_url, rss_url");
      return;
    }

    const res = await githubAPI.createSourceList({ list_name: listName, sources });
    if (!res.success) {
      alert(`Create list failed: ${res.error || "unknown error"}`);
      return;
    }

    alert(`Added ${res.inserted} rows to ${res.list_name}`);
    setNewListName("");
    setSourceRowsText("");
    await loadSourceLists();
    setSelectedListName(listName);
  };

  const handleRunSourceMetadata = async () => {
    if (!selectedListName) {
      alert("Select a list first.");
      return;
    }
    setIsRunningSourceMetadata(true);
    try {
      const run = await githubAPI.runSourceMetadata(selectedListName);
      if (!run.success) {
        alert(`Metadata run failed: ${run.error || "unknown error"}`);
        return;
      }
      const report = await githubAPI.getSourceMetadataReport(selectedListName, run.result?.run_id || "");
      if (!report.success) {
        alert("Run finished, but report fetch failed.");
        return;
      }
      setSourceReportSummary(report.summary);
      setSourceReportDetails(report.details || []);
    } finally {
      setIsRunningSourceMetadata(false);
    }
  };

  const loadCurrentWeekStars = async () => {
    const res = await githubAPI.getCurrentWeekStars();
    if (!res.success) return;
    setWeekStars(res.stars || []);
    const map = {};
    for (const s of res.stars) {
      const key = articleIdFromUrl(s.url || "");
      if (key) map[key] = s;
    }
    setStarredByArticleId(map);
  };

  const toggleStar = async (article) => {
    const articleId = articleIdFromUrl(article.url);
    const existing = starredByArticleId[articleId];

    if (existing?.star_id) {
      const del = await githubAPI.removeStarById(existing.star_id);
      if (!del.success) {
        alert(`Failed to unstar: ${del.error || "unknown error"}`);
        return;
      }
      setStarredByArticleId((prev) => {
        const next = { ...prev };
        delete next[articleId];
        return next;
      });
      setWeekStars((prev) => prev.filter((s) => (s.star_id || "") !== existing.star_id));
      return;
    }

    const add = await githubAPI.addStar(article);
    if (!add.success) {
      alert(`Failed to star: ${add.error || "unknown error"}`);
      return;
    }
    setStarredByArticleId((prev) => ({
      ...prev,
      [articleId]: { star_id: add.star_id, article_id: add.article_id }
    }));
    setWeekStars((prev) => [
      ...prev,
      {
        star_id: add.star_id,
        article_id: add.article_id,
        title: article.title,
        url: article.url,
        publication: article.publication,
        summary: article.summary,
        author: article.journalist || "Unknown",
        score: Number(article.score || 0),
        starred_at: new Date().toISOString(),
      },
    ]);
  };

  const loadResultsAfterPipeline = async () => {
    try {
      const articlesData = await googleSheetsAPI.getArticles();
      if (articlesData.length > 0) {
        setArticles(articlesData);
        setShowStarredOnly(false);
        await loadCurrentWeekStars();
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
        const usedKeywordOverride = (keywordsInput || "").trim().length > 0;
        alert(
          usedKeywordOverride
            ? "Pipeline completed, but 0 articles matched your keyword override. Try broader keywords or leave keywords blank to use defaults."
            : "Pipeline completed, but 0 articles were collected this run. Sources may have had no matching recent content."
        );
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
    topic,
    setTopic,
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
    loadSourceLists();
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
    "Are you sure you want to run the pipeline?\n\nThis process usually takes 1 and a half hour."
  );
  if (!confirmed) return;

  setViewStatus("running");

  const result = await triggerRun({
    canRun: rateLimitCheck.canRun,
    reason: rateLimitCheck.reason,
    listName: selectedListName,
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
        starredByArticleId={starredByArticleId}
        weekStars={weekStars}
        articleIdFromUrl={articleIdFromUrl}
        onToggleStar={toggleStar}
        showStarredOnly={showStarredOnly}
        onShowAll={() => setShowStarredOnly(false)}
        onShowStarredOnly={() => setShowStarredOnly(true)}
        onRunAgain={() => {
          setViewStatus("idle");
          setRunStatus("idle");
          setArticles([]);
          setStarredByArticleId({});
          setWeekStars([]);
          setShowStarredOnly(false);
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

        <div className="w-full max-w-3xl mx-auto mb-6 p-4 border-2 border-[#b8860b] rounded-lg bg-[#faf8f3] text-left">
          <h3 className="text-base font-bold mb-3">Publication Source Lists</h3>

          <label className="block text-sm font-semibold mb-1">New List Name</label>
          <input
            value={newListName}
            onChange={(e) => setNewListName(e.target.value)}
            className="w-full p-2 border border-gray-300 rounded mb-2"
            placeholder="e.g. finance_may"
          />

          <label className="block text-sm font-semibold mb-1">Sources (one per line: base_url, rss_url)</label>
          <textarea
            value={sourceRowsText}
            onChange={(e) => setSourceRowsText(e.target.value)}
            rows={4}
            className="w-full p-2 border border-gray-300 rounded mb-2"
            placeholder={"https://site1.com, https://site1.com/feed\nhttps://site2.com,"}
          />

          <button
            type="button"
            onClick={handleCreateSourceList}
            className="px-4 py-2 bg-[#b8860b] text-black font-semibold rounded"
          >
            Save Source List
          </button>

          <hr className="my-4" />

          <label className="block text-sm font-semibold mb-1">Select Existing List</label>
          <div className="flex flex-col sm:flex-row gap-2">
            <select
              value={selectedListName}
              onChange={(e) => setSelectedListName(e.target.value)}
              className="w-full sm:flex-1 p-2 border border-gray-300 rounded"
            >
              {sourceLists.map((s) => (
                <option key={s.list_name} value={s.list_name}>
                  {s.list_name} ({s.active_rows}/{s.total_rows} active)
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleRunSourceMetadata}
              disabled={!selectedListName || isRunningSourceMetadata}
              className="px-4 py-2 bg-[#b8860b] text-black font-semibold rounded disabled:opacity-60"
            >
              {isRunningSourceMetadata ? "Checking..." : "Run Metadata Check"}
            </button>
          </div>
        </div>

        <PipelineStatusCard runStatus={runStatus} errorMessage={errorMessage} topic={topic} />

        <RunActions
          topic={topic}
          setTopic={setTopic}
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

      {sourceReportSummary && (
        <div className="mb-4 p-4 border rounded-lg bg-white">
          <h3 className="font-bold mb-2">Source Validation Report</h3>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-sm">
            <div>Total: <span className="font-semibold">{sourceReportSummary.total}</span></div>
            <div>Valid sitemap: <span className="font-semibold">{sourceReportSummary.valid_sitemap}</span></div>
            <div>Valid RSS: <span className="font-semibold">{sourceReportSummary.valid_rss}</span></div>
            <div>Both valid: <span className="font-semibold">{sourceReportSummary.both_valid}</span></div>
            <div>Neither valid: <span className="font-semibold">{sourceReportSummary.neither_valid}</span></div>
          </div>
        </div>
      )}

      {sourceReportDetails.length > 0 && (
        <div className="mb-8 overflow-auto border rounded-lg bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-100">
              <tr>
                <th className="p-2 text-left">Publication</th>
                <th className="p-2 text-left">Sitemap</th>
                <th className="p-2 text-left">Sitemap Valid</th>
                <th className="p-2 text-left">RSS Valid</th>
                <th className="p-2 text-left">Active After</th>
              </tr>
            </thead>
            <tbody>
              {sourceReportDetails.map((r, idx) => (
                <tr key={`${r.publication || "pub"}-${idx}`} className="border-t">
                  <td className="p-2">{r.publication}</td>
                  <td className="p-2">{r.sitemap_url}</td>
                  <td className="p-2">{r.sitemap_valid}</td>
                  <td className="p-2">{r.rss_valid}</td>
                  <td className="p-2">{r.active_after}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="bg-gradient-to-br from-[#faf8f3] to-[#f5f1e6] rounded-lg p-4 sm:p-6 border-2 border-[#b8860b] shadow-lg">
        <h3 className="text-lg font-bold text-gray-900 mb-3">How it works</h3>
        <ol className="space-y-2">
          <li className="text-sm text-gray-700">1. Collect articles from 40+ publications</li>
          <li className="text-sm text-gray-700">2. AI summarizes each article</li>
          <li className="text-sm text-gray-700">3. Results saved to Google Sheets and PDF available as GitHub artifact ZIP</li>
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
