import { RefreshCw, ExternalLink, User, Calendar, Download } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

function ArticlesList({ articles, onRunAgain, lastRunTime, onDownloadPDF, hasPDF }) {
  // Filter articles from the last week
  const oneWeekAgo = new Date();
  oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);
  
  // Filter by last week
  const lastWeekArticles = articles.filter(article => {
    const articleDate = new Date(article.collectedDate);
    return articleDate >= oneWeekAgo;
  });

  // If we have lastRunTime, also filter by that to get only articles from the most recent run
  let recentArticles = lastWeekArticles;
  
  if (lastRunTime) {
    // Get articles from the last run (within 1 hour of lastRunTime to account for pipeline duration)
    const runTimeThreshold = new Date(lastRunTime);
    runTimeThreshold.setHours(runTimeThreshold.getHours() - 1);
    
    const lastRunArticles = lastWeekArticles.filter(article => {
      const articleDate = new Date(article.collectedDate);
      return articleDate >= runTimeThreshold;
    });
    
    // Use last run articles if we have any, otherwise fall back to last week
    if (lastRunArticles.length > 0) {
      recentArticles = lastRunArticles;
    }
  }

  return (
    <div className="w-full h-full flex flex-col">
      {/* Header with Home button */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-6 gap-4">
        <div>
          <h2 className="text-3xl font-bold text-gray-900">
            Article Summaries
          </h2>
          <p className="text-base text-gray-600 mt-2">
            {recentArticles.length} articles {lastRunTime ? 'from latest run' : 'from the last week'} {lastRunTime && `• ${formatDistanceToNow(lastRunTime, { addSuffix: true })}`}
          </p>
        </div>

        <div className="flex gap-3">
          {hasPDF && (
            <button
              onClick={onDownloadPDF}
              className="inline-flex items-center gap-2 px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] hover:bg-[#faf8f3] transition-all shadow-md hover:shadow-lg"
            >
              <Download className="w-5 h-5" />
              Download PDF
            </button>
          )}
          
          <button
            onClick={onRunAgain}
            className="inline-flex items-center gap-2 px-6 py-3 bg-[#b8860b] text-black font-semibold rounded-lg hover:bg-[#8b6914] transition-colors shadow-md hover:shadow-lg"
          >
            <RefreshCw className="w-5 h-5" />
            Home
          </button>
        </div>
      </div>

      {/* Success Banner */}
      <div className="mb-8 p-5 bg-[#faf8f3] border-2 border-[#b8860b] rounded-lg flex items-center gap-4">
        <div className="flex-shrink-0">
          <div className="w-10 h-10 rounded-full bg-[#b8860b] flex items-center justify-center">
            <span className="text-white text-xl font-bold">✓</span>
          </div>
        </div>
        <div>
          <p className="font-bold text-lg text-gray-900">Pipeline completed successfully!</p>
          <p className="text-base text-gray-700">
            {lastRunTime 
              ? 'Showing articles from the most recent run.' 
              : 'Showing articles from the last week.'}
          </p>
        </div>
      </div>

      {/* No articles message */}
      {recentArticles.length === 0 && (
        <div className="text-center py-12">
          <p className="text-gray-600 text-lg">No articles found from the last week.</p>
          <p className="text-gray-500 mt-2">Try running the pipeline to collect new articles.</p>
        </div>
      )}

      {/* Articles Grid - 3 columns on large screens, EDGE TO EDGE */}
      {recentArticles.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
          {recentArticles.map(article => (
            <div 
              key={article.id}
              className="bg-white rounded-lg shadow-lg border border-gray-200 p-6 hover:shadow-xl hover:border-[#b8860b] transition-all flex flex-col h-full"
            >
              {/* Article Header */}
              <div className="mb-4">
                {/* Full title - no truncation */}
                <h3 className="text-lg font-semibold text-gray-900 mb-3">
                  {article.title}
                </h3>
                
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