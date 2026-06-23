"""Smoke tests — cheap invariants that need no DB, env vars, or network.

Guards the two contracts most likely to silently rot: the 10 internal team
codes, and that every seed scenario routes to one of them. Kept dependency-free
(only pyyaml) so it runs in the pre-commit hook even without the app's runtime
deps installed; the code-drift cross-check runs only when those deps exist.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

# Canonical 10 internal team codes — mirror of analyzer/llm_client.py:ACTIONS.
EXPECTED_ACTIONS = {
    "OPS_WATCH", "OPS_DEEP",
    "SVC_TEAM_A", "SVC_TEAM_B",
    "INFRA_TEAM", "TENANT_OPS",
    "COMPUTE_OPS",
    "STORAGE_TEAM", "SEC_TEAM", "ON_CALL",
}


def test_exactly_ten_action_codes():
    assert len(EXPECTED_ACTIONS) == 10


def test_scenarios_yaml_actions_are_valid():
    data = yaml.safe_load((ROOT / "config" / "scenarios.yaml").read_text(encoding="utf-8"))
    scenarios = data["scenarios"]
    assert scenarios, "scenarios.yaml has no scenarios"
    bad = [s["id"] for s in scenarios if s.get("action") not in EXPECTED_ACTIONS]
    assert not bad, f"scenarios route to unknown action codes: {bad}"


def test_action_codes_match_source():
    """Cross-check the canonical set against the real ACTIONS. Skips when the
    app's runtime deps (pgvector, etc.) aren't installed in this env."""
    pytest.importorskip("pgvector", reason="app runtime deps not installed")
    from analyzer.llm_client import ACTIONS
    assert ACTIONS == EXPECTED_ACTIONS, "EXPECTED_ACTIONS drifted from llm_client.ACTIONS"
