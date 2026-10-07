"""All HTTP routes. Thin: validate parameters, delegate to services, return the contract shape."""
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Query

from .. import config
from ..schemas.models import ActionResult, OutageDetail, OutageList, PriorityResponse, QueueList
from ..services import (
    causal_service, dispatch_service, evidence_service, map_service, operations_service,
    optimization_service, outage_service,
    overview_service, pipeline_service, priority_service,
)
from ..services.data_store import store
from ..services.ml import get_regime

router = APIRouter(prefix="/api")

Page = Query(1, ge=1)
PageSize = Query(50, ge=1, le=config.MAX_PAGE_SIZE)
Dispatch = Literal["recommended", "deferred"]


@router.get("/health", tags=["Meta"])
def health() -> Dict[str, Any]:
    return {
        "status": "healthy" if not store.missing else "degraded",
        "service": "LightSafe Decision-Support API",
        "artifacts_loaded": store.loaded and not store.outages_df.empty,
        "missing_artifacts": store.missing,
    }


@router.get("/summary", tags=["Meta"])
def summary() -> Dict[str, Any]:
    """Pipeline provenance, artifact availability and the interpretation notice."""
    return pipeline_service.get_pipeline_status()


@router.get("/overview", tags=["Overview"])
def overview(borough: Optional[str] = None, priority_tier: Optional[str] = None) -> Dict[str, Any]:
    return overview_service.get_overview_data(borough=borough, priority_tier=priority_tier)


@router.get("/ml/regime", tags=["Operational context"])
def ml_regime(borough: Optional[str] = None) -> Dict[str, Any]:
    """Borough repair-pressure context (frozen model). Context only: not a crime forecast, not used for dispatch."""
    return get_regime(borough)


@router.get("/outages", response_model=OutageList, tags=["Outages"])
def list_outages(
    borough: Optional[str] = None,
    priority_tier: Optional[Literal["High", "Medium", "Low", "All"]] = None,
    search: Optional[str] = Query(None, max_length=100),
    dispatch_status: Optional[Dispatch] = None,
    min_score: Optional[float] = Query(None, ge=0, le=100),
    scope: Literal["scored", "excluded", "all"] = "scored",
    page: int = Page,
    page_size: int = PageSize,
):
    return outage_service.get_outages(
        borough=borough, priority_tier=priority_tier, search=search, dispatch_status=dispatch_status,
        min_score=min_score, scope=scope, page=page, page_size=page_size,
    )


