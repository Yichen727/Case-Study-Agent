from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values

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


def get_ai_settings() -> AISettings:
    # Read on explicit work, so a newly saved .env takes effect without keeping
    # secrets in Streamlit session state, task payloads, or the database.
    values = {**dotenv_values(ENV_FILE), **os.environ}
    return AISettings(api_key=(values.get("OPENAI_API_KEY") or "").strip(),
                      model=(values.get("OPENAI_METADATA_MODEL") or "gpt-5.4-mini").strip())
