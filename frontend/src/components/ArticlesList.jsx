import { RefreshCw, ExternalLink, User, Calendar, Download, Star } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

function ArticlesList({
  articles,
  onRunAgain,
  lastRunTime,
  onDownloadPDF,
  hasPDF,
  starredByArticleId = {},
  articleIdFromUrl = (u) => u,
  onToggleStar = () => {},
  showStarredOnly = false,
  onShowAll = () => {},
  onShowStarredOnly = () => {},
}) {
  // Filter articles from the last run only
  const filtered = articles.filter(article => {
    if (!lastRunTime) return true; // If no lastRunTime, show all articles
    
    const articleDate = new Date(article.collectedDate);
    const runDate = new Date(lastRunTime);
    
    // Show articles that were collected during or after the last run
    // Allow a small buffer (5 minutes) to account for pipeline duration
    const bufferTime = 5 * 60 * 1000; // 5 minutes in milliseconds
    return articleDate >= new Date(runDate.getTime() - bufferTime);
  });

  filtered.sort((a, b) => {
    const scoreA = Number(a.score || 0);
    const scoreB = Number(b.score || 0);
    if (scoreB !== scoreA) return scoreB - scoreA; // higher score first
    return new Date(b.collectedDate) - new Date(a.collectedDate); // tie-breaker: newer first
  });

  const recentArticles = filtered;
  const starredSet = new Set(Object.keys(starredByArticleId || {}));
  const starredRecentArticles = recentArticles.filter((article) =>
    starredSet.has(articleIdFromUrl(article.url))
  );
  const displayedArticles = showStarredOnly ? starredRecentArticles : recentArticles;

  // Also calculate one week ago for additional context
  const oneWeekAgo = new Date();
  oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);
  
  const articlesFromLastWeek = articles.filter(article => {
    const articleDate = new Date(article.collectedDate);
    return articleDate >= oneWeekAgo;
  });

  return (
    <div className="w-full h-full flex flex-col px-4 sm:px-6">
      {/* Header with Home button */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-6 gap-4">
        <div>
          <h2 className="text-3xl font-bold text-gray-900">
            Article Summaries
          </h2>
          <p className="text-base text-gray-600 mt-2">
            {recentArticles.length} articles from latest run {lastRunTime && `• ${formatDistanceToNow(lastRunTime, { addSuffix: true })}`}
          </p>
          {articlesFromLastWeek.length !== recentArticles.length && (
            <p className="text-sm text-gray-500 mt-1">
              ({articlesFromLastWeek.length} total from the last week)
            </p>
          )}
        </div>

        <div className="flex flex-col sm:flex-row gap-3 w-full sm:w-auto">
          {hasPDF && (
            <button
              onClick={onDownloadPDF}
              className="inline-flex items-center justify-center gap-2 w-full sm:w-auto px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] hover:bg-[#faf8f3] transition-all shadow-md hover:shadow-lg"
            >
              <Download className="w-5 h-5" />
              Download PDF
            </button>
          )}

          <button
            onClick={onShowAll}
            className={`inline-flex items-center justify-center gap-2 w-full sm:w-auto px-4 py-2 rounded-lg border ${!showStarredOnly ? "bg-[#b8860b] text-black border-[#b8860b]" : "bg-white text-gray-700 border-gray-300"}`}
          >
            All This Run
          </button>

          <button
            onClick={onShowStarredOnly}
            className={`inline-flex items-center justify-center gap-2 w-full sm:w-auto px-4 py-2 rounded-lg border ${showStarredOnly ? "bg-[#b8860b] text-black border-[#b8860b]" : "bg-white text-gray-700 border-gray-300"}`}
          >
            Starred This Week ({starredRecentArticles.length})
          </button>
          
          <button
            onClick={onRunAgain}
            className="inline-flex items-center justify-center gap-2 w-full sm:w-auto px-6 py-3 bg-[#b8860b] text-black font-semibold rounded-lg hover:bg-[#8b6914] transition-colors shadow-md hover:shadow-lg"
          >
            <RefreshCw className="w-5 h-5" />
            Home
          </button>
        </div>
      </div>

      {/* Success Banner */}
      <div className="mb-8 p-4 sm:p-5 bg-[#faf8f3] border-2 border-[#b8860b] rounded-lg flex items-start sm:items-center gap-3 sm:gap-4">
        <div className="flex-shrink-0">
          <div className="w-10 h-10 rounded-full bg-[#b8860b] flex items-center justify-center">
            <span className="text-white text-xl font-bold">✓</span>
          </div>
        </div>
        <div>
          <p className="font-bold text-lg text-gray-900">Pipeline completed successfully!</p>
          <p className="text-base text-gray-700">
            Showing articles from the latest run{lastRunTime && ` (${lastRunTime.toLocaleString()})`}.
          </p>
        </div>
      </div>

      {/* No articles message */}
      {displayedArticles.length === 0 && (
        <div className="text-center py-12">
          <p className="text-gray-600 text-lg">
            {showStarredOnly ? "No starred summaries for this week yet." : "No articles found from the latest run."}
          </p>
          <p className="text-gray-500 mt-2">
            {showStarredOnly ? "Star summaries to pin them for the week." : "Try running the pipeline to collect new articles."}
          </p>
        </div>
      )}

      {/* Articles Grid */}
      {displayedArticles.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
          {displayedArticles.map(article => (
            <div 
              key={article.id}
              className="bg-white rounded-lg shadow-lg border border-gray-200 p-4 sm:p-6 hover:shadow-xl hover:border-[#b8860b] transition-all flex flex-col h-full"
            >
              {/* Article Header */}
              <div className="mb-4">
                {/* Full title - no truncation */}
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-lg font-semibold text-gray-900 mb-3">
                    {article.title}
                  </h3>
                  <button
                    type="button"
                    onClick={() => onToggleStar(article)}
                    className="shrink-0 p-1 rounded hover:bg-gray-100"
                    aria-label="Toggle star"
                    title="Star this summary"
                  >
                    {(() => {
                      const aid = articleIdFromUrl(article.url);
                      const isStarred = !!starredByArticleId[aid];
                      return (
                        <Star
                          className={`w-5 h-5 ${isStarred ? "fill-[#b8860b] text-[#b8860b]" : "text-gray-400"}`}
                        />
                      );
                    })()}
                  </button>
                </div>
                
                {/* Meta info */}
                <div className="flex flex-wrap items-center gap-3 text-sm text-gray-600">
                  <span className="font-semibold text-[#b8860b]">
                    {article.publication}
                  </span>
                  <span className="flex items-center gap-1">
                    <User className="w-4 h-4" />
                    {article.journalist}
                  </span>
                  <span className="flex items-center gap-1">
                    <Calendar className="w-4 h-4" />
                    {new Date(article.collectedDate).toLocaleDateString()}
                  </span>
                </div>
              </div>

              {/* Full Summary - no truncation */}
              <p className="text-gray-700 mb-4 leading-relaxed flex-grow">
                {article.summary}
              </p>

              {/* Read More Link */}
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-[#b8860b] hover:text-[#8b6914] font-semibold text-sm mt-auto"
              >
                Read full article
                <ExternalLink className="w-4 h-4" />
              </a>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default ArticlesList;
