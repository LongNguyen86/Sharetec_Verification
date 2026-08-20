from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration management with fail-fast validation using Pydantic Settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base Paths
    BASE_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent)

    # SharePoint Settings
    SHAREPOINT_SITE_URL: str = Field(default="", alias="SHAREPOINT_SITE_URL")
    SHAREPOINT_USERNAME: str = Field(default="", alias="SHAREPOINT_USERNAME")
    SHAREPOINT_PASSWORD: str = Field(default="", alias="SHAREPOINT_PASSWORD")

    # Gemini AI API Key
    GEMINI_API_KEY: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    GOOGLE_API_KEY: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")

    @property
    def api_key(self) -> str:
        """Resolve active Gemini API Key."""
        return self.GEMINI_API_KEY or self.GOOGLE_API_KEY or ""

    # Workspace directory
    WORKSPACE_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "workspace")

    @property
    def db_path(self) -> Path:
        return self.WORKSPACE_DIR / "rules.db"

    # Backward compatibility properties for legacy code
    @property
    def SITE_URL(self) -> str:
        return self.SHAREPOINT_SITE_URL

    @property
    def LOCAL_INGEST_DIR(self) -> Path:
        return self.WORKSPACE_DIR / "ingest"

    @property
    def LOCAL_CSV_DIR(self) -> Path:
        return self.WORKSPACE_DIR / "csv"

    @property
    def LOCAL_MAPPING_DIR(self) -> Path:
        return self.WORKSPACE_DIR / "mapping"

    @property
    def LOCAL_REPORT_DIR(self) -> Path:
        return self.WORKSPACE_DIR / "qa_reports"
    
settings = Settings()