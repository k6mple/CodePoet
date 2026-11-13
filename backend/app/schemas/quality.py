"""Versioned, bounded quality evidence; scores are uncalibrated and remain null."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.ir import NodeId

QualityStatus = Literal['pass', 'warn', 'fail', 'not_applicable', 'unavailable']
MetricName = Literal['parameterValidity', 'layoutConflict', 'colorSemantic', 'signalNoise']


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    message: str = Field(max_length=500)
    nodeIds: list[NodeId] = Field(default_factory=list, max_length=200)
    timeMs: int | None = Field(default=None, ge=0, le=15000)
    value: float | None = None
    details: dict[str, float | int | str | None] = Field(default_factory=dict, max_length=12)


class Metric(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: MetricName
    status: QualityStatus
    score: float | None = Field(default=None, ge=0, le=100)
    value: float | None = None
    method: str = Field(max_length=1000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=25)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(max_length=80)
    message: str = Field(max_length=500)
    nodeIds: list[NodeId] = Field(default_factory=list, max_length=200)
    timeMs: int | None = Field(default=None, ge=0, le=15000)


class Assessment(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    algorithmVersion: str = Field(max_length=80)
    status: QualityStatus
    evaluatedTimesMs: list[int] = Field(max_length=3)
    thresholds: dict[str, float | int] = Field(max_length=12)
    metrics: list[Metric] = Field(min_length=4, max_length=4)
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=10)
