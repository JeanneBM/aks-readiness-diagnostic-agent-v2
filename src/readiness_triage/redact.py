"""Pattern-based redaction applied before evidence leaves the collector.

This is a safety net, not a guarantee: free-form logs can hold secrets in shapes
no pattern anticipates. Review bundles before sending them to a model provider.
"""
import re

_RULES = (
    (re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\b(?:password|passwd|pwd|api[_-]?key|access[_-]?key|secret|token|client[_-]?secret"
                r"|authorization|connectionstring)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([?&](?:sig|signature|token|key|code|access_token)=)[^&\s\"]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(AccountKey=)[^;\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(https?://)[^\s/:@]+:[^\s/@]+@"), r"\1[REDACTED]@"),
    # Bounded and anchored to token starts, so long tokens without "@" stay linear.
    (re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){0,8}\.[A-Za-z]{2,24}\b"),
     "[REDACTED_EMAIL]"),
)


def redact(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
