"""Shared Pydantic policies, constrained types, and reusable validators."""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue


class OpenAIRequestModel(BaseModel):
    """Tolerant top-level boundary for the supported OpenAI request subset."""

    model_config = ConfigDict(strict=True, extra="ignore", str_strip_whitespace=True)


class OpenAIMessageModel(BaseModel):
    """Strict string-message subset; tool and multimodal fields are unsupported."""

    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class InternalStrictModel(BaseModel):
    """Strict internal contract that rejects unknown fields and assignment errors."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


NonEmptyText = Annotated[str, Field(min_length=1)]
ShortText = Annotated[str, Field(min_length=1, max_length=256)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveCharacterId = Annotated[int, Field(gt=0)]
PositiveLocationId = Annotated[int, Field(gt=0)]

RegistryIdentifier = Annotated[
    str,
    Field(
        min_length=3,
        max_length=128,
        pattern=r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$",
    ),
]
NamespaceIdentifier = RegistryIdentifier
SubjectIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
]
SubjectTypeIdentifier = RegistryIdentifier

FactType = Literal["world_fact", "belief_fact", "rumor_fact"]
FactSourceType = Literal["user", "narrative", "system", "rules"]

MAX_PATCH_DEPTH = 5
MAX_PATCH_KEYS = 100
MAX_PATCH_BYTES = 32 * 1024
MAX_PATCH_KEY_LENGTH = 128


def validate_bounded_json_value(value: Any, *, require_object: bool = False) -> Any:
    """Validate a bounded, JSON-native extension patch without coercion.

    The root mapping has depth zero. Each nested mapping or list increments the
    depth. Key count is cumulative across every mapping in the patch.
    """

    if require_object and type(value) is not dict:
        raise ValueError("patch must be a JSON object")

    key_count = 0

    def walk(node: Any, depth: int, path: str) -> None:
        nonlocal key_count
        if depth > MAX_PATCH_DEPTH:
            raise ValueError(
                f"{path} exceeds maximum nesting depth {MAX_PATCH_DEPTH}"
            )

        if type(node) is dict:
            for key, child in node.items():
                if type(key) is not str:
                    raise ValueError(f"{path} contains a non-string key")
                if not key.strip():
                    raise ValueError(f"{path} contains an empty key")
                if len(key) > MAX_PATCH_KEY_LENGTH:
                    raise ValueError(
                        f"{path} contains a key longer than "
                        f"{MAX_PATCH_KEY_LENGTH} characters"
                    )
                if key.startswith(("_", "$")):
                    raise ValueError(f"{path}.{key} uses a reserved key prefix")
                key_count += 1
                if key_count > MAX_PATCH_KEYS:
                    raise ValueError(
                        f"patch exceeds maximum total key count {MAX_PATCH_KEYS}"
                    )
                walk(child, depth + 1, f"{path}.{key}")
        elif type(node) is list:
            for index, child in enumerate(node):
                walk(child, depth + 1, f"{path}[{index}]")
        elif node is None or type(node) in {str, bool, int}:
            return
        elif type(node) is float:
            if not math.isfinite(node):
                raise ValueError(f"{path} contains a non-finite JSON number")
        else:
            raise ValueError(
                f"{path} contains non-JSON value {type(node).__name__}"
            )

    walk(value, depth=0, path="$")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"patch is not JSON-compatible: {error}") from error
    if len(encoded) > MAX_PATCH_BYTES:
        raise ValueError(
            f"patch exceeds maximum serialized size {MAX_PATCH_BYTES} bytes"
        )
    return value


def validate_bounded_json_patch(value: Any) -> Any:
    """Validate a bounded JSON object used for shallow object merges."""

    return validate_bounded_json_value(value, require_object=True)


BoundedJsonPatch = Annotated[
    dict[str, JsonValue],
    BeforeValidator(validate_bounded_json_patch),
]

BoundedJsonValue = Annotated[JsonValue, BeforeValidator(validate_bounded_json_value)]
