"""
gdrive.py - Google Drive API integration.
Supports authentication, file uploads, folder creation, sharing, and storage info.
"""

import os
import pickle
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import Database

SCOPES = ['https://www.googleapis.com/auth/drive.file']


class GoogleDrive:
    def __init__(self, db: Database, logger):
        self.db = db
        self.logger = logger
        self.service = None
        self.creds = None

    async def authenticate(self) -> bool:
        """Authenticate with Google Drive using OAuth2."""
        token_file = Config.GOOGLE_DRIVE_TOKEN_FILE
        creds = None

        # Load existing token if present
        if token_file.exists():
            try:
                with open(token_file, 'rb') as f:
                    creds = pickle.load(f)
            except Exception as e:
                self.logger.warning(f"Failed to load token: {e}")

        # Refresh or obtain new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, creds.refresh, Request())
                except Exception as e:
                    self.logger.error(f"Token refresh failed: {e}")
                    creds = None
            if not creds:
                if not Config.GOOGLE_DRIVE_CREDENTIALS_FILE.exists():
                    self.logger.error("Google Drive credentials file not found")
                    return False
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(Config.GOOGLE_DRIVE_CREDENTIALS_FILE), SCOPES
                )
                # Run local server for OAuth (requires user interaction)
                try:
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    self.logger.error(f"OAuth flow failed: {e}")
                    return False
            # Save credentials
            token_file.parent.mkdir(parents=True, exist_ok=True)
            with open(token_file, 'wb') as f:
                pickle.dump(creds, f)

        self.creds = creds
        self.service = build('drive', 'v3', credentials=creds)
        self.logger.info("Google Drive authenticated")
        return True

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def upload_file(self, local_path: Path, filename: str = None, folder_id: str = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Upload a file to Google Drive.
        Returns: (file_id, web_view_link)
        """
        if not self.service:
            if not await self.authenticate():
                return None, None

        file_name = filename or local_path.name
        media = MediaFileUpload(str(local_path), resumable=True, chunksize=1024*1024*5)  # 5MB chunks

        file_metadata = {
            'name': file_name,
            'parents': [folder_id] if folder_id else []
        }

        try:
            loop = asyncio.get_event_loop()
            file = await loop.run_in_executor(
                None,
                lambda: self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id,webViewLink'
                ).execute()
            )
            file_id = file.get('id')
            web_link = file.get('webViewLink')

            # Optionally share with specific email
            if Config.GOOGLE_DRIVE_SHARE_EMAIL:
                await self._share_file(file_id, Config.GOOGLE_DRIVE_SHARE_EMAIL)

            self.logger.info(f"Uploaded to Drive: {file_name} (ID: {file_id})")
            return file_id, web_link

        except HttpError as e:
            self.logger.error(f"Google Drive upload error: {e}")
            raise Exception(f"Upload failed: {e}")

    async def _share_file(self, file_id: str, email: str, role: str = 'reader') -> bool:
        """Share a file with a specific email address."""
        if not self.service:
            return False

        permission = {
            'type': 'user',
            'role': role,
            'emailAddress': email,
        }
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.service.permissions().create(
                    fileId=file_id,
                    body=permission,
                    sendNotificationEmail=False
                ).execute()
            )
            return True
        except HttpError as e:
            self.logger.warning(f"Failed to share file {file_id}: {e}")
            return False

    async def create_folder(self, folder_name: str, parent_id: str = None) -> Optional[str]:
        """Create a folder in Google Drive and return its ID."""
        if not self.service:
            await self.authenticate()

        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        try:
            loop = asyncio.get_event_loop()
            folder = await loop.run_in_executor(
                None,
                lambda: self.service.files().create(body=file_metadata, fields='id').execute()
            )
            return folder.get('id')
        except HttpError as e:
            self.logger.error(f"Failed to create folder: {e}")
            return None

    async def get_about(self) -> Dict[str, Any]:
        """Get storage quota and user info."""
        if not self.service:
            await self.authenticate()

        try:
            loop = asyncio.get_event_loop()
            about = await loop.run_in_executor(
                None,
                lambda: self.service.about().get(fields='storageQuota,user').execute()
            )
            return about
        except HttpError as e:
            self.logger.error(f"Failed to get about: {e}")
            return {}

    async def list_files(self, folder_id: str = None, page_size: int = 10) -> list:
        """List files in a folder."""
        if not self.service:
            await self.authenticate()

        query = f"'{folder_id}' in parents" if folder_id else ""
        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self.service.files().list(
                    q=query, pageSize=page_size, fields="files(id, name, mimeType, size, webViewLink)"
                ).execute()
            )
            return results.get('files', [])
        except HttpError as e:
            self.logger.error(f"Failed to list files: {e}")
            return []

    async def delete_file(self, file_id: str) -> bool:
        """Delete a file from Google Drive."""
        if not self.service:
            await self.authenticate()

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self.service.files().delete(fileId=file_id).execute())
            return True
        except HttpError as e:
            self.logger.error(f"Failed to delete file {file_id}: {e}")
            return False
