"""Safe ingestion errors shared by local and future cloud sources."""

MESSAGES = {
    "INVALID_URL": "Please enter a valid OneDrive or SharePoint link.",
    "UNSUPPORTED_SOURCE": "This source is not supported. Please use OneDrive, SharePoint or local upload.",
    "AUTH_REQUIRED": "Sign in with Microsoft to access this presentation.",
    "ACCESS_DENIED": "You don't have permission to access this file.",
    "FILE_NOT_FOUND": "The presentation could not be found. Please check the link.",
    "INVALID_FILE": "The linked file is not a valid PowerPoint presentation.",
    "FETCH_FAILED": "Unable to retrieve the file. Please try again.",
    "PARSE_FAILED": "The presentation was retrieved but could not be processed.",
}

LOCAL_MESSAGES = {
    "INVALID_FILE": "Please upload a valid .pptx file within the supported size limits.",
    "FETCH_FAILED": "Unable to read or save the uploaded file. Please try again.",
    "PARSE_FAILED": "The uploaded presentation could not be processed. Please check the file and try again.",
}


class IngestionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])

    def user_message(self, source_type: str = "LOCAL") -> str:
        if source_type == "LOCAL":
            return LOCAL_MESSAGES.get(self.code, MESSAGES[self.code])
        return MESSAGES[self.code]
