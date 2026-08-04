"""Universal, campaign-neutral contracts for deterministic JSON state patches."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import AfterValidator, Field, model_validator

from .common import (
    BoundedJsonPatch,
    BoundedJsonValue,
    InternalStrictModel,
    MAX_PATCH_BYTES,
    NamespaceIdentifier,
    RegistryIdentifier,
    SubjectIdentifier,
    SubjectTypeIdentifier,
    validate_bounded_json_value,
)

RESERVED_NAMESPACES = frozenset({"engine.internal", "engine.schema", "engine.sql"})


def validate_path_segment(value: str) -> str:
    if value.startswith(("_", "$")) or value in {".", ".."}:
        raise ValueError("state path segment uses a reserved value or prefix")
    if any(character in value for character in ". /\\"):
        raise ValueError("state path segment contains a path separator")
    return value


StatePathSegment = Annotated[
    str,
    Field(min_length=1, max_length=128),
    AfterValidator(validate_path_segment),
]
StatePath = Annotated[list[StatePathSegment], Field(min_length=1, max_length=16)]


def canonical_json(value: object) -> str:
    """Return the canonical representation used for comparisons and set members."""

    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


class EntityReference(InternalStrictModel):
    entity_kind: RegistryIdentifier
    entity_id: SubjectIdentifier


class StateTarget(InternalStrictModel):
    namespace: NamespaceIdentifier
    subject_type: SubjectTypeIdentifier
    subject_id: SubjectIdentifier

    @model_validator(mode="after")
    def reject_reserved_namespace(self):
        if self.namespace in RESERVED_NAMESPACES or self.namespace.startswith("engine.internal."):
            raise ValueError("namespace is reserved for internal engine state")
        return self


class ExpectedValue(InternalStrictModel):
    """Explicit optimistic comparison; the wrapped value may itself be JSON null."""

    value: BoundedJsonValue


class ExpectedObject(InternalStrictModel):
    """Explicit optimistic comparison for an object-valued merge target."""

    value: BoundedJsonPatch


class StateOperationBase(InternalStrictModel):
    path: StatePath


class SetValue(StateOperationBase):
    op: Literal["set"]
    value: BoundedJsonValue
    expected: ExpectedValue | None = None


class RemoveValue(StateOperationBase):
    op: Literal["remove"]
    expected: ExpectedValue | None = None
    missing_ok: bool = False


class MergeObject(StateOperationBase):
    """Shallow object merge; nested changes require separate ordered operations."""

    op: Literal["merge_object"]
    value: BoundedJsonPatch
    expected: ExpectedObject | None = None


class AddSetMember(StateOperationBase):
    op: Literal["add_set_member"]
    member: BoundedJsonValue


class RemoveSetMember(StateOperationBase):
    op: Literal["remove_set_member"]
    member: BoundedJsonValue
    missing_ok: bool = False


StateOperation: TypeAlias = Annotated[
    SetValue | RemoveValue | MergeObject | AddSetMember | RemoveSetMember,
    Field(discriminator="op"),
]


class StatePatch(InternalStrictModel):
    target: StateTarget
    base_revision: Annotated[int, Field(ge=0)] | None = None
    operations: Annotated[list[StateOperation], Field(min_length=1, max_length=100)]
    idempotency_key: UUID

    @model_validator(mode="after")
    def validate_aggregate_payload(self):
        submitted: list[object] = []
        for operation in self.operations:
            if isinstance(operation, (SetValue, MergeObject)):
                submitted.append(operation.value)
            elif isinstance(operation, (AddSetMember, RemoveSetMember)):
                submitted.append(operation.member)
            if isinstance(operation, (SetValue, RemoveValue, MergeObject)) and operation.expected:
                submitted.append(operation.expected.value)
        validate_bounded_json_value(submitted)
        if len(canonical_json(self.model_dump(mode="json")).encode("utf-8")) > MAX_PATCH_BYTES:
            raise ValueError(f"state patch exceeds maximum serialized size {MAX_PATCH_BYTES} bytes")
        return self


class StatePatchConflict(ValueError):
    """Raised when revision, expected-value, or required-presence checks fail."""


def apply_state_patch(document: dict, revision: int, patch: StatePatch) -> tuple[dict, int]:
    """Apply a validated patch atomically to a copy and return its next revision."""

    if patch.base_revision is not None and patch.base_revision != revision:
        raise StatePatchConflict("base revision does not match current revision")
    result = deepcopy(document)

    def parent_and_key(path: StatePath, *, create: bool = False):
        parent = result
        for segment in path[:-1]:
            if segment not in parent:
                if not create:
                    raise StatePatchConflict(f"missing path segment: {segment}")
                parent[segment] = {}
            if type(parent[segment]) is not dict:
                raise StatePatchConflict(f"path segment is not an object: {segment}")
            parent = parent[segment]
        return parent, path[-1]

    def check_expected(parent: dict, key: str, expected: ExpectedValue | ExpectedObject | None):
        if expected is None:
            return
        if key not in parent or canonical_json(parent[key]) != canonical_json(expected.value):
            raise StatePatchConflict("expected value does not match current state")

    for operation in patch.operations:
        create = isinstance(operation, SetValue)
        parent, key = parent_and_key(operation.path, create=create)
        if isinstance(operation, SetValue):
            check_expected(parent, key, operation.expected)
            parent[key] = deepcopy(operation.value)
        elif isinstance(operation, RemoveValue):
            if key not in parent:
                if operation.missing_ok:
                    continue
                raise StatePatchConflict("remove target is missing")
            check_expected(parent, key, operation.expected)
            del parent[key]
        elif isinstance(operation, MergeObject):
            if key not in parent or type(parent[key]) is not dict:
                raise StatePatchConflict("merge target must be an existing object")
            check_expected(parent, key, operation.expected)
            parent[key].update(deepcopy(operation.value))
        else:
            if key not in parent or type(parent[key]) is not list:
                raise StatePatchConflict("set-member target must be an existing array")
            wanted = canonical_json(operation.member)
            matching = [i for i, item in enumerate(parent[key]) if canonical_json(item) == wanted]
            if isinstance(operation, AddSetMember):
                if not matching:
                    parent[key].append(deepcopy(operation.member))
            elif matching:
                parent[key] = [item for item in parent[key] if canonical_json(item) != wanted]
            elif not operation.missing_ok:
                raise StatePatchConflict("set member is missing")
    canonical_json(result)
    return result, revision + 1
