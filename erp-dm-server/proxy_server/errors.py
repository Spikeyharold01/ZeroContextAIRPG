"""Safe HTTP-facing errors for accepted conversation exchanges."""

from dataclasses import dataclass


@dataclass
class TurnError(Exception):
    code: str
    message: str
    request_id: str
    retryable: bool = False
    http_status: int = 400

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "request_id": self.request_id, "retryable": self.retryable}}
