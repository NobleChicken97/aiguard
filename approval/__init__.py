"""Approval handler hierarchy and queue helpers.

The built-in handlers (CLI, AutoApprove, AutoDeny, Async) share the
``ApprovalHandler`` interface. ``get_pending_approvals`` and
``resolve_approval`` are the storage helpers used by both the queue UI
and the web polling flow. Phase 3 adds the pause/resume set
(``AsyncApprovalHandler``, ``ApprovalPending``, ``PendingApproval``,
pending-resume helpers) for the non-blocking web flow. The old blocking
``WebApprovalHandler`` poll loop was retired: holding a worker thread
for up to 300s per pending approval does not scale.
"""

from approval.gate import (
    ApprovalHandler,
    ApprovalPending,
    AsyncApprovalHandler,
    AutoApproveHandler,
    AutoDenyHandler,
    CLIApprovalHandler,
    PendingApproval,
    delete_pending_resume,
    get_approval_status,
    get_pending_approvals,
    load_pending_resume,
    owns_approval,
    resolve_approval,
    save_pending_resume,
)

__all__ = [
    "ApprovalHandler",
    "ApprovalPending",
    "AsyncApprovalHandler",
    "AutoApproveHandler",
    "AutoDenyHandler",
    "CLIApprovalHandler",
    "PendingApproval",
    "delete_pending_resume",
    "get_approval_status",
    "get_pending_approvals",
    "load_pending_resume",
    "owns_approval",
    "resolve_approval",
    "save_pending_resume",
]
