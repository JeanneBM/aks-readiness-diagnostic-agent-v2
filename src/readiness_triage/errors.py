"""Error categories. None of them carries provider response bodies or credentials."""


class ProviderError(Exception):
    """The provider did not return a usable HTTP 200. Metadata is allowlisted."""

    def __init__(self, message: str, *, http_status=None, retry_after_seconds=None,
                 transport_category=None, attempts=None):
        super().__init__(message)
        self.details = {}
        if type(http_status) is int and 100 <= http_status <= 599:
            self.details["http_status"] = http_status
        if type(retry_after_seconds) is int and 0 <= retry_after_seconds <= 3600:
            self.details["retry_after_seconds"] = retry_after_seconds
        if transport_category in ("timeout", "connection"):
            self.details["transport_category"] = transport_category
        if type(attempts) is int and attempts >= 1:
            self.details["attempts"] = attempts


class ContractError(Exception):
    """The provider answered, but the answer broke the output contract."""


class ModelNoAnswer(ContractError):
    """The model declined or stopped early; kept apart from an invalid answer."""

    REASONS = ("refusal", "truncated", "filtered", "incomplete")

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            raise ValueError("Invalid no-answer reason")
        self.reason = reason
        super().__init__("The model gave no complete diagnosis: " + reason)
