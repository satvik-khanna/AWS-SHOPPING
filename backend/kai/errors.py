"""Failures that must never be replaced with invented data in live mode."""


class LiveDataUnavailable(RuntimeError):
    def __init__(self, service: str, detail: str):
        self.service = service
        self.detail = detail
        super().__init__(f"{service}: {detail}")
