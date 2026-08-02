"""
Basilisk Toolkit — Services and tools for IC Python canisters.

Provides ready-made abstractions on top of the Basilisk CDK:

  - Task/process management (Task, TaskStep, TaskSchedule, TaskManager)
  - Wallet (ICRC-1 token registry, transfers, balance tracking)
  - Filesystem (in-memory POSIX fs via frozen_stdlib_preamble)
  - Persistent storage (via ic-python-db entity ORM)
  - Encryption (vetKeys + per-principal envelopes + groups)
  - Logging (via ic-python-logging)
  - Interactive shell, SFTP, and SSHD for canister access

Canister-side code: entities and task_manager run *inside* the canister.
Client-side code: shell, sshd, sftp run on the developer machine.
"""

__version__ = "0.5.3"

__all__ = [
    # Status enums
    "TaskStatus",
    "TaskExecutionStatus",
    # Task entities
    "Codex",
    "Call",
    "Task",
    "TaskStep",
    "TaskSchedule",
    "TaskExecution",
    # Wallet entities
    "Token",
    "WalletBalance",
    "WalletTransfer",
    # FX entities
    "FXPair",
    # Wallet
    "Wallet",
    # FX service
    "FXService",
    # VetKey service
    "VetKeyService",
    # Crypto entities & service
    "KeyEnvelope",
    "CryptoGroup",
    "CryptoGroupMember",
    "CryptoService",
    "EncryptedString",
    # Task manager
    "TaskManager",
    # Execution
    "run_code",
    "create_task_entity_class",
    # Utilities (no canister deps — always importable)
    "PRNG",
    "date_utils",
]

from . import date_utils  # noqa: F401

# Utilities that have no canister-only dependencies — always importable.
from .prng import PRNG  # noqa: F401

# These imports will only work inside a canister (they depend on ic-python-db).
# When used client-side (e.g. in tests), import individual modules directly.
try:
    from .crypto import (
        CryptoGroup,
        CryptoGroupMember,
        CryptoService,
        EncryptedString,
        KeyEnvelope,
    )
    from .entities import (
        Call,
        Codex,
        FXPair,
        Task,
        TaskExecution,
        TaskSchedule,
        TaskStep,
        Token,
        WalletBalance,
        WalletTransfer,
    )
    from .execution import create_task_entity_class, run_code
    from .fx import FXService
    from .status import TaskExecutionStatus, TaskStatus
    from .task_manager import TaskManager
    from .vetkeys import VetKeyService
    from .wallet import Wallet
except ImportError:
    pass
