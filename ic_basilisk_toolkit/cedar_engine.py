"""Fail-closed Cedar authorization engine parameterized by namespace and schema."""

from typing import Any, Callable, List, Optional

try:  # pragma: no cover - exercised by which artifact the canister is built on
    from ic_basilisk_toolkit import cedar as _cedar
    from ic_basilisk_toolkit.cedar import CedarError
except ImportError:  # pragma: no cover
    _cedar = None

    class CedarError(Exception):
        """Placeholder so callers can catch one exception type either way."""


from ic_basilisk_toolkit.cedar_slicing import Slicer


def _log(message: str) -> None:
    try:
        from ic_python_logging import get_logger

        get_logger(__name__).warning(message)
    except Exception:
        print(message)


class CedarEngine:
    """Load Cedar policies once and decide authorization requests fail-closed."""

    def __init__(
        self,
        namespace: str,
        principal_type: str,
        schema: str,
        policies: str,
        slicer: Slicer | None = None,
        context_provider: Callable[[], Any] | None = None,
        fail_open_when_unavailable: bool = False,
    ) -> None:
        self.namespace = namespace
        self.principal_type = principal_type
        self.schema = schema
        self.policies = policies
        self.slicer = slicer or Slicer(namespace, schema, principal_type)
        self.context_provider = context_provider
        self.fail_open_when_unavailable = fail_open_when_unavailable
        self._loaded = False
        self._attempted = False
        self._warnings: List[str] = []
        self._error = ""
        self._actions_cache: Optional[frozenset] = None
        self._extra_policies = ""

    def available(self) -> bool:
        """Whether the native Cedar module exists in this build."""
        if _cedar is None:
            return False
        try:
            import _basilisk_cedar  # noqa: F401
        except ImportError:
            return False
        return True

    def enabled(self) -> bool:
        """Whether Cedar is actually deciding calls in this deployment."""
        return self._loaded

    def load(self, extra_policies: str = "") -> bool:
        """Parse schema and policies. Fail-closed on unavailable or CedarError."""
        self._attempted = True
        if not self.available():
            self._error = "no native Cedar module in this build"
            return False

        policies = self.policies
        if extra_policies:
            policies = f"{policies}\n\n{extra_policies}"

        try:
            warnings = _cedar.load(self.schema, policies)
        except CedarError as exc:
            self._error = str(exc)
            _log(f"cedar_engine: policies rejected: {exc}")
            return False

        self._loaded = True
        self._error = ""
        self._extra_policies = extra_policies
        self._warnings = list(warnings or ())
        for warning in self._warnings:
            _log(f"cedar_engine: {warning}")
        return True

    def status(self) -> dict:
        """What the authorizer is doing, for an operator who needs to know."""
        return {
            "available": self.available(),
            "enforcing": self.enabled(),
            "attempted": self._attempted,
            "error": self._error,
            "warnings": list(self._warnings),
            "has_extra_policies": bool(self._extra_policies),
        }

    def extra_policies(self) -> str:
        """Custom policy text last loaded successfully (empty if defaults only)."""
        return self._extra_policies

    def effective_policies(self) -> str:
        """Full Cedar policy source currently loaded (base + extra)."""
        if self._extra_policies:
            return f"{self.policies}\n\n{self._extra_policies}"
        return self.policies

    def snapshot(self) -> dict:
        """Schema, policy sources, and engine status for introspection endpoints."""
        return {
            **self.status(),
            "namespace": self.namespace,
            "principal_type": self.principal_type,
            "schema": self.schema,
            "base_policies": self.policies,
            "extra_policies": self._extra_policies,
            "policies": self.effective_policies(),
        }

    def declared_actions(self) -> frozenset:
        """Action names declared in the schema."""
        if self._actions_cache is not None:
            return self._actions_cache
        actions = set()
        for line in self.schema.splitlines():
            line = line.strip()
            if not line.startswith("action "):
                continue
            rest = line[len("action ") :].split(" in ")[0].split(";")[0].strip()
            if rest.startswith('"'):
                end = rest.find('"', 1)
                name = rest[1:end] if end != -1 else rest.strip('"')
            else:
                name = rest.split()[0] if rest else ""
            if name:
                actions.add(name)
        self._actions_cache = frozenset(actions)
        return self._actions_cache

    def is_authorized(
        self,
        principal_id: str,
        action: str,
        resource_type: str = "",
        resource_id: str = "",
        resource_row=None,
        entities: Optional[List[dict]] = None,
    ) -> bool:
        """Decide one call. Denies on any failure."""
        if not self.enabled():
            return False

        if entities is None:
            entities = self.slicer.slice_for(
                principal_id, resource_type, resource_id, resource_row
            )

        resource = (
            self.slicer.uid(resource_type, resource_id)
            if resource_type and resource_id
            else f'{self.namespace}::{self.namespace}::"{self.namespace.lower()}"'
        )

        context = self.context_provider() if self.context_provider else None

        try:
            return _cedar.is_authorized(
                self.slicer.uid(self.principal_type, principal_id),
                f'{self.namespace}::Action::"{action}"',
                resource,
                entities,
                context,
            )
        except CedarError as exc:
            _log(f"cedar_engine: decision failed for {action}: {exc}")
            return False

    def check(
        self,
        principal_id: str,
        action: str,
        resource_type: str = "",
        resource_id: str = "",
        resource_row=None,
        entities: Optional[List[dict]] = None,
    ) -> None:
        """Raise ``PermissionError`` unless the call is authorized."""
        if not self.enabled():
            if self.fail_open_when_unavailable:
                return
            raise PermissionError("Cedar enforcement is not enabled")
        if not self.is_authorized(
            principal_id,
            action,
            resource_type,
            resource_id,
            resource_row,
            entities=entities,
        ):
            raise PermissionError(f"'{action}' denied by policy")

    def require_enforcement(self) -> None:
        """Refuse to run without Cedar."""
        if not self.enabled():
            raise RuntimeError(
                "Cedar enforcement required but unavailable: "
                f"{self._error or 'load() was never called'}. Build the canister "
                "on the Cedar template artifact."
            )