@router.get("/outages/{outage_id}", response_model=OutageDetail, tags=["Outages"])
def outage_detail(outage_id: str = Path(..., max_length=40)):
    detail = outage_service.get_outage_by_id(outage_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Outage {outage_id} not found")
    return detail


@router.post("/outages/{outage_id}/{action}", response_model=ActionResult, tags=["Outages"])
def outage_action(outage_id: str = Path(..., max_length=40), action: Literal["approve", "defer", "flag", "reset"] = Path(...)):
    """Session-local operator annotation. It does not change the Stage 14 plan."""
    result = outage_service.update_outage_action(outage_id, action)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Outage {outage_id} not found")
    return result


@router.get("/priority", response_model=PriorityResponse, tags=["Priority"])
def priority(
    borough: Optional[str] = None,
    priority_tier: Optional[Literal["High", "Medium", "Low", "All"]] = None,
    search: Optional[str] = Query(None, max_length=100),
    dispatch_status: Optional[Dispatch] = None,
    min_score: Optional[float] = Query(None, ge=0, le=100),
    page: int = Page,
    page_size: int = PageSize,
):
    return priority_service.get_priority_queue(
        borough=borough, priority_tier=priority_tier, search=search, dispatch_status=dispatch_status,
        min_score=min_score, page=page, page_size=page_size,
    )


@router.get("/priority/summary", tags=["Priority"])
def priority_summary() -> Dict[str, Any]:
    return priority_service.get_priority_summary()


@router.get("/queue", response_model=QueueList, tags=["Dispatch simulation"])
def queue(
    method: Literal["FIFO", "LightSafe", "fifo", "lightsafe"] = "LightSafe",
    borough: Optional[str] = None,
    priority_tier: Optional[Literal["High", "Medium", "Low", "All"]] = None,
    page: int = Page,
    page_size: int = PageSize,
):
    return dispatch_service.get_queue(method, borough, priority_tier, page, page_size)


@router.get("/comparison", tags=["Dispatch simulation"])
def comparison(points: int = Query(150, ge=2, le=1000)) -> Dict[str, Any]:
    return dispatch_service.get_comparison(points)


@router.get("/optimization", tags=["Optimization"])
def optimization() -> Dict[str, Any]:
    return optimization_service.get_optimization_summary()


@router.get("/optimization/plan", response_model=OutageList, tags=["Optimization"])
def optimization_plan(
    decision: Optional[Dispatch] = None,
    borough: Optional[str] = None,
    page: int = Page,
    page_size: int = PageSize,
    action: Optional[Literal["none", "approved", "deferred", "flagged"]] = None,
):
    """`action` filters by the session operator action: "none" = not yet acted on, otherwise that status."""
    return optimization_service.get_optimization_plan(decision, borough, page, page_size, action)


@router.get("/map/outages", tags=["Map"])
def map_outages(
    borough: Optional[str] = None,
    priority_tier: Optional[Literal["High", "Medium", "Low", "All"]] = None,
    dispatch_status: Optional[Dispatch] = None,
    limit: int = Query(500, ge=1, le=config.MAX_MAP_POINTS),
) -> Dict[str, Any]:
    return map_service.get_map_outages(borough, priority_tier, dispatch_status, limit)


@router.get("/map/crimes", tags=["Map"])
def map_crimes(night_only: bool = True, limit: int = Query(600, ge=1, le=config.MAX_MAP_POINTS)) -> Dict[str, Any]:
    return map_service.get_map_crimes(night_only, limit)


@router.get("/map/buffers/{outage_id}", tags=["Map"])
def map_buffers(outage_id: str = Path(..., max_length=40)) -> Dict[str, Any]:
    rings = map_service.get_buffer_rings(outage_id)
    if rings is None:
        raise HTTPException(status_code=404, detail=f"Outage {outage_id} not found")
    return rings


@router.get("/causal/overview", tags=["Causal evidence"])
def causal_overview() -> Dict[str, Any]:
    return causal_service.get_causal_overview()


@router.get("/causal/event-study", tags=["Causal evidence"])
def causal_event_study() -> List[Dict[str, Any]]:
    return causal_service.get_event_study_data()


# ----------------------------------------------------------- evidence / operations explorer
@router.get("/evidence/stage11", tags=["Evidence explorer"])
def evidence_stage11() -> Dict[str, Any]:
    """Frozen Stage 11 estimates, ring definitions and the stored variant grid."""
    return evidence_service.get_stage11()


@router.get("/evidence/legacy", tags=["Evidence explorer"])
def evidence_legacy() -> Dict[str, Any]:
    """Retired Stage 7-10 pair design (provisional), with its ring/matching definitions."""
    return evidence_service.get_legacy_pair_design()


CapacityK = Query(65, ge=config.CAPACITY_MIN, le=config.CAPACITY_MAX)


@router.get("/operations/capacity", tags=["Operations explorer"])
def operations_capacity(k: int = CapacityK) -> Dict[str, Any]:
    """Re-run the frozen FIFO simulation at capacity k: metrics, service targets, daily series."""
    return operations_service.get_capacity(k)


@router.get("/operations/capacity-curve", tags=["Operations explorer"])
def operations_capacity_curve() -> Dict[str, Any]:
    return operations_service.get_capacity_curve()


@router.get("/operations/replay", tags=["Operations explorer"])
def operations_replay(k: int = CapacityK) -> Dict[str, Any]:
    """Per-job known/dispatch days and site coordinates for the city replay."""
    return operations_service.get_replay(k)
