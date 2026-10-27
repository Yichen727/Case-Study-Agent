class SlideExportNotImplemented(RuntimeError):
    pass


def export_original_slides(case_ids: list[str], output_path: str) -> None:
    """
    Deliberate boundary for a future export implementation.

    Do not implement this by recreating shapes with python-pptx and claiming
    exact fidelity. First validate an Open XML or PowerPoint automation strategy
    against real source decks and target deployment environments.
    """
    raise SlideExportNotImplemented(
        "Original-slide export has not been implemented. "
        "Validate a fidelity-preserving PowerPoint/Open XML strategy first."
    )
