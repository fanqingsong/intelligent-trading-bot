"""Lightweight team RBAC for the BFF edge.

Disabled by default (``ITB_RBAC_ENABLED=0``). When enabled, callers must send:

* ``X-ITB-User`` — identity (for audit logs)
* ``X-ITB-Teams`` — comma-separated teams the caller may use
* optional ``X-ITB-Admin: 1`` — bypass team checks

Requested ``team`` must be in the caller's team list (unless admin).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request


def rbac_enabled() -> bool:
    raw = (os.environ.get("ITB_RBAC_ENABLED") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Caller:
    user: str
    teams: frozenset[str]
    admin: bool

    def allows(self, team: str | None) -> bool:
        if not rbac_enabled() or self.admin or "*" in self.teams:
            return True
        if not team:
            return True
        return team in self.teams


def parse_caller(
    *,
    x_itb_user: str | None = None,
    x_itb_teams: str | None = None,
    x_itb_admin: str | None = None,
) -> Caller:
    if not rbac_enabled():
        return Caller(user="anonymous", teams=frozenset({"*"}), admin=True)
    user = (x_itb_user or "").strip() or "anonymous"
    admin = (x_itb_admin or "").strip().lower() in ("1", "true", "yes", "on")
    teams = frozenset(t.strip() for t in (x_itb_teams or "").split(",") if t.strip())
    if not admin and not teams:
        raise HTTPException(
            403,
            "RBAC enabled: send X-ITB-Teams (comma-separated) or X-ITB-Admin: 1",
        )
    return Caller(user=user, teams=teams, admin=admin)


def require_team(caller: Caller, team: str | None) -> None:
    if caller.allows(team):
        return
    raise HTTPException(
        403,
        f"User {caller.user!r} is not allowed to use team {team!r}",
    )


async def caller_dep(
    x_itb_user: str | None = Header(None, alias="X-ITB-User"),
    x_itb_teams: str | None = Header(None, alias="X-ITB-Teams"),
    x_itb_admin: str | None = Header(None, alias="X-ITB-Admin"),
) -> Caller:
    return parse_caller(
        x_itb_user=x_itb_user,
        x_itb_teams=x_itb_teams,
        x_itb_admin=x_itb_admin,
    )


def team_from_request(request: Request, body_team: str | None = None) -> str | None:
    return body_team or request.query_params.get("team")
