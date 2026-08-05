"""Contracts for deterministic structured-output recovery."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StructuredOutputPolicy(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    enabled: bool = True
    library: Literal["json_repair"] = "json_repair"
    max_input_bytes: int = Field(default=1024 * 1024, ge=1)
    max_repair_input_bytes: int = Field(default=256 * 1024, ge=1)
    max_attempts: Literal[1] = 1
    reject_duplicate_keys: bool = True
    reject_multiple_objects: bool = True
    allow_markdown_fence_extraction: bool = True
    max_nesting_depth: int = Field(default=32, ge=1)
    max_object_keys: int = Field(default=10000, ge=1)
    max_array_elements: int = Field(default=10000, ge=1)
    repair_time_warning_ms: int | None = Field(default=250, ge=1)
    max_error_summary_characters: int = Field(default=500, ge=32)
    secure_debug_raw_output: bool = False
    fail_closed_for_authoritative_state: bool = True

    @model_validator(mode="after")
    def repair_limit_cannot_exceed_input_limit(self):
        if self.max_repair_input_bytes > self.max_input_bytes:
            raise ValueError("max_repair_input_bytes cannot exceed max_input_bytes")
        if not self.reject_duplicate_keys or not self.reject_multiple_objects:
            raise ValueError("duplicate keys and multiple JSON values must be rejected")
        if not self.fail_closed_for_authoritative_state:
            raise ValueError("authoritative structured state must fail closed")
        return self

    @classmethod
    def from_config(cls, config: Any) -> "StructuredOutputPolicy":
        """Convert engine configuration at one mandatory validated boundary."""
        names = (
            "enabled", "library", "max_input_bytes", "max_repair_input_bytes", "max_attempts",
            "reject_duplicate_keys", "reject_multiple_objects", "allow_markdown_fence_extraction",
            "max_nesting_depth", "max_object_keys", "max_array_elements", "repair_time_warning_ms",
            "max_error_summary_characters", "secure_debug_raw_output", "fail_closed_for_authoritative_state",
        )
        return cls.model_validate({name: getattr(config, name) for name in names})


class RecoveryDiagnostics(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    target_model: str
    first_validation_category: Literal["not_attempted", "valid", "syntax", "schema", "infrastructure"]
    second_validation_category: Literal["not_attempted", "valid", "syntax", "schema", "infrastructure"]
    envelope_extracted: bool = False
    repair_attempts: int = Field(default=0, ge=0, le=1)
    elapsed_repair_ms: float | None = Field(default=None, ge=0)
    repair_time_warning: bool = False
    raw_output_included: bool = False


class StructuredOutputResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True, frozen=True)

    status: Literal[
        "valid", "repaired", "syntax_repair_failed", "schema_validation_failed", "input_rejected",
        "validation_infrastructure_failed"
    ]
    validated_model: BaseModel | None = None
    repair_attempted: bool = False
    repair_succeeded: bool = False
    failure_category: Literal["syntax", "schema", "input", "library", "validation_infrastructure"] | None = None
    error_summary: str | None = None
    original_content_hash: str
    repaired_content_hash: str | None = None
    repair_method: Literal["json_repair"] | None = None
    diagnostics: RecoveryDiagnostics
    secure_debug_raw_output: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status in {"valid", "repaired"} and self.validated_model is not None
