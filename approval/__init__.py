"""Approval handler hierarchy and queue helpers.

The four built-in handlers (CLI, AutoApprove, AutoDeny, Web) share the
``ApprovalHandler`` interface. ``get_pending_approvals`` and
``resolve_approval`` are the storage helpers used by both the queue UI
and the web polling flow.
"""

from approval.gate import (
    ApprovalHandler,
    AutoApproveHandler,
    AutoDenyHandler,
    CLIApprovalHandler,
    WebApprovalHandler,
    get_pending_approvals,
    resolve_approval,
)

__all__ = [
    "ApprovalHandler",
    "AutoApproveHandler",
    "AutoDenyHandler",
    "CLIApprovalHandler",
    "WebApprovalHandler",
    "get_pending_approvals",
    "resolve_approval",
]
