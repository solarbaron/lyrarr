"""Audit & Repair (PLAN §4): detect and repair bad state left by older versions.

Public entry points: start_run, cancel_run, run_bootstrap, run_scheduled and
REGISTRY (every check, see checks/). Importing the package has no side
effects beyond registering the checks; the database is used lazily.
"""

from . import checks  # noqa: F401  (registers the checks)
from .registry import REGISTRY, CheckSpec, Issue, RepairResult
from .runner import (
    EVIDENCE_CHECKS,
    LEGACY_ENDPOINT_CHECKS,
    cancel_run,
    is_running,
    run_bootstrap,
    run_scheduled,
    running_info,
    start_run,
)

__all__ = ['REGISTRY', 'CheckSpec', 'Issue', 'RepairResult', 'EVIDENCE_CHECKS', 'LEGACY_ENDPOINT_CHECKS',
           'cancel_run', 'is_running', 'run_bootstrap', 'run_scheduled', 'running_info', 'start_run']
