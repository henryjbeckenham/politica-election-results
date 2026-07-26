from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DatasetSelectionRequest(BaseModel):
    adapter_id: str = Field(min_length=1)
    dataset_key: str = Field(min_length=1)


class MappingResolutionRequest(BaseModel):
    resolution_type: Literal["matched", "not_applicable"] = "matched"
    canonical_id: str | None = None
    notes: str | None = Field(default=None, max_length=2_000)
    resolved_by: str = Field(default="Local operator", min_length=1, max_length=200)

    @model_validator(mode="after")
    def canonical_id_required_for_match(self) -> "MappingResolutionRequest":
        if self.resolution_type == "matched" and not self.canonical_id:
            raise ValueError("canonical_id is required when resolution_type is matched")
        if self.resolution_type == "not_applicable" and self.canonical_id:
            raise ValueError("canonical_id must be empty when resolution_type is not_applicable")
        return self


class PublishRequest(BaseModel):
    approved_by: str = Field(min_length=1, max_length=200)
    snapshot_name: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4_000)


class ValidationRequest(BaseModel):
    requested_by: str = Field(default="Local operator", min_length=1, max_length=200)
