class SlideExportNotImplemented(RuntimeError):
    pass


def export_original_slides(case_ids: list[str], output_path: str) -> None:
    """
    Application export boundary, to be connected to ordered cases in phase 7.
    The OOXML implementation has passed structural and manual sample validation.
    """
    raise SlideExportNotImplemented(
        "The original-slide export samples have passed validation. "
        "Exporting selected cases will be connected in a later stage."
    )
