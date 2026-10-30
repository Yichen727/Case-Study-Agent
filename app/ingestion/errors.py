"""Safe errors for local presentation ingestion."""

MESSAGES = {
    "UNSUPPORTED_SOURCE": "Unsupported presentation source.",
    "INVALID_FILE": "Please upload a valid .pptx file within the supported size limits.",
    "FETCH_FAILED": "Unable to read or save the uploaded file. Please try again.",
    "PARSE_FAILED": "The uploaded presentation could not be processed. Please check the file and try again.",
}


class IngestionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])

    def user_message(self) -> str:
        return MESSAGES[self.code]
