"""Campaign alias-to-message matching; message tokens never drive DB lookups."""

import re
import unicodedata

from proxy_server.models import AliasMatch

PRONOUNS = frozenset({"i", "me", "my", "you", "your", "we", "us", "they", "them", "he", "she", "it"})


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _spans(alias: str, message: str) -> list[tuple[int, int]]:
    escaped = re.escape(alias).replace(r"\ ", r"\s+")
    return [match.span() for match in re.finditer(rf"(?<!\w){escaped}(?!\w)", message, re.UNICODE)]


def match_aliases(raw_message: str, aliases: list[dict], *, limit: int = 20) -> list[AliasMatch]:
    normalized_message = normalize(raw_message)
    candidates = []
    for item in aliases:
        alias = normalize(str(item["alias"]))
        spans = _spans(alias, normalized_message)
        if not alias or alias in PRONOUNS or not spans:
            continue
        for span in spans:
            candidates.append((AliasMatch(alias, str(item["subject_type"]), str(item["subject_id"]),
                                          bool(item.get("canonical")), bool(item.get("in_scene")),
                                          bool(item.get("at_location"))), span))
    candidates.sort(key=lambda pair: (pair[1][0], -len(pair[0].alias), not pair[0].canonical,
                                      not pair[0].in_scene, not pair[0].at_location,
                                      pair[0].subject_type, pair[0].subject_id))
    identities_by_alias: dict[str, set[tuple[str, str]]] = {}
    for item, _span in candidates:
        identities_by_alias.setdefault(item.alias, set()).add((item.subject_type, item.subject_id))
    kept: list[AliasMatch] = []
    kept_spans: list[tuple[int, int]] = []
    for candidate, span in candidates:
        if any(span[0] >= prior[0] and span[1] <= prior[1] and span != prior for prior in kept_spans):
            continue
        if any(item.alias == candidate.alias and item.subject_type == candidate.subject_type
               and item.subject_id == candidate.subject_id for item in kept):
            continue
        kept.append(AliasMatch(**{**candidate.__dict__,
                                  "ambiguous": len(identities_by_alias[candidate.alias]) > 1}))
        kept_spans.append(span)
        if len(kept) == limit:
            break
    return kept
