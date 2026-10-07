"""Operational repair-pressure context (frozen XGBoost model).

This package is the only runtime ML implementation. The research code that trained the model lives in ml_model/;
the backend serves a verified copy of the frozen artifact. ML is contextual: it does not feed the priority score,
the dispatch queue, the FIFO comparison or the optimisation plan.
"""
from .model_store import ml_model
from .regime import get_regime

__all__ = ["ml_model", "get_regime"]
