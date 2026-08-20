from __future__ import annotations
import io
from dataclasses import dataclass
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from backend.utils.normalization import safe_name

FOLDER_MIME="application/vnd.google-apps.folder"

@dataclass
class DriveItem:
    id:str; url:str

class DriveService:
    """Private Google Drive storage. It never modifies sharing permissions."""
    def __init__(self, credentials:Credentials, root_id:str):
        if not root_id: raise ValueError("GOOGLE_DRIVE_ROOT_FOLDER_ID is required")
        self.api=build("drive","v3",credentials=credentials,cache_discovery=False); self.root_id=root_id

    def create_folder(self,name:str,parent_id:str)->DriveItem:
        obj=self.api.files().create(body={"name":safe_name(name),"mimeType":FOLDER_MIME,"parents":[parent_id]},fields="id,webViewLink").execute()
        return DriveItem(obj["id"],obj.get("webViewLink",f"https://drive.google.com/drive/folders/{obj['id']}"))

    def upload_bytes(self,name:str,data:bytes,mime_type:str,parent_id:str)->DriveItem:
        media=MediaIoBaseUpload(io.BytesIO(data),mimetype=mime_type,resumable=True)
        obj=self.api.files().create(body={"name":safe_name(name),"parents":[parent_id]},media_body=media,fields="id,webViewLink").execute()
        return DriveItem(obj["id"],obj.get("webViewLink",f"https://drive.google.com/file/d/{obj['id']}/view"))

    def exists(self,file_id:str)->bool:
        try:self.api.files().get(fileId=file_id,fields="id",supportsAllDrives=True).execute(); return True
        except Exception:return False

def credentials_from_session(token:dict)->Credentials:
    return Credentials(token=token.get("access_token"),refresh_token=token.get("refresh_token"),token_uri="https://oauth2.googleapis.com/token",client_id=token.get("client_id"),client_secret=token.get("client_secret"),scopes=token.get("scope",[]))

