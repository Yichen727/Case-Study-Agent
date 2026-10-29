"""Keep the ordinary test suite offline even after local credentials are configured."""
import pytest
from shutil import copyfile
from zipfile import ZipFile


@pytest.fixture
def deck_variant(tmp_path):
    """A byte-distinct valid PPTX version with identical slide content."""
    def create(source, name="new-version.pptx"):
        destination = tmp_path / name
        copyfile(source,destination)
        with ZipFile(destination,"a") as deck:
            deck.comment = name.encode()
        return destination
    return create


@pytest.fixture(autouse=True)
def offline_openai_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.setattr("app.config.ENV_FILE",tmp_path / "no-credentials.env")
