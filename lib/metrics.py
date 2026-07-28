"""Comparison metrics between a baseline route and a green route."""

from __future__ import annotations

from dataclasses import dataclass

from .routing import RouteResult


@dataclass
class Comparison:
    source: str
    target: str
    alpha: float
    baseline: RouteResult
    green: RouteResult
    carbon_saved_gco2: float
    pct_carbon_saved: float
    latency_added_ms: float
    pct_latency_added: float


def compare_routes(source: str, target: str, baseline: RouteResult, green: RouteResult) -> Comparison:
    carbon_saved = baseline.total_carbon_gco2_per_kwh - green.total_carbon_gco2_per_kwh
    pct_carbon_saved = (
        (carbon_saved / baseline.total_carbon_gco2_per_kwh * 100)
        if baseline.total_carbon_gco2_per_kwh
        else 0.0
    )
    latency_added = green.total_latency_ms - baseline.total_latency_ms
    pct_latency_added = (
        (latency_added / baseline.total_latency_ms * 100) if baseline.total_latency_ms else 0.0
    )
    return Comparison(
        source=source,
        target=target,
        alpha=green.alpha,
        baseline=baseline,
        green=green,
        carbon_saved_gco2=carbon_saved,
        pct_carbon_saved=pct_carbon_saved,
        latency_added_ms=latency_added,
        pct_latency_added=pct_latency_added,
    )
