import { useState, useEffect } from 'react';
import { Play, Clock, CheckCircle, FileText, Download } from 'lucide-react';
import LoadingScreen from './LoadingScreen';
import ArticlesList from './ArticlesList';
import googleSheetsAPI from '../services/googleSheetsAPI';
import githubAPI from '../services/githubAPI';

function SummariesView() {
  const [pipelineStatus, setPipelineStatus] = useState('idle');
  const [articles, setArticles] = useState([]);
  const [lastRunTime, setLastRunTime] = useState(null);
  const [pdfLink, setPdfLink] = useState(null);

  // Check for existing PDF on mount
  useEffect(() => {
    checkForPDF();
  }, []);

  const checkForPDF = async () => {
  try {
    // Get latest run info from Sheets
    const runInfo = await googleSheetsAPI.getLatestRunInfo();
    if (runInfo) {
      setPdfLink({
        runNumber: runInfo.runNumber,
        runUrl: runInfo.runUrl,
        artifactName: `roundup-files-${runInfo.runNumber}`
      });
    }
  } catch (error) {
    console.error('Error checking for PDF:', error);
  }
};

  const handleRunPipeline = async () => {
    // Confirmation popup
    const confirmed = window.confirm(
      '⚠️ Are you sure you want to run the pipeline?\n\n' +
      'This will:\n' +
      '• Collect articles from 40+ publications\n' +
      '• Generate AI summaries\n' +
      '• Save to Google Sheets\n' +
      '• Create a PDF report\n\n' +
      'This process takes 3-7 minutes.'
    );

    if (!confirmed) {
      return;
    }

    // Show loading screen
    setPipelineStatus('running');

    try {
      // Trigger GitHub Actions workflow
      const result = await githubAPI.triggerPipeline();
      
      if (result.success) {
        console.log('✅ Pipeline triggered successfully on GitHub Actions');
        
        // After 6 minutes, auto-load results
        setTimeout(async () => {
          await loadResultsAfterPipeline();
        }, 360000); // 6 minutes
        
      } else {
        setPipelineStatus('idle');
        alert(
          '❌ Failed to trigger pipeline automatically.\n\n' +
          `Error: ${result.error}\n\n` +
          'Opening GitHub Actions for manual trigger...'
        );
        
        const githubOwner = import.meta.env.VITE_GITHUB_OWNER;
        const githubRepo = import.meta.env.VITE_GITHUB_REPO;
        window.open(
          `https://github.com/${githubOwner}/${githubRepo}/actions`,
          '_blank'
        );
      }
    } catch (error) {
      console.error('Error:', error);
      setPipelineStatus('idle');
      alert('Error triggering pipeline. Please try again.');
    }
  };

  const loadResultsAfterPipeline = async () => {
    try {
      const articlesData = await googleSheetsAPI.getArticles();
      
      if (articlesData.length > 0) {
        setArticles(articlesData);
        
        const dates = articlesData.map(a => new Date(a.collectedDate));
        const mostRecent = new Date(Math.max(...dates));
        setLastRunTime(mostRecent);
        
        const pdfInfo = await googleSheetsAPI.getLatestPDFLink();
        if (pdfInfo) {
          setPdfLink(pdfInfo);
        }
        
        setPipelineStatus('complete');
      } else {
        alert('No new articles yet. Pipeline may still be running.\n\nClick "View Results" to check again.');
        setPipelineStatus('idle');
      }
    } catch (error) {
      console.error('Error loading results:', error);
      setPipelineStatus('idle');
    }
  };

  const handleViewResults = async () => {
    const checking = window.confirm(
      'Load latest results from Google Sheets?\n\n' +
      'This will display all articles currently in the database.'
    );
    
    if (!checking) return;
    
    setPipelineStatus('running');
    await loadResultsAfterPipeline();
  };

  const handleDownloadPDF = () => {
  if (pdfLink && pdfLink.runUrl) {
    // Open the GitHub Actions run page where user can download artifacts
    alert(
      '📄 PDF Download Instructions:\n\n' +
      '1. A new tab will open with the workflow run\n' +
      '2. Scroll down to "Artifacts" section\n' +
      '3. Click on "roundup-files-' + pdfLink.runNumber + '" to download\n' +
      '4. Unzip the file to access the PDF'
    );
    window.open(pdfLink.runUrl, '_blank');
  } else {
    alert('No PDF available yet. Run the pipeline first to generate a PDF.');
  }
};

  // LANDING PAGE
  if (pipelineStatus === 'idle') {
    return (
      <div className="max-w-7xl mx-auto">
        {/* Hero Section */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[#faf8f3] mb-4 border-2 border-[#b8860b]">
            <img src="/logo.svg" alt="TaterTot Logo" className="w-10 h-10" />
          </div>
          <h2 className="text-2xl font-bold text-gray-900 mb-2">
            Article Pipeline Ready
          </h2>
          <p className="text-base text-gray-600 mb-6">
            Click the button below to collect and summarize the latest articles from your publications.
          </p>
          
          {/* Action Buttons */}
          <div className="flex items-center justify-center gap-3 flex-wrap">
            <button
              onClick={handleRunPipeline}
              className="inline-flex items-center gap-3 px-8 py-3 bg-[#b8860b] text-black font-bold rounded-lg hover:bg-[#8b6914] transform hover:scale-105 transition-all duration-200 shadow-xl hover:shadow-2xl"
            >
              <Play className="w-5 h-5" />
              <span className="text-base">Run Pipeline</span>
            </button>

            <button
              onClick={handleViewResults}
              className="inline-flex items-center gap-2 px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] hover:bg-[#faf8f3] transition-all shadow-md hover:shadow-lg"
            >
              <FileText className="w-5 h-5" />
              <span>View Results</span>
            </button>

            {pdfLink && (
              <button
                onClick={handleDownloadPDF}
                className="inline-flex items-center gap-2 px-6 py-3 bg-white text-[#b8860b] font-semibold rounded-lg border-2 border-[#b8860b] hover:bg-[#faf8f3] transition-all shadow-md hover:shadow-lg"
              >
                <Download className="w-5 h-5" />
                <span>Download PDF</span>
              </button>
            )}
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
            <div className="flex items-center gap-2 mb-1">
              <img src="/logo.svg" alt="" className="w-4 h-4" />
              <span className="text-xs font-semibold text-gray-600">Publications</span>
            </div>
            <p className="text-xl font-bold text-gray-900">40</p>
          </div>

          <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
            <div className="flex items-center gap-2 mb-1">
              <Clock className="w-4 h-4 text-[#b8860b]" />
              <span className="text-xs font-semibold text-gray-600">Avg. Runtime</span>
            </div>
            <p className="text-xl font-bold text-gray-900">~3 min</p>
          </div>

          <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
            <div className="flex items-center gap-2 mb-1">
              <CheckCircle className="w-4 h-4 text-[#b8860b]" />
              <span className="text-xs font-semibold text-gray-600">Last Run</span>
            </div>
            <p className="text-xl font-bold text-gray-900">
              {lastRunTime ? lastRunTime.toLocaleDateString() : 'Never'}
            </p>
          </div>
        </div>

        {/* How it Works */}
        <div className="bg-gradient-to-br from-[#faf8f3] to-[#f5f1e6] rounded-lg p-6 border-2 border-[#b8860b] shadow-lg">
          <h3 className="text-lg font-bold text-gray-900 mb-3">
            How it works
          </h3>
          <ol className="space-y-2">
            <li className="flex items-start gap-2">
              <span className="flex-shrink-0 w-5 h-5 rounded-full bg-[#b8860b] text-white text-xs flex items-center justify-center font-bold">1</span>
              <span className="text-sm text-gray-700">Collect articles from 40+ publications</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="flex-shrink-0 w-5 h-5 rounded-full bg-[#b8860b] text-white text-xs flex items-center justify-center font-bold">2</span>
              <span className="text-sm text-gray-700">AI summarizes each article focusing on key details</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="flex-shrink-0 w-5 h-5 rounded-full bg-[#b8860b] text-white text-xs flex items-center justify-center font-bold">3</span>
              <span className="text-sm text-gray-700">Results saved to Google Sheets and PDF uploaded to Drive</span>
            </li>
          </ol>
        </div>
      </div>
    );
  }

  // LOADING SCREEN
  if (pipelineStatus === 'running') {
    return <LoadingScreen />;
  }

  // RESULTS PAGE
  if (pipelineStatus === 'complete') {
    return (
      <ArticlesList 
        articles={articles} 
        onRunAgain={() => {
          setPipelineStatus('idle');
          setArticles([]);
        }}
        lastRunTime={lastRunTime}
        onDownloadPDF={handleDownloadPDF}
        hasPDF={!!pdfLink}
      />
    );
  }
}

export default SummariesView;