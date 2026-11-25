"""
Basilisk Toolkit — Status values for task/process management.

These statuses are used by the task execution system inside the canister.
Only task-related statuses are included here; application-level statuses
(e.g. TradeStatus, DisputeStatus) belong in the application layer (realms).

They are plain strings, not ``Enum`` members: they are stored in ``String``
fields, which refuse an ``Enum`` member under CPython. In a canister,
basilisk's ``enum`` stand-in made the members plain strings, so the same code
behaved differently there and in tests.
"""


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskExecutionStatus:
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
