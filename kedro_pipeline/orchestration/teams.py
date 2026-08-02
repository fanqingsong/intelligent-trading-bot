"""Cross-team tagging / deployment naming for Prefect jobs."""
from __future__ import annotations

import os
from typing import Any


def default_team() -> str:
    return (os.environ.get("ITB_TEAM") or "default").strip() or "default"


def default_env() -> str:
    return (os.environ.get("ITB_ENV") or os.environ.get("ENV") or "dev").strip() or "dev"


def configured_teams() -> list[str]:
    raw = os.environ.get("ITB_TEAMS") or default_team()
    teams = [t.strip() for t in raw.split(",") if t.strip()]
    return teams or ["default"]


def normalize_team(team: str | None) -> str:
    value = (team or default_team()).strip() or "default"
    allowed = set(configured_teams())
    if value not in allowed:
        # Unknown team still tagged, but routed to default deployment.
        return value
    return value


def job_kind(steps: list[str], config_overrides: dict[str, Any] | None) -> str:
    overrides = config_overrides or {}
    if any(s in ("predict_rolling", "simulate") for s in steps):
        return "backtest"
    if "train" in overrides:
        return "train" if overrides.get("train") else "predict"
    if "train" in steps:
        return "train"
    return "predict"


def build_run_tags(
    *,
    job_id: str,
    steps: list[str],
    config_overrides: dict[str, Any] | None,
    team: str,
) -> list[str]:
    overrides = config_overrides or {}
    symbol = str(overrides.get("symbol") or "").strip()
    kind = job_kind(steps, overrides)
    tags = [
        "itb",
        f"job:{job_id}",
        f"team:{normalize_team(team)}",
        f"env:{default_env()}",
        f"kind:{kind}",
    ]
    if symbol:
        tags.append(f"symbol:{symbol}")
    return tags


def deployment_name_for_team(team: str) -> str:
    """Map team → deployment name; unknown teams fall back to default."""
    normalized = normalize_team(team)
    if normalized in configured_teams():
        return normalized
    return "default"
