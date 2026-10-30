"""Keep the ordinary test suite offline even after local credentials are configured."""
import pytest
from shutil import copyfile
from zipfile import ZipFile


@pytest.fixture(scope="session")
def synthetic_export_template(tmp_path_factory):
    """Use a small 4:3 template with the same dimensions as synthetic source decks."""
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()
    for page in range(1,6):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1),Inches(1),Inches(7),Inches(1)).text = f"TEMPLATE PAGE {page}"
    path = tmp_path_factory.mktemp("template") / "company.pptx"
    deck.save(path)
    return path


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
def offline_openai_credentials(monkeypatch, tmp_path, synthetic_export_template):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.setattr("app.config.ENV_FILE",tmp_path / "no-credentials.env")
    monkeypatch.setattr("app.config.EXPORT_TEMPLATE_PATH",synthetic_export_template)
