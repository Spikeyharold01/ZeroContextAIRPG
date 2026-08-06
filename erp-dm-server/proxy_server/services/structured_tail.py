"""Strict provider-neutral hidden JSON tail extraction and safe diagnostics."""

from dataclasses import dataclass
import hashlib
import logging
import json


START = "<zcairpg-storyteller-output-v1>"
END = "</zcairpg-storyteller-output-v1>"
logger = logging.getLogger(__name__)
_SENSITIVE_KEYS = {"password", "secret", "api_key", "token", "database_path", "db_path"}


def _redacted_debug_payload(payload: str) -> str:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        return "<unparseable hidden payload>"
    def redact(node):
        if isinstance(node, dict):
            return {key: "<redacted>" if key.casefold() in _SENSITIVE_KEYS else redact(child)
                    for key, child in node.items()}
        if isinstance(node, list):
            return [redact(child) for child in node]
        return node
    return json.dumps(redact(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class TailBlock:
    narrative: str
    payload: str
    payload_hash: str


def extract_tail(raw: str, *, request_id: str, secure_debug_raw_output: bool = False,
                 secure_debug_max_characters: int = 4096) -> TailBlock:
    if not isinstance(raw, str) or raw.count(START) != 1 or raw.count(END) != 1:
        raise ValueError("structured output requires exactly one hidden block")
    start = raw.index(START)
    end = raw.index(END, start + len(START))
    if raw[end + len(END):].strip():
        raise ValueError("structured output block must be the response tail")
    narrative = raw[:start].rstrip()
    payload = raw[start + len(START):end].strip()
    if not narrative or not payload or START in narrative or END in narrative:
        raise ValueError("visible narrative and hidden payload are required")
    digest = hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()
    logger.debug("structured tail request_id=%s bytes=%d payload_hash=%s extraction=valid",
                 request_id, len(payload.encode("utf-8")), digest)
    if secure_debug_raw_output:
        logger.debug("SENSITIVE HIDDEN MODEL STATE request_id=%s payload=%s", request_id,
                     _redacted_debug_payload(payload)[:secure_debug_max_characters])
    return TailBlock(narrative, payload, digest)
