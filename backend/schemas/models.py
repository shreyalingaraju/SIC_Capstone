"""Response models: the stable API contract (see docs/api_contract.md)."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class Page(BaseModel):
    total_count: int
    page: int
    page_size: int
    total_pages: int


class OutageItem(BaseModel):
    outage_id: str
    borough: str
    police_precinct: Optional[str] = None
    location_desc: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    created_date: Optional[str] = None
    closed_date: Optional[str] = None
    outage_duration_hours: Optional[float] = None
    duration_days: Optional[float] = None
    scored: bool
    exclusion_reason: Optional[str] = None
    local_crime_rate: Optional[float] = None       # crimes/day within 250 m over the 14-day look-back
    priority_score: Optional[float] = None         # Stage 12 index, 0-100 (not a probability)
    priority_tier: Optional[str] = None            # High | Medium | Low (null when not scored)
    dispatch_status: Optional[str] = None          # recommended | deferred | not_scored
    optimization_rank: Optional[int] = None        # Stage 14 rank, recommended repairs only
    operator_note: str = "None"                    # session-local UI annotation, not part of the plan
    impact_index: Optional[float] = None           # Stage 14 objective coefficient (index points)


class OutageList(Page):
    items: List[OutageItem]


class ScoreDecomposition(BaseModel):
    tau_net: Optional[float] = None
    tau_net_source: Optional[str] = None
    local_crime_rate: Optional[float] = None
    lookback_days: int
    duration_factor: Optional[float] = None
    outage_duration_hours: Optional[float] = None
    raw_priority: Optional[float] = None
    priority_score: Optional[float] = None
    priority_tier: Optional[str] = None


class OutageDetail(OutageItem):
    incident_address: Optional[str] = None
    cross_street_1: Optional[str] = None
    cross_street_2: Optional[str] = None
    intersection_street_1: Optional[str] = None
    intersection_street_2: Optional[str] = None
    fifo_queue_rank: Optional[int] = None
    lightsafe_queue_rank: Optional[int] = None
    recent_crime_count_14d: Optional[int] = None
    decomposition: Optional[ScoreDecomposition] = None


class QueueItem(BaseModel):
    queue_rank: int
    repair_day: int
    outage_id: str
    borough: str
    location_desc: Optional[str] = None
    created_date: Optional[str] = None
    priority_score: Optional[float] = None
    priority_tier: Optional[str] = None
    impact_index: Optional[float] = None


class QueueList(Page):
    method: str
    items: List[QueueItem]


class PriorityResponse(OutageList):
    summary: Dict[str, Any]


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outage_id: str
    operator_note: str
    success: bool
