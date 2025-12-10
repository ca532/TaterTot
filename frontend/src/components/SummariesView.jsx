import { useState, useEffect } from 'react';
import { Play, Clock, CheckCircle, FileText, Download, AlertCircle } from 'lucide-react';
import LoadingScreen from './LoadingScreen';
import ArticlesList from './ArticlesList';
import googleSheetsAPI from '../services/googleSheetsAPI';
import githubAPI from '../services/githubAPI';
import rateLimitService from '../services/rateLimitService';
import logo from '../assets/ca-circle.png';

function SummariesView() {
  const [pipelineStatus, setPipelineStatus] = useState('idle');
  const [articles, setArticles] = useState([]);
  const [lastRunTime, setLastRunTime] = useState(null);
  const [pdfLink, setPdfLink] = useState(null);
  const [rateLimitInfo, setRateLimitInfo] = useState(null);
  const [pollingInterval, setPollingInterval] = useState(null);

  // Check for existing PDF and last run date on mount
  useEffect(() => {
    checkForPDF();
    loadLastRunDate();
    updateRateLimitInfo();
  }, []);

  // Update rate limit info - more frequently when in cooldown
  useEffect(() => {
    const updateInfo = () => {
      const info = rateLimitService.canRunPipeline();
      setRateLimitInfo(info);
    };

    // Initial update
    updateInfo();

    // Set interval - check every 5 seconds to keep timer accurate
    const interval = setInterval(() => {
      updateInfo();
    }, 5000); // Check every 5 seconds

    return () => clearInterval(interval);
  }, []);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollingInterval) {
        clearInterval(pollingInterval);
      }
    };
  }, [pollingInterval]);

  const updateRateLimitInfo = () => {
    const info = rateLimitService.canRunPipeline();
    setRateLimitInfo(info);
  };

  const loadLastRunDate = async () => {
    try {
      console.log('Loading last run date from Google Sheets...');
      
      // Get all articles to find the most recent one
      const articlesData = await googleSheetsAPI.getArticles();
      
      if (articlesData && articlesData.length > 0) {
        // Find the most recent article date
        const dates = articlesData.map(a => new Date(a.collectedDate));
        const mostRecent = new Date(Math.max(...dates));
        
        setLastRunTime(mostRecent);
        console.log('Last run date loaded:', mostRecent);
      } else {
        console.log('No articles found in sheets');
        setLastRunTime(null);
      }
    } catch (error) {
      console.error('Error loading last run date:', error);
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
          artifactName: `roundup-files-${runInfo.runNumber}`
        });
      }
    } catch (error) {
      console.error('Error checking for PDF:', error);
    }
  };

  const pollForResults = () => {
    console.log('Starting polling after 15 minute wait...');
    
    // Wait 15 minutes before starting to poll
    const initialDelay = setTimeout(() => {
      console.log('15 minutes elapsed, now polling for completion...');
      
      let attempts = 0;
      const maxAttempts = 20; // 20 attempts * 30 seconds = 10 minutes of polling after initial 15 min wait
      
      const interval = setInterval(async () => {
        attempts++;
        console.log(`Polling attempt ${attempts}/${maxAttempts}...`);
        
        try {
          // Check if workflow is still running
          const isRunning = await githubAPI.isWorkflowRunning();
          
          if (!isRunning) {
            // Workflow completed, try to load results
            console.log('Workflow completed, loading results...');
            clearInterval(interval);
            setPollingInterval(null);
            
            // Wait a bit for data to be fully written to sheets
            await new Promise(resolve => setTimeout(resolve, 10000)); // 10 second buffer
            
            await loadResultsAfterPipeline();
            rateLimitService.recordPipelineComplete();
            updateRateLimitInfo();
          } else {
            console.log('Workflow still running...');
          }
          
          // Safety timeout - total max time: 15 min initial + 10 min polling = 25 minutes
          if (attempts >= maxAttempts) {
            console.log('Max polling attempts reached');
            clearInterval(interval);
            setPollingInterval(null);
            
            alert(
              '⏱️ Pipeline is taking longer than expected (25+ minutes).\n\n' +
              'This might be due to:\n' +
              '• High server load\n' +
              '• Network issues\n' +
              '• Publication site blocking\n\n' +
              'Click "View Results" in a few minutes to check manually.'
            );
            
            setPipelineStatus('idle');
            rateLimitService.manualClearRunning();
            updateRateLimitInfo();
          }
        } catch (error) {
          console.error('Error during polling:', error);
          // Continue polling even if there's an error
        }
      }, 30000); // Poll every 30 seconds
      
      setPollingInterval(interval);
      
    }, 900000); // 900000ms = 15 minutes
    
    // Store the initial timeout so we can clear it if needed
    return initialDelay;
  };

  const handleRunPipeline = async () => {
    // Check rate limit first
    const rateLimitCheck = rateLimitService.canRunPipeline();
    
    if (!rateLimitCheck.canRun) {
      alert(`⏱️ Rate Limit\n\n${rateLimitCheck.reason}\n\nThis prevents server overload and ensures quality results.`);
      return;
    }

    // Confirmation popup with rate limit info
    const confirmed = window.confirm(
      '⚠️ Are you sure you want to run the pipeline?\n\n' +
      'This will:\n' +
      '• Collect articles from 40+ publications\n' +
      '• Generate AI summaries\n' +
      '• Save to Google Sheets\n' +
      '• Create a PDF report\n\n' +
      'Process takes 15-20 minutes.\n' +
      `After this run, there will be a ${rateLimitService.COOLDOWN_MINUTES}-minute cooldown period.`
    );

    if (!confirmed) {
      return;
    }

    // Record pipeline start for rate limiting
    rateLimitService.recordPipelineStart();
    updateRateLimitInfo();

    // Show loading screen
    setPipelineStatus('running');

    try {
      // Trigger GitHub Actions workflow
      const result = await githubAPI.triggerPipeline();
      
      if (result.success) {
        console.log('✅ Pipeline triggered successfully on GitHub Actions');
        
        // Start polling for completion (after 15 minute delay)
        pollForResults();
        
      } else {
        // Failed to trigger - manually clear rate limit
        rateLimitService.manualClearRunning();
        updateRateLimitInfo();
        
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
      rateLimitService.manualClearRunning();
      updateRateLimitInfo();
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
        
        const runInfo = await googleSheetsAPI.getLatestRunInfo();
        if (runInfo) {
          setPdfLink({
            runNumber: runInfo.runNumber,
            runUrl: runInfo.runUrl
          });
        } else {
          setPdfLink({ available: true });
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

  const handleDownloadPDF = async () => {
    console.log('Starting PDF download...');
    
    try {
      const artifactInfo = await githubAPI.getLatestArtifactDownloadURL();
      
      if (!artifactInfo || !artifactInfo.downloadURL) {
        alert('No PDF available yet. Run the pipeline first to generate a PDF.');
        return;
      }
      
      console.log('Artifact found:', artifactInfo);
      
      const token = import.meta.env.VITE_GITHUB_TOKEN;
      
      if (token) {
        try {
          const response = await fetch(artifactInfo.downloadURL, {
            headers: {
              'Accept': 'application/vnd.github+json',
              'Authorization': `Bearer ${token}`,
              'X-GitHub-Api-Version': '2022-11-28'
            }
          });
          
          if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${artifactInfo.name}.zip`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            
            alert('✅ Download started!\n\nThe file is a ZIP archive.\nExtract it to access the PDF.');
          } else {
            throw new Error(`Download failed: ${response.status}`);
          }
        } catch (fetchError) {
          console.error('Direct download failed:', fetchError);
          openGitHubArtifactPage(artifactInfo);
        }
      } else {
        openGitHubArtifactPage(artifactInfo);
      }
      
    } catch (error) {
      console.error('Error downloading PDF:', error);
      alert('Failed to download PDF. Opening GitHub Actions page instead.');
      
      const githubOwner = import.meta.env.VITE_GITHUB_OWNER;
      const githubRepo = import.meta.env.VITE_GITHUB_REPO;
      window.open(
        `https://github.com/${githubOwner}/${githubRepo}/actions`,
        '_blank'
      );
    }
  };

  const openGitHubArtifactPage = (artifactInfo) => {
    alert(
      '📄 PDF Download:\n\n' +
      'Opening GitHub to download the artifact.\n\n' +
      `Artifact: ${artifactInfo.name}\n` +
      'Click the artifact to download, then extract the ZIP to get your PDF.'
    );
    
    const githubOwner = import.meta.env.VITE_GITHUB_OWNER;
    const githubRepo = import.meta.env.VITE_GITHUB_REPO;
    window.open(
      `https://github.com/${githubOwner}/${githubRepo}/actions`,
      '_blank'
    );
  };

  // LANDING PAGE
  if (pipelineStatus === 'idle') {
    return (
      <div className="max-w-7xl mx-auto">
        {/* Rate Limit Warning Banner */}
        {rateLimitInfo && !rateLimitInfo.canRun && (
          <div className="mb-6 p-4 bg-yellow-50 border-2 border-yellow-400 rounded-lg flex items-start gap-3">
            <AlertCircle className="w-6 h-6 text-yellow-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-yellow-900 mb-1">⏱️ Cooldown Period Active</p>
              <p className="text-sm text-yellow-800">{rateLimitInfo.reason}</p>
              {rateLimitInfo.nextAvailableTime && (
                <p className="text-xs text-yellow-700 mt-1">
                  Next available: {rateLimitInfo.nextAvailableTime.toLocaleTimeString()}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Hero Section */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-20 h-20 mb-4">
            <img 
              src={logo} 
              alt="CA Logo" 
              className="w-20 h-20"
            />
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
              disabled={rateLimitInfo && !rateLimitInfo.canRun}
              className="inline-flex items-center gap-3 px-8 py-3 bg-[#b8860b] text-black font-bold rounded-lg hover:bg-[#8b6914] transform hover:scale-105 transition-all duration-200 shadow-xl hover:shadow-2xl disabled:bg-gray-300 disabled:cursor-not-allowed disabled:transform-none"
              title={rateLimitInfo && !rateLimitInfo.canRun ? rateLimitInfo.reason : 'Run pipeline'}
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

          {/* Cooldown Timer */}
          {rateLimitInfo && !rateLimitInfo.canRun && (
            <p className="mt-3 text-sm text-gray-600">
              ⏳ Time remaining: <span className="font-bold text-[#b8860b]">{rateLimitService.formatRemainingTime()}</span>
            </p>
          )}
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
            <div className="flex items-center gap-2 mb-1">
              <FileText className="w-4 h-4 text-[#b8860b]" />
              <span className="text-xs font-semibold text-gray-600">Publications</span>
            </div>
            <p className="text-xl font-bold text-gray-900">40</p>
          </div>

          <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
            <div className="flex items-center gap-2 mb-1">
              <Clock className="w-4 h-4 text-[#b8860b]" />
              <span className="text-xs font-semibold text-gray-600">Avg. Runtime</span>
            </div>
            <p className="text-xl font-bold text-gray-900">15-20 min</p>
          </div>

          <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
            <div className="flex items-center gap-2 mb-1">
              <CheckCircle className="w-4 h-4 text-[#b8860b]" />
              <span className="text-xs font-semibold text-gray-600">Last Run</span>
            </div>
            <p className="text-xl font-bold text-gray-900">
              {lastRunTime ? lastRunTime.toLocaleDateString() : 'Loading...'}
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
          
          {/* Rate Limit Info */}
          <div className="mt-4 pt-4 border-t-2 border-[#b8860b]">
            <p className="text-xs text-gray-600 flex items-center gap-2">
              <Clock className="w-3 h-3" />
              <span>Rate limit: Maximum one run every {rateLimitService.COOLDOWN_MINUTES} minutes to ensure quality</span>
            </p>
          </div>
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
          updateRateLimitInfo();
        }}
        lastRunTime={lastRunTime}
        onDownloadPDF={handleDownloadPDF}
        hasPDF={!!pdfLink}
      />
    );
  }
}

export default SummariesView;