"""Create small synthetic PPTX sources for identity and export acceptance.

Contains invented test data only. These are not company case studies.
Run from the repository root: python -B -m evaluation.make_fixture_decks
"""
from io import BytesIO
from pathlib import Path
import struct
import zlib

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches


def test_image() -> BytesIO:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return BytesIO(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + bytes((20, 150, 210)) * 8) * 8))
        + chunk(b"IEND", b"")
    )


def add_case(deck, continuation=False, rich=False):
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    for y, text in (
        (0.2, "CASE STUDY SYNTHETIC"),
        (0.8, "SYNTHETIC: SAME NAME"),
        (1.5, "BRIEF\nSynthetic continuation." if continuation else "BRIEF\nSynthetic test brief."),
    ):
        if y == 1.5:
            text += "\nCREATIVE\nInvented fixture content.\nIMPACT\nNo real campaign outcome."
        slide.shapes.add_textbox(Inches(0.4), Inches(y), Inches(5), Inches(2)).text = text
    if rich:
        slide.shapes.add_picture(test_image(), Inches(6), Inches(0.5), width=Inches(0.5))
        data = CategoryChartData()
        data.categories = ["Test A", "Test B"]
        data.add_series("Synthetic values", [1, 2])
        slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(6), Inches(1.5),
                               Inches(3), Inches(3), data)
        run = slide.shapes.add_textbox(Inches(0.4), Inches(4.5), Inches(4), Inches(0.5)).text_frame.paragraphs[0].add_run()
        run.text = "READ MORE HERE."
        run.hyperlink.address = "https://example.com/synthetic-fixture"
    return slide


def main():
    folder = Path(__file__).with_name("fixtures")
    folder.mkdir(exist_ok=True)
    first = Presentation()
    add_case(first, rich=True)
    add_case(first, continuation=True)
    first.save(folder / "source_a.pptx")
    second = Presentation()
    add_case(second)
    second.slide_master.background.fill.solid()
    second.slide_master.background.fill.fore_color.rgb = RGBColor(235, 240, 245)
    second.save(folder / "source_b.pptx")
    print("Created", folder / "source_a.pptx", "(2 slides, image, editable chart, hyperlink, continuation)")
    print("Created", folder / "source_b.pptx", "(1 slide, same title/page number, different master background)")


if __name__ == "__main__":
    main()
