from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "OMFS CaseVault"
    environment: str = "development"
    database_url: str = "sqlite:///./data/casevault.db"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/auth/callback"
    google_drive_root_folder_id: str = ""
    allowed_google_emails: str = ""
    session_secret: str = "change-me"
    auth_disabled: bool = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowlist(self) -> set[str]:
        return {x.strip().lower() for x in self.allowed_google_emails.split(",") if x.strip()}

@lru_cache
def get_settings() -> Settings:
    return Settings()

