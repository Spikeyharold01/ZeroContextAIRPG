"""One-shot JSON syntax recovery with mandatory Pydantic validation."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from .models import RecoveryDiagnostics, StructuredOutputPolicy, StructuredOutputResult


_FENCE = re.compile(r"\A\s*```(?P<language>json)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```\s*\Z", re.IGNORECASE)
_BLOCK = re.compile(r"\A\s*<structured-output>\s*(?P<body>[\s\S]*?)\s*</structured-output>\s*\Z")
_FORBIDDEN_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _DuplicateKey(ValueError):
    pass


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _summary(error: object, limit: int) -> str:
    if isinstance(error, ValidationError):
        parts = []
        for item in error.errors(include_url=False, include_context=False, include_input=False)[:10]:
            location = ".".join(str(value) for value in item.get("loc", ())) or "$"
            parts.append(f"{location}: {item.get('type', 'validation_error')}")
        value = "; ".join(parts)
    else:
        value = type(error).__name__
    return value[:limit]


def _category(error: ValidationError) -> str:
    errors = error.errors(include_url=False, include_context=False, include_input=False)
    return "syntax" if errors and all(item.get("type") == "json_invalid" for item in errors) else "schema"


def _extract_envelope(raw: str, policy: StructuredOutputPolicy) -> tuple[str, bool, str | None]:
    stripped = raw.strip()
    if stripped.startswith("```"):
        if not policy.allow_markdown_fence_extraction:
            return raw, False, "markdown fences are disabled"
        match = _FENCE.fullmatch(raw)
        if match is None or (match.group("language") or "").casefold() != "json":
            return raw, False, "only one complete json markdown fence is accepted"
        return match.group("body").strip(), True, None
    if "<structured-output>" in stripped or "</structured-output>" in stripped:
        match = _BLOCK.fullmatch(raw)
        if match is None:
            return raw, False, "structured-output block is incomplete or ambiguous"
        return match.group("body").strip(), True, None
    return stripped, False, None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _strict_load(text: str, *, reject_duplicates: bool) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_pairs if reject_duplicates else dict,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite number: {value}")),
    )


def _scan_top_level_values(text: str) -> int:
    """Count plausible top-level JSON value starts without choosing one."""
    i = 0
    n = len(text)
    count = 0
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        ch = text[i]
        if ch in "{[":
            count += 1
            stack = [ch]
            i += 1
            in_string = False
            escaped = False
            quote = '"'
            previous = ch
            while i < n and stack:
                c = text[i]
                if in_string:
                    if escaped:
                        escaped = False
                    elif c == "\\":
                        escaped = True
                    elif c == quote:
                        in_string = False
                elif c in {'"', "'"}:
                    in_string = True
                    quote = c
                elif c in "{[":
                    if previous not in "{:[,":
                        return count + 1
                    stack.append(c)
                elif c in "}]" and (stack[-1], c) in {("{", "}"), ("[", "]")}:
                    stack.pop()
                elif c in "}]" and stack:
                    stack.pop()
                if not c.isspace():
                    previous = c
                i += 1
            continue
        if ch in {'"', "'"}:
            count += 1
            quote = ch
            i += 1
            escaped = False
            while i < n:
                c = text[i]
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == '-' or ch.isdigit():
            count += 1
            i += 1
            while i < n and (text[i].isdigit() or text[i] in ".eE+-"):
                i += 1
            continue
        if ch.isalpha():
            count += 1
            i += 1
            while i < n and text[i].isalpha():
                i += 1
            continue
        i += 1
    return count


def _has_multiple_values(text: str) -> bool:
    """Recognize multiple plausible top-level values before repair."""
    if _scan_top_level_values(text) > 1:
        return True
    decoder = json.JSONDecoder()
    try:
        _, end = decoder.raw_decode(text.lstrip())
    except json.JSONDecodeError:
        return False
    return bool(text.lstrip()[end:].strip())


def _check_shape(value: Any, policy: StructuredOutputPolicy) -> None:
    keys = arrays = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal keys, arrays
        if depth > policy.max_nesting_depth:
            raise ValueError("maximum nesting depth exceeded")
        if isinstance(node, dict):
            keys += len(node)
            if keys > policy.max_object_keys:
                raise ValueError("maximum object key count exceeded")
            for key, child in node.items():
                if key.startswith(("__", "$")):
                    raise ValueError("reserved or malicious object key")
                walk(child, depth + 1)
        elif isinstance(node, list):
            arrays += len(node)
            if arrays > policy.max_array_elements:
                raise ValueError("maximum array element count exceeded")
            for child in node:
                walk(child, depth + 1)

    walk(value, 0)


def _repair_json_once(raw: str) -> str:
    module = importlib.import_module("json_repair")
    repaired = module.repair_json(raw, return_objects=False)
    if not isinstance(repaired, str):
        raise TypeError("json_repair returned a non-string value")
    return repaired


def validate_structured_output(
    raw_output: str,
    target_model: type[BaseModel],
    policy: StructuredOutputPolicy | None = None,
) -> StructuredOutputResult:
    """Validate directly, repair syntax once if needed, then validate again.

    This function performs no persistence and contains no retry or model call.
    Every exception is converted to a safe structured rejection result.
    """
    policy = policy or StructuredOutputPolicy()
    raw_for_hash = raw_output if isinstance(raw_output, str) else repr(type(raw_output))
    original_hash = _hash(raw_for_hash)
    model_name = getattr(target_model, "__name__", "invalid-target")
    envelope_extracted = False

    def result(status: str, *, model=None, attempted=False, succeeded=False, category=None,
               error=None, repaired=None, first="not_attempted", second="not_attempted", elapsed=None):
        summary = _summary(error, policy.max_error_summary_characters) if error is not None else None
        return StructuredOutputResult(
            status=status, validated_model=model, repair_attempted=attempted, repair_succeeded=succeeded,
            failure_category=category, error_summary=summary, original_content_hash=original_hash,
            repaired_content_hash=_hash(repaired) if repaired is not None else None,
            repair_method="json_repair" if attempted else None,
            diagnostics=RecoveryDiagnostics(target_model=model_name, first_validation_category=first,
                second_validation_category=second, envelope_extracted=envelope_extracted,
                repair_attempts=1 if attempted else 0, elapsed_repair_ms=elapsed,
                repair_time_warning=bool(elapsed is not None and policy.repair_time_warning_ms is not None
                                         and elapsed > policy.repair_time_warning_ms),
                raw_output_included=policy.secure_debug_raw_output),
            secure_debug_raw_output=raw_output if policy.secure_debug_raw_output and isinstance(raw_output, str) else None,
        )

    if not isinstance(raw_output, str) or not isinstance(target_model, type) or not issubclass(target_model, BaseModel):
        return result("input_rejected", category="input", error=TypeError())
    size = len(raw_output.encode("utf-8", "surrogatepass"))
    if size == 0 or size > policy.max_input_bytes or not raw_output.strip():
        return result("input_rejected", category="input", error=ValueError())
    if _FORBIDDEN_CONTROLS.search(raw_output):
        return result("input_rejected", category="input", error=ValueError())
    candidate, envelope_extracted, envelope_error = _extract_envelope(raw_output, policy)
    if envelope_error or not candidate:
        return result("input_rejected", category="input", error=ValueError())
    if policy.reject_multiple_objects and _has_multiple_values(candidate):
        return result("input_rejected", category="input", error=ValueError())
    parsed = None
    try:
        parsed = _strict_load(candidate, reject_duplicates=policy.reject_duplicate_keys)
    except _DuplicateKey as error:
        return result("input_rejected", category="input", error=error)
    except (ValueError, RecursionError):
        pass  # Pydantic supplies the stable syntax classification below.
    if parsed is not None:
        try:
            _check_shape(parsed, policy)
        except (ValueError, RecursionError) as safety_error:
            return result("input_rejected", category="input", error=safety_error)
    try:
        validated = target_model.model_validate_json(candidate)
    except ValidationError as first_error:
        first_category = _category(first_error)
        if first_category == "schema":
            return result("schema_validation_failed", category="schema", error=first_error, first="schema")
        if not policy.enabled:
            return result("syntax_repair_failed", category="syntax", error=first_error, first="syntax")
        if size > policy.max_repair_input_bytes:
            return result("input_rejected", category="input", error=ValueError(), first="syntax")
        started = time.perf_counter()
        try:
            repaired = _repair_json_once(candidate)
        except Exception as repair_error:
            elapsed = (time.perf_counter() - started) * 1000
            return result("syntax_repair_failed", attempted=True, category="library", error=repair_error,
                          first="syntax", elapsed=elapsed)
        elapsed = (time.perf_counter() - started) * 1000
        if not isinstance(repaired, str) or not repaired:
            return result("syntax_repair_failed", attempted=True, category="library", error=TypeError(),
                          first="syntax", elapsed=elapsed)
        if len(repaired.encode("utf-8", "surrogatepass")) > policy.max_input_bytes:
            return result("syntax_repair_failed", attempted=True, category="syntax", error=ValueError(),
                          repaired=repaired, first="syntax", elapsed=elapsed)
        if policy.reject_multiple_objects and _has_multiple_values(repaired):
            return result("syntax_repair_failed", attempted=True, category="input", error=ValueError(),
                          repaired=repaired, first="syntax", elapsed=elapsed)
        try:
            repaired_value = _strict_load(repaired, reject_duplicates=policy.reject_duplicate_keys)
            _check_shape(repaired_value, policy)
        except (ValueError, RecursionError) as repaired_error:
            return result("syntax_repair_failed", attempted=True, category="input", error=repaired_error,
                          repaired=repaired, first="syntax", elapsed=elapsed)
        try:
            validated = target_model.model_validate_json(repaired)
        except ValidationError as second_error:
            second_category = _category(second_error)
            return result("schema_validation_failed" if second_category == "schema" else "syntax_repair_failed",
                          attempted=True, category=second_category, error=second_error, repaired=repaired,
                          first="syntax", second=second_category, elapsed=elapsed)
        except Exception as second_error:
            return result("validation_infrastructure_failed", attempted=True,
                          category="validation_infrastructure", error=second_error, repaired=repaired,
                          first="syntax", second="infrastructure", elapsed=elapsed)
        return result("repaired", model=validated, attempted=True, succeeded=True, repaired=repaired,
                      first="syntax", second="valid", elapsed=elapsed)
    except Exception as first_error:
        return result("validation_infrastructure_failed", category="validation_infrastructure",
                      error=first_error, first="infrastructure")
    return result("valid", model=validated, first="valid")
