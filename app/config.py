from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "case_studies.sqlite3"
ENV_FILE = PROJECT_ROOT / ".env"

for directory in (DATA_DIR, UPLOAD_DIR):
    directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class AISettings:
    api_key: str = field(default="", repr=False)
    model: str = "gpt-5.4-mini"
    embedding_model: str = "text-embedding-3-small"


def get_ai_settings() -> AISettings:
    # Read on explicit work, so a newly saved .env takes effect without keeping
    # secrets in Streamlit session state, task payloads, or the database.
    values = {**dotenv_values(ENV_FILE), **os.environ}
    return AISettings(api_key=(values.get("OPENAI_API_KEY") or "").strip(),
                      model=(values.get("OPENAI_METADATA_MODEL") or "gpt-5.4-mini").strip(),
                      embedding_model=(values.get("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip())


@dataclass(frozen=True)
class MicrosoftSettings:
    tenant_id: str = ""
    client_id: str = ""
    sharepoint_hosts: tuple[str,...] = ("freud.sharepoint.com","freud-my.sharepoint.com")

    @property
    def configured(self):
        try:
            UUID(self.tenant_id)
            UUID(self.client_id)
            return True
        except ValueError:
            return False


def get_microsoft_settings():
    values = {**dotenv_values(ENV_FILE),**os.environ}
    hosts = values.get("MICROSOFT_SHAREPOINT_HOSTS") or "freud.sharepoint.com,freud-my.sharepoint.com"
    return MicrosoftSettings(tenant_id=(values.get("MICROSOFT_TENANT_ID") or "").strip(),
                             client_id=(values.get("MICROSOFT_CLIENT_ID") or "").strip(),
                             sharepoint_hosts=tuple(host.strip().lower() for host in hosts.split(",") if host.strip()))
