/**
 * Rate Limit Service
 * Manages pipeline execution rate limiting with cooldown periods
 */

class RateLimitService {
  constructor() {
    this.COOLDOWN_MINUTES = 30; // 30 minute cooldown between runs
    this.STORAGE_KEY = 'pipeline_rate_limit';
  }

  /**
   * Get current rate limit state from localStorage
   */
  getState() {
    const stored = localStorage.getItem(this.STORAGE_KEY);
    if (!stored) return null;
    
    try {
      return JSON.parse(stored);
    } catch (error) {
      console.error('Error parsing rate limit state:', error);
      return null;
    }
  }

  /**
   * Save rate limit state to localStorage
   */
  saveState(state) {
    try {
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(state));
    } catch (error) {
      console.error('Error saving rate limit state:', error);
    }
  }

  /**
   * Record that a pipeline run has started
   */
  recordPipelineStart() {
    const now = Date.now();
    this.saveState({
      lastRunStart: now,
      isRunning: true,
      lastRunComplete: null
    });
    console.log('Pipeline start recorded at', new Date(now));
  }

  /**
   * Record that a pipeline run has completed
   */
  recordPipelineComplete() {
    const state = this.getState();
    if (!state) return;

    const now = Date.now();
    this.saveState({
      lastRunStart: state.lastRunStart,
      isRunning: false,
      lastRunComplete: now
    });
    console.log('Pipeline completion recorded at', new Date(now));
  }

  /**
   * Check if pipeline can be run (not in cooldown)
   */
  canRunPipeline() {
    const state = this.getState();
    
    // No previous runs - can run
    if (!state) {
      return {
        canRun: true,
        reason: 'No previous runs',
        nextAvailableTime: null
      };
    }

    const now = Date.now();

    // Check if currently running
    if (state.isRunning) {
      return {
        canRun: false,
        reason: 'Pipeline is currently running',
        nextAvailableTime: null
      };
    }

    // Check cooldown period
    const lastRelevantTime = state.lastRunComplete || state.lastRunStart;
    if (!lastRelevantTime) {
      return {
        canRun: true,
        reason: 'No valid previous run time',
        nextAvailableTime: null
      };
    }

    const cooldownMs = this.COOLDOWN_MINUTES * 60 * 1000;
    const timeSinceLastRun = now - lastRelevantTime;
    const timeRemaining = cooldownMs - timeSinceLastRun;

    if (timeRemaining > 0) {
      // Still in cooldown
      const nextAvailable = new Date(lastRelevantTime + cooldownMs);
      const minutesRemaining = Math.ceil(timeRemaining / 60000);
      
      return {
        canRun: false,
        reason: `Please wait ${minutesRemaining} more minute${minutesRemaining !== 1 ? 's' : ''} before running again`,
        nextAvailableTime: nextAvailable,
        timeRemainingMs: timeRemaining
      };
    }

    // Cooldown period has passed
    return {
      canRun: true,
      reason: 'Cooldown period completed',
      nextAvailableTime: null
    };
  }

  /**
   * Format remaining time as human-readable string
   */
  formatRemainingTime() {
    const check = this.canRunPipeline();
    
    if (check.canRun) {
      return 'Available now';
    }

    if (!check.timeRemainingMs) {
      if (check.reason === 'Pipeline is currently running') {
        return 'Running...';
      }
      return 'Calculating...';
    }

    const minutes = Math.floor(check.timeRemainingMs / 60000);
    const seconds = Math.floor((check.timeRemainingMs % 60000) / 1000);

    if (minutes > 0) {
      return `${minutes}m ${seconds}s`;
    } else {
      return `${seconds}s`;
    }
  }

  /**
   * Manually clear the running state (for error recovery)
   */
  manualClearRunning() {
    const state = this.getState();
    if (state && state.isRunning) {
      this.saveState({
        ...state,
        isRunning: false,
        lastRunComplete: Date.now()
      });
      console.log('Manually cleared running state');
    }
  }

  /**
   * Sync local rate-limit state from backend /pipeline/status response
   */
  syncFromBackendStatus(backendStatus) {
    if (!backendStatus) return;

    const status = backendStatus.status;
    const lastTriggeredAtSec = backendStatus.lastTriggeredAt;
    const lastTriggeredAtMs = lastTriggeredAtSec ? Number(lastTriggeredAtSec) * 1000 : null;
    const current = this.getState() || {};

    if (status === 'queued' || status === 'running' || status === 'in_progress') {
      this.saveState({
        lastRunStart: lastTriggeredAtMs || current.lastRunStart || Date.now(),
        isRunning: true,
        lastRunComplete: null
      });
      return;
    }

    // For completed/idle states, clear running without resetting cooldown anchor every poll
    this.saveState({
      lastRunStart: lastTriggeredAtMs || current.lastRunStart || null,
      isRunning: false,
      lastRunComplete: lastTriggeredAtMs || current.lastRunComplete || current.lastRunStart || null
    });
  }

  /**
   * Clear all rate limit data (for testing/debugging)
   */
  clearAll() {
    localStorage.removeItem(this.STORAGE_KEY);
    console.log('Rate limit data cleared');
  }

  /**
   * Get debug info
   */
  getDebugInfo() {
    const state = this.getState();
    const check = this.canRunPipeline();
    
    return {
      state,
      check,
      currentTime: new Date().toISOString(),
      cooldownMinutes: this.COOLDOWN_MINUTES
    };
  }
}

// Export singleton instance
export default new RateLimitService();
