"""Kedro hooks that feed per-node progress back to the pipeline worker.

The worker wraps ``session.run`` with stdout capture for logs (SSE); these
hooks only update ``current_step`` / ``progress`` on the Redis job hash. A
thread-local context lets multiple concurrent jobs coexist without colliding.
"""
from __future__ import annotations

import threading
from typing import Callable

from kedro.framework.hooks import hook_impl

_ctx = threading.local()


def set_job_context(
    job_id: str,
    total: int,
    update: Callable[..., None],
    append_log: Callable[[str, str], None],
) -> None:
    """Install the active job context for the current thread before run()."""
    _ctx.job_id = job_id
    _ctx.total = max(total, 1)
    _ctx.update = update
    _ctx.append_log = append_log
    _ctx.completed = 0


def clear_job_context() -> None:
    for attr in ("job_id", "total", "update", "append_log", "completed"):
        if hasattr(_ctx, attr):
            delattr(_ctx, attr)


class RedisProgressHook:
    """Updates Redis job progress/current_step as each node runs."""

    @hook_impl
    def before_node_run(self, node, catalog, inputs, is_async, session_id):  # noqa: PLR0913
        if not hasattr(_ctx, "update"):
            return
        _ctx.update(
            _ctx.job_id,
            current_step=node.name,
            progress=str(int(100 * _ctx.completed / _ctx.total)),
        )
        if _ctx.append_log:
            _ctx.append_log(_ctx.job_id, f"=== Node: {node.name} ===")

    @hook_impl
    def after_node_run(self, node, catalog, inputs, outputs, is_async, session_id):  # noqa: PLR0913
        if not hasattr(_ctx, "update"):
            return
        _ctx.completed = getattr(_ctx, "completed", 0) + 1
        _ctx.update(
            _ctx.job_id,
            progress=str(int(100 * _ctx.completed / _ctx.total)),
        )

    @hook_impl
    def on_node_error(self, error, node, catalog, inputs, is_async, session_id):  # noqa: PLR0913
        if hasattr(_ctx, "append_log"):
            _ctx.append_log(_ctx.job_id, f"ERROR in node {node.name}: {error}")
