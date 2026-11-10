"""One error type for every refusal in this package; routes.py turns it into
a JSON response with the same status and message."""


class CorpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message
