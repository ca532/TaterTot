/**
 * Google Sheets API Service
 * Fetches data from Google Sheets for display in the dashboard
 */

const SHEET_ID = import.meta.env.VITE_GOOGLE_SHEET_ID;
const API_KEY = import.meta.env.VITE_GOOGLE_API_KEY;

class GoogleSheetsService {
  constructor() {
    this.baseURL = `https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}`;
    
    if (!SHEET_ID || !API_KEY) {
      console.warn('⚠️ Google Sheets credentials not configured');
      console.warn('Add VITE_GOOGLE_SHEET_ID and VITE_GOOGLE_API_KEY to .env.local');
    }
  }

  /**
   * Fetch articles from Google Sheets
   */
  async getArticles() {
    if (!SHEET_ID || !API_KEY) {
      console.error('Google Sheets not configured');
      return [];
    }

    const range = 'Articles!A2:G1000'; // Skip header row, get all data
    const url = `${this.baseURL}/values/${range}?key=${API_KEY}`;
    
    try {
      const response = await fetch(url);
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const data = await response.json();
      
      if (!data.values || data.values.length === 0) {
        console.log('No articles found in Google Sheets');
        return [];
      }
      
      // Transform rows into objects
      const articles = data.values.map((row, index) => ({
        id: row[0] || `article-${index}`,
        title: row[1] || 'Untitled',
        url: row[2] || '#',
        publication: row[3] || 'Unknown',
        journalist: row[4] || 'Unknown',
        summary: row[5] || 'No summary available',
        collectedDate: row[6] || new Date().toISOString()
      }));
      
      console.log(`✅ Fetched ${articles.length} articles from Google Sheets`);
      return articles;
      
    } catch (error) {
      console.error('❌ Error fetching articles:', error);
      return [];
    }
  }

  /**
   * Fetch pending outreach drafts (for Phase 3)
   */
  async getDrafts() {
    if (!SHEET_ID || !API_KEY) {
      return [];
    }

    const range = 'Outreach Drafts!A2:I1000';
    const url = `${this.baseURL}/values/${range}?key=${API_KEY}`;
    
    try {
      const response = await fetch(url);
      const data = await response.json();
      
      if (!data.values) return [];
      
      const drafts = data.values
        .map((row, index) => ({
          id: row[0] || `draft-${index}`,
          journalist: row[1] || 'Unknown',
          email: row[2] || '',
          subject: row[3] || '',
          body: row[4] || '',
          topic: row[5] || '',
          status: row[6] || 'pending',
          approved: row[7] === 'TRUE',
          createdDate: row[8] || new Date().toISOString()
        }))
        .filter(draft => draft.status === 'pending');
      
      console.log(`✅ Fetched ${drafts.length} pending drafts`);
      return drafts;
      
    } catch (error) {
      console.error('❌ Error fetching drafts:', error);
      return [];
    }
  }

  /**
   * Fetch pitching menu items
   */
  async getPitchingMenu() {
    if (!SHEET_ID || !API_KEY) {
      return [];
    }

    const range = 'Pitching Menu!A2:E1000';
    const url = `${this.baseURL}/values/${range}?key=${API_KEY}`;
    
    try {
      const response = await fetch(url);
      const data = await response.json();
      
      if (!data.values) return [];
      
      const items = data.values
        .map((row, index) => ({
          id: row[0] || `topic-${index}`,
          topic: row[1] || 'Unknown',
          description: row[2] || '',
          keywords: row[3] ? row[3].split(',').map(k => k.trim()) : [],
          active: row[4] === 'TRUE'
        }))
        .filter(item => item.active);
      
      console.log(`✅ Fetched ${items.length} pitching menu items`);
      return items;
      
    } catch (error) {
      console.error('❌ Error fetching pitching menu:', error);
      return [];
    }
  }

  /**
   * Get the latest workflow run info from GitHub API
   */
  async getLatestWorkflowRun() {
    // For now, return mock data
    return {
      status: 'completed',
      conclusion: 'success',
      created_at: new Date().toISOString()
    };
  }

  /**
 * Get the latest PDF download link from Metadata sheet
 */
  async getLatestPDFLink() {
    if (!SHEET_ID || !API_KEY) {
      return null;
    }

    const range = 'Metadata!A:C';
    const url = `${this.baseURL}/values/${range}?key=${API_KEY}`;
    
    try {
      const response = await fetch(url);
      
      if (!response.ok) {
        // Metadata sheet might not exist yet
        return null;
      }
      
      const data = await response.json();
      
      if (!data.values || data.values.length === 0) {
        return null;
      }
      
      // Find the latest_pdf row
      const pdfRow = data.values.find(row => row[0] === 'latest_pdf');
      const pdfViewRow = data.values.find(row => row[0] === 'latest_pdf_view');
      
      if (pdfRow && pdfRow[1]) {
        return {
          downloadLink: pdfRow[1],
          viewLink: pdfViewRow ? pdfViewRow[1] : pdfRow[1],
          updatedAt: pdfRow[2] || null
        };
      }
      
      return null;
      
    } catch (error) {
      console.error('Error fetching PDF link:', error);
      return null;
    }
  }

    /**
   * Get the latest GitHub Actions run info from Metadata sheet
   */
  async getLatestRunInfo() {
    if (!SHEET_ID || !API_KEY) {
      return null;
    }

    const range = 'Metadata!A:C';
    const url = `${this.baseURL}/values/${range}?key=${API_KEY}`;
    
    try {
      const response = await fetch(url);
      
      if (!response.ok) {
        return null;
      }
      
      const data = await response.json();
      
      if (!data.values || data.values.length === 0) {
        return null;
      }
      
      // Find the run number and URL
      const runNumberRow = data.values.find(row => row[0] === 'latest_run_number');
      const runUrlRow = data.values.find(row => row[0] === 'latest_run_url');
      
      if (runNumberRow && runNumberRow[1]) {
        return {
          runNumber: runNumberRow[1],
          runUrl: runUrlRow ? runUrlRow[1] : null,
          updatedAt: runNumberRow[2] || null
        };
      }
      
      return null;
      
    } catch (error) {
      console.error('Error fetching run info:', error);
      return null;
    }
  }
}

export default new GoogleSheetsService();