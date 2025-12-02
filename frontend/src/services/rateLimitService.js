/**
 * Rate Limiting Service
 * Prevents multiple simultaneous pipeline runs and enforces cooldown periods
 */

class RateLimitService {
  constructor() {
    this.STORAGE_KEY = 'pipeline_rate_limit';
    this.COOLDOWN_MINUTES = 15; // Minimum time between runs
  }

  /**
   * Check if pipeline can be run
   * @returns {Object} { canRun: boolean, reason: string, nextAvailableTime: Date }
   */
  canRunPipeline() {
    const lastRunData = this.getLastRunData();
    
    if (!lastRunData) {
      return { canRun: true, reason: null, nextAvailableTime: null };
    }

    const { timestamp, status } = lastRunData;
    const lastRunTime = new Date(timestamp);
    const now = new Date();
    const minutesSinceLastRun = (now - lastRunTime) / (1000 * 60);

    // Check if a run is currently in progress
    if (status === 'running') {
      const runningFor = Math.floor(minutesSinceLastRun);
      
      return {
        canRun: false,
        reason: `Pipeline is currently running (${runningFor} min). Please wait for it to complete.`,
        nextAvailableTime: null
      };
    }

    // Check cooldown period
    if (minutesSinceLastRun < this.COOLDOWN_MINUTES) {
      const remainingMinutes = Math.ceil(this.COOLDOWN_MINUTES - minutesSinceLastRun);
      const nextAvailable = new Date(lastRunTime.getTime() + this.COOLDOWN_MINUTES * 60 * 1000);
      
      return {
        canRun: false,
        reason: `Please wait ${remainingMinutes} more minute(s) before running again. Last run was ${Math.floor(minutesSinceLastRun)} minutes ago.`,
        nextAvailableTime: nextAvailable
      };
    }

    return { canRun: true, reason: null, nextAvailableTime: null };
  }

  /**
   * Record that pipeline has started
   */
  recordPipelineStart() {
    const data = {
      timestamp: new Date().toISOString(),
      status: 'running'
    };
    localStorage.setItem(this.STORAGE_KEY, JSON.stringify(data));
  }

  /**
   * Record that pipeline has completed
   */
  recordPipelineComplete() {
    const data = {
      timestamp: new Date().toISOString(),
      status: 'completed'
    };
    localStorage.setItem(this.STORAGE_KEY, JSON.stringify(data));
  }

  /**
   * Clear running status (used when detecting stuck runs)
   */
  clearRunningStatus() {
    const data = {
      timestamp: new Date().toISOString(),
      status: 'cleared'
    };
    localStorage.setItem(this.STORAGE_KEY, JSON.stringify(data));
  }

  /**
   * Get last run data from storage
   */
  getLastRunData() {
    try {
      const data = localStorage.getItem(this.STORAGE_KEY);
      return data ? JSON.parse(data) : null;
    } catch (error) {
      console.error('Error reading rate limit data:', error);
      return null;
    }
  }

  /**
   * Get time until next available run
   */
  getTimeUntilNextRun() {
    const { canRun, nextAvailableTime } = this.canRunPipeline();
    
    if (canRun) {
      return 0;
    }

    if (!nextAvailableTime) {
      return 0;
    }

    const now = new Date();
    const milliseconds = nextAvailableTime - now;
    return Math.max(0, Math.ceil(milliseconds / (1000 * 60))); // Return minutes
  }

  /**
   * Format remaining time for display
   */
  formatRemainingTime() {
    const minutes = this.getTimeUntilNextRun();
    
    if (minutes === 0) {
      return 'Available now';
    }

    if (minutes === 1) {
      return '1 minute';
    }

    return `${minutes} minutes`;
  }

  /**
   * Reset rate limit (admin only)
   */
  reset() {
    localStorage.removeItem(this.STORAGE_KEY);
    console.log('Rate limit reset');
  }
}

export default new RateLimitService();