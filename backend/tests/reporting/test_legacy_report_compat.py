"""Legacy report API compatibility regression test.

Phase 2 must not change which routes are mounted by the ``analysis`` router.
We assert the legacy report endpoints still exist in the router's route
table — full behavior is covered by the existing ``test_analysis_runs.py``
suite, which Phase 2 leaves untouched.
"""

from __future__ import annotations

from riskagent.api import analysis as analysis_api


def _route_paths() -> set[str]:
    return {route.path for route in analysis_api.router.routes}


def test_legacy_report_route_still_registered():
    paths = _route_paths()
    assert "/analysis-runs/{run_id}/report" in paths


def test_legacy_stream_report_route_still_registered():
    paths = _route_paths()
    assert "/analysis-runs/{run_id}/stream-report" in paths


def test_legacy_router_prefix_unchanged():
    assert analysis_api.router.prefix == "/analysis-runs"
