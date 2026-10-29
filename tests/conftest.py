"""Keep the ordinary test suite offline even after local credentials are configured."""
import pytest


@pytest.fixture(autouse=True)
def offline_openai_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.setattr("app.config.ENV_FILE",tmp_path / "no-credentials.env")
