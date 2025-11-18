/**
 * GitHub API Service
 * Triggers workflows and fetches artifacts
 */

const GITHUB_OWNER = import.meta.env.VITE_GITHUB_OWNER;
const GITHUB_REPO = import.meta.env.VITE_GITHUB_REPO;
const GITHUB_TOKEN = import.meta.env.VITE_GITHUB_TOKEN;

class GitHubService {
  constructor() {
    this.baseURL = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}`;
    
    if (!GITHUB_OWNER || !GITHUB_REPO) {
      console.warn('⚠️ GitHub credentials not configured');
    }
  }

  /**
 * Trigger the article collection workflow
 */
async triggerPipeline() {
  if (!GITHUB_TOKEN) {
    console.error('GitHub token not configured');
    return { success: false, error: 'No GitHub token configured' };
  }

  if (!GITHUB_OWNER || !GITHUB_REPO) {
    console.error('GitHub owner/repo not configured');
    return { success: false, error: 'GitHub owner/repo not configured' };
  }

  // Use workflow ID instead of filename (more reliable)
  const url = `${this.baseURL}/actions/workflows/collect-articles.yml/dispatches`;
  
  console.log('Triggering workflow:', url);
  
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Accept': 'application/vnd.github+json',
        'Authorization': `Bearer ${GITHUB_TOKEN}`,
        'Content-Type': 'application/json',
        'X-GitHub-Api-Version': '2022-11-28'
      },
      body: JSON.stringify({
        ref: 'master',  // Your default branch
        inputs: {
          test_mode: 'false'
        }
      })
    });
    
    console.log('Response status:', response.status);
    
    if (response.status === 204) {
      console.log('✅ Workflow triggered successfully');
      return { success: true };
    } else {
      const errorText = await response.text();
      console.error('GitHub API Error:', response.status, errorText);
      
      let errorMessage = `HTTP ${response.status}`;
      
      try {
        const errorJson = JSON.parse(errorText);
        errorMessage = errorJson.message || errorMessage;
      } catch (e) {
        // Not JSON
      }
      
      return { success: false, error: errorMessage };
    }
    
  } catch (error) {
    console.error('Error triggering workflow:', error);
    return { success: false, error: error.message };
  }
}

  /**
   * Get latest workflow runs
   */
  async getWorkflowRuns(limit = 5) {
    const url = `${this.baseURL}/actions/runs?per_page=${limit}`;
    
    const headers = {
      'Accept': 'application/vnd.github+json',
    };
    
    if (GITHUB_TOKEN) {
      headers['Authorization'] = `Bearer ${GITHUB_TOKEN}`;
    }
    
    try {
      const response = await fetch(url, { headers });
      const data = await response.json();
      
      return data.workflow_runs || [];
    } catch (error) {
      console.error('Error fetching workflow runs:', error);
      return [];
    }
  }

  /**
   * Check if a workflow is currently running
   */
  async isWorkflowRunning() {
    const runs = await this.getWorkflowRuns(1);
    
    if (runs.length > 0) {
      const latestRun = runs[0];
      return latestRun.status === 'in_progress' || latestRun.status === 'queued';
    }
    
    return false;
  }

  /**
   * Get the status of the latest workflow run
   */
  async getLatestRunStatus() {
    const runs = await this.getWorkflowRuns(1);
    
    if (runs.length > 0) {
      const latestRun = runs[0];
      return {
        status: latestRun.status,        // queued, in_progress, completed
        conclusion: latestRun.conclusion, // success, failure, cancelled
        createdAt: latestRun.created_at,
        updatedAt: latestRun.updated_at
      };
    }
    
    return null;
  }
    /**
   * Get direct download link for latest artifact
   */
  async getLatestArtifactDownloadURL() {
    try {
      const runs = await this.getWorkflowRuns(5);
      
      // Find latest successful run
      const successfulRun = runs.find(run => 
        run.conclusion === 'success' &&
        run.name === 'Collect & Summarize Articles'
      );
      
      if (!successfulRun) {
        console.log('No successful runs found');
        return null;
      }
      
      // Get artifacts for this run
      const artifactsURL = `${this.baseURL}/actions/runs/${successfulRun.id}/artifacts`;
      
      const headers = {
        'Accept': 'application/vnd.github+json',
      };
      
      if (GITHUB_TOKEN) {
        headers['Authorization'] = `Bearer ${GITHUB_TOKEN}`;
      }
      
      const response = await fetch(artifactsURL, { headers });
      const data = await response.json();
      
      // Find roundup artifact
      const artifact = data.artifacts?.find(a => a.name.startsWith('roundup-files-'));
      
      if (artifact) {
        return {
          downloadURL: artifact.archive_download_url,
          name: artifact.name,
          runNumber: successfulRun.run_number,
          createdAt: artifact.created_at
        };
      }
      
      return null;
      
    } catch (error) {
      console.error('Error getting artifact:', error);
      return null;
    }
  }
}

export default new GitHubService();