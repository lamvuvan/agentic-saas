"""Pydantic v2 models for Tool Registry v1."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ApiBlock(BaseModel):
    """HTTP dispatch configuration for a tool."""

    method: str = Field(..., pattern="^(GET|POST|PUT|DELETE|PATCH)$")
    url: str  # May contain ${ENV_VAR} placeholders — resolved at load time
    params: dict[str, str] | None = None  # GET query param mapping: tool_param → backend_param
    body_mapping: dict[str, str] | None = None  # POST body mapping: tool_param → backend_field
    item_mapping: dict[str, str] | None = None  # Mapping for array item fields
    response_path: str | None = None  # Top-level key to extract from response
    response_rename: dict[str, str] | None = None  # Rename response keys: old → new
    response_fields: list[str] | None = None  # Whitelist of fields to return


class HandlerRef(BaseModel):
    """Reference to a registered Python handler function."""

    name: str = Field(..., min_length=1)


class ToolDefinition(BaseModel):
    """A callable tool definition loaded from tools.yaml."""

    name: str  # Format: namespace__verb (double underscore)
    namespace: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object
    dispatch: ApiBlock | HandlerRef

    @field_validator("name")
    @classmethod
    def name_must_have_double_underscore(cls, v: str) -> str:
        if "__" not in v:
            raise ValueError(f"Tool name '{v}' must follow namespace__verb format (double underscore required)")
        return v

    @field_validator("parameters")
    @classmethod
    def parameters_must_be_object_schema(cls, v: dict) -> dict:
        if v.get("type") != "object":
            raise ValueError("parameters.type must be 'object'")
        return v


class ExecutionRequest(BaseModel):
    """Request body for POST /tools/{name}/execute."""

    params: dict[str, Any] = Field(default_factory=dict)


class ExecutionResponse(BaseModel):
    """Response from POST /tools/{name}/execute — success or error variant."""

    tool_name: str
    result: Any | None = None  # list | dict on success
    error: str | None = None  # English error message on failure
    error_code: str | None = None  # TOOL_NOT_FOUND | TIMEOUT | BACKEND_ERROR | VALIDATION_ERROR | HANDLER_ERROR
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result_or_error(self) -> ExecutionResponse:
        if self.error is None and self.result is None and not self.metadata.get("backend_status"):
            # Allow empty result (e.g., empty list) — only enforce error_code when error is set
            pass
        if self.error is not None and self.error_code is None:
            raise ValueError("error_code is required when error is set")
        return self


class HealthResponse(BaseModel):
    """Response from GET /health."""

    status: str  # "healthy" | "degraded"
    tool_count: int = 0
    config_path: str = ""
    last_reload: str | None = None  # ISO 8601 timestamp
    error: str | None = None  # Present only when status == "degraded"
