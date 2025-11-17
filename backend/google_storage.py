"""
Google Sheets Storage Backend
Handles reading from and writing to Google Sheets as a database
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import json
import os

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


class GoogleSheetsDB:
    """
    Use Google Sheets as a database for the article pipeline
    """
    
    def __init__(self, credentials_path='credentials.json', sheet_id=None):
        """
        Initialize connection to Google Sheets
        
        Args:
            credentials_path: Path to service account JSON file
            sheet_id: Google Sheet ID (from URL)
        """
        # Define scopes (add Drive scope!)
        scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/drive.file'
        ]
        
        # Load credentials
        if os.path.exists(credentials_path):
            # Local development
            creds = Credentials.from_service_account_file(
                credentials_path,
                scopes=scope
            )
        else:
            # GitHub Actions (credentials from environment)
            creds_json = os.getenv('GOOGLE_CREDENTIALS')
            if creds_json:
                creds_dict = json.loads(creds_json)
                creds = Credentials.from_service_account_info(
                    creds_dict,
                    scopes=scope
                )
            else:
                raise ValueError("No credentials found. Set GOOGLE_CREDENTIALS env variable or provide credentials.json")
        
        # STORE credentials for later use with Drive API
        self._credentials = creds
        
        # Authorize and get client
        self.client = gspread.authorize(creds)
        
        # Open spreadsheet
        sheet_id = sheet_id or os.getenv('GOOGLE_SHEET_ID')
        if not sheet_id:
            raise ValueError("No Sheet ID provided. Set GOOGLE_SHEET_ID env variable or pass sheet_id parameter")
        
        try:
            self.spreadsheet = self.client.open_by_key(sheet_id)
            print(f"✅ Connected to Google Sheet: {self.spreadsheet.title}")
        except Exception as e:
            raise ValueError(f"Failed to open spreadsheet with ID {sheet_id}: {str(e)}")
        
        # Get worksheets
        try:
            self.articles_sheet = self.spreadsheet.worksheet('Articles')
            self.drafts_sheet = self.spreadsheet.worksheet('Outreach Drafts')
            self.pitching_sheet = self.spreadsheet.worksheet('Pitching Menu')
            print("✅ All worksheets found: Articles, Outreach Drafts, Pitching Menu")
        except Exception as e:
            raise ValueError(f"Failed to find worksheets: {str(e)}")
    
    def save_articles(self, articles):
        """
        Save collected articles to Google Sheets
        
        Args:
            articles: List of article dictionaries
        """
        if not articles:
            print("⚠️  No articles to save")
            return
        
        rows = []
        for article in articles:
            rows.append([
                article.get('id', ''),
                article.get('title', ''),
                article.get('url', ''),
                article.get('publication', ''),
                article.get('journalist', 'Unknown'),
                article.get('summary', ''),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        # Append to sheet (keeps history)
        try:
            self.articles_sheet.append_rows(rows, value_input_option='USER_ENTERED')
            print(f"✅ Saved {len(articles)} articles to Google Sheets")
        except Exception as e:
            print(f"❌ Error saving articles: {str(e)}")
            raise
    
    def get_recent_articles(self, limit=100):
        """
        Get recent articles for summarization
        
        Args:
            limit: Maximum number of articles to return
            
        Returns:
            List of article dictionaries
        """
        try:
            # Get all records (skips header row)
            records = self.articles_sheet.get_all_records()
            
            # Return most recent
            recent = records[-limit:] if len(records) > limit else records
            print(f"✅ Retrieved {len(recent)} recent articles")
            return recent
        except Exception as e:
            print(f"❌ Error retrieving articles: {str(e)}")
            return []
    
    def save_drafts(self, drafts):
        """
        Save outreach drafts for review
        
        Args:
            drafts: List of draft email dictionaries
        """
        if not drafts:
            print("⚠️  No drafts to save")
            return
        
        rows = []
        for draft in drafts:
            rows.append([
                draft.get('id', ''),
                draft.get('journalist', ''),
                draft.get('email', ''),
                draft.get('subject', ''),
                draft.get('body', ''),
                draft.get('topic', ''),
                'pending',  # status
                'FALSE',    # approved
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        try:
            self.drafts_sheet.append_rows(rows, value_input_option='USER_ENTERED')
            print(f"✅ Saved {len(drafts)} drafts to Google Sheets")
        except Exception as e:
            print(f"❌ Error saving drafts: {str(e)}")
            raise
    
    def get_pending_drafts(self):
        """
        Get drafts awaiting approval
        
        Returns:
            List of pending draft dictionaries
        """
        try:
            records = self.drafts_sheet.get_all_records()
            
            # Filter for pending drafts
            pending = [r for r in records if r.get('Status', '').lower() == 'pending']
            
            print(f"✅ Retrieved {len(pending)} pending drafts")
            return pending
        except Exception as e:
            print(f"❌ Error retrieving drafts: {str(e)}")
            return []
    
    def get_pitching_menu(self):
        """
        Get active pitching menu items
        
        Returns:
            List of pitching menu dictionaries
        """
        try:
            records = self.pitching_sheet.get_all_records()
            
            # Filter active items
            active = [r for r in records if str(r.get('Active', '')).upper() == 'TRUE']
            
            print(f"✅ Retrieved {len(active)} active pitching menu items")
            return active
        except Exception as e:
            print(f"❌ Error retrieving pitching menu: {str(e)}")
            return []
        
    def upload_pdf_to_drive(self, pdf_path, target_folder_id=None):
        """
        Upload PDF to a shared Google Drive folder
        
        Args:
            pdf_path: Path to the PDF file
            target_folder_id: ID of the shared Drive folder (optional)
            
        Returns:
            (download_link, view_link) tuple
        """
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            
            # Build Drive service with stored credentials
            drive_service = build('drive', 'v3', credentials=self._credentials)
            
            # Use provided folder ID or environment variable
            folder_id = target_folder_id or os.getenv('GOOGLE_DRIVE_FOLDER_ID')
            
            if not folder_id:
                print("⚠️  No Drive folder ID provided. Upload skipped.")
                print("Set GOOGLE_DRIVE_FOLDER_ID environment variable")
                return None, None
            
            # Check if file exists
            if not os.path.exists(pdf_path):
                print(f"❌ PDF file not found: {pdf_path}")
                return None, None
            
            print(f"📤 Uploading {os.path.basename(pdf_path)} to Google Drive...")
            
            # Upload PDF to the specific folder
            file_metadata = {
                'name': os.path.basename(pdf_path),
                'parents': [folder_id]  # Upload to shared folder
            }
            
            media = MediaFileUpload(pdf_path, mimetype='application/pdf', resumable=True)
            
            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink, webContentLink'
            ).execute()
            
            print(f"✅ File uploaded with ID: {file['id']}")
            
            # Make file publicly accessible (anyone with link can view)
            print("🔓 Setting public access...")
            drive_service.permissions().create(
                fileId=file['id'],
                body={
                    'type': 'anyone',
                    'role': 'reader'
                },
                fields='id'
            ).execute()
            
            # Get shareable links
            # For direct download, we need to modify the link
            file_id = file['id']
            download_link = f"https://drive.google.com/uc?export=download&id={file_id}"
            view_link = file.get('webViewLink')
            
            print(f"✅ PDF uploaded to Google Drive")
            print(f"   View: {view_link}")
            print(f"   Download: {download_link}")
            
            return download_link, view_link
            
        except Exception as e:
            print(f"❌ Error uploading PDF to Drive: {str(e)}")
            import traceback
            traceback.print_exc()
            return None, None

    def _get_or_create_drive_folder(self, drive_service, folder_name):
        """
        This method is no longer needed - we use a pre-created shared folder
        Keeping for backwards compatibility
        """
        print(f"⚠️  Using pre-shared folder instead of creating new one")
        return None
        
    def _get_or_create_drive_folder(self, drive_service, folder_name):
        """
        Get existing folder or create new one in Google Drive
        """
        # Search for existing folder
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        folders = results.get('files', [])
        
        if folders:
            return folders[0]['id']
        
        # Create new folder
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        
        folder = drive_service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()
        
        print(f"✅ Created Google Drive folder: {folder_name}")
        return folder['id']
    
    def save_artifact_info(self, run_number, run_url):
        """
        Save GitHub Actions artifact info to Metadata sheet
        
        Args:
            run_number: GitHub Actions run number
            run_url: URL to the workflow run
        """
        try:
            # Get or create Metadata sheet
            try:
                metadata_sheet = self.spreadsheet.worksheet('Metadata')
            except:
                metadata_sheet = self.spreadsheet.add_worksheet(
                    title='Metadata',
                    rows=10,
                    cols=3
                )
                # Add headers
                metadata_sheet.update('A1:C1', [['Key', 'Value', 'Updated']])
            
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Save run number
            try:
                cell = metadata_sheet.find('latest_run_number')
                row_num = cell.row
                metadata_sheet.update(f'B{row_num}:C{row_num}', [[str(run_number), timestamp]])
            except:
                metadata_sheet.append_row(['latest_run_number', str(run_number), timestamp])
            
            # Save run URL
            try:
                cell = metadata_sheet.find('latest_run_url')
                row_num = cell.row
                metadata_sheet.update(f'B{row_num}:C{row_num}', [[run_url, timestamp]])
            except:
                metadata_sheet.append_row(['latest_run_url', run_url, timestamp])
            
            print(f"✅ Artifact info saved to Metadata sheet")
            print(f"   Run #{run_number}")
            
        except Exception as e:
            print(f"⚠️  Error saving artifact info: {str(e)}")
    
    def update_draft_status(self, draft_id, approved=True):
        """
        Update draft status (for future use)
        
        Args:
            draft_id: ID of the draft to update
            approved: Whether the draft was approved
        """
        try:
            # Find the row with this draft_id
            cell = self.drafts_sheet.find(str(draft_id))
            if cell:
                row_num = cell.row
                
                # Update status columns
                if approved:
                    self.drafts_sheet.update_cell(row_num, 7, 'approved')  # Status column
                    self.drafts_sheet.update_cell(row_num, 8, 'TRUE')      # Approved column
                
                print(f"✅ Draft {draft_id} marked as {'approved' if approved else 'rejected'}")
            else:
                print(f"⚠️  Draft {draft_id} not found")
        except Exception as e:
            print(f"❌ Error updating draft: {str(e)}")


# Test function
def test_connection():
    """
    Test the Google Sheets connection
    """
    print("\n" + "="*60)
    print("Testing Google Sheets Connection")
    print("="*60 + "\n")
    
    try:
        # Create instance (no import needed - defined in this file)
        db = GoogleSheetsDB()
        
        print("\n📊 Testing save_articles...")
        test_articles = [
            {
                'id': 'test-1',
                'title': 'Test Article 1',
                'url': 'https://example.com/article1',
                'publication': 'Test Publication',
                'journalist': 'Test Author',
                'summary': 'This is a test article summary'
            }
        ]
        db.save_articles(test_articles)
        
        print("\n📊 Testing get_pitching_menu...")
        menu = db.get_pitching_menu()
        print(f"Found {len(menu)} pitching menu items:")
        for item in menu:
            print(f"  - {item.get('Topic')}: {item.get('Keywords')}")
        
        print("\n✅ All tests passed!\n")
        
    except Exception as e:
        print(f"\n❌ Test failed: {str(e)}\n")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Run test when executed directly
    test_connection()