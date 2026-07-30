"""Python API over the ``_basilisk_cedar`` native module.

The native module speaks JSON envelopes so that a failure is distinguishable
from a denial. This layer turns that back into ordinary Python: a decision is a
``bool``, and anything that went wrong is a :class:`CedarError`.

That distinction is the point. A malformed entity payload and a policy that says
no are the same outcome from the caller's seat — access refused — but only one of
them is a bug, and a deployment where every decision fails looks exactly like a
strict one until somebody checks.
"""

import json
from typing import Any, Dict, List, Optional, Union

_MISSING = (
    "_basilisk_cedar is unavailable. It exists only in a canister built on the "
    "Cedar template artifact; select it in dfx.json, or run authorization "
    "checks outside the canister with the cedar CLI."
)


class CedarError(Exception):
    """Cedar could not reach a decision, as distinct from deciding to deny."""


def _module():
    try:
        import _basilisk_cedar
    except ImportError as exc:
        raise CedarError(_MISSING) from exc
    return _basilisk_cedar


def _as_json(value: Union[str, Any, None]) -> Optional[str]:
    """Accept either pre-encoded JSON or a Python object."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _unwrap(raw: str) -> Dict[str, Any]:
    try:
        reply = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise CedarError(f"malformed reply from _basilisk_cedar: {raw!r}") from exc
    if not isinstance(reply, dict):
        raise CedarError(f"unexpected reply from _basilisk_cedar: {reply!r}")
    if "error" in reply:
        raise CedarError(reply["error"])
    return reply


def load(schema: str, policies: str) -> None:
    """Parse and validate a schema and policy set, holding them for later calls.

    Parsing dominates the cost of authorization, so this belongs at startup and
    never on a request path. Raises :class:`CedarError` if the policies do not
    parse or do not typecheck against the schema — which is how an extension
    shipping broken policies is refused at install rather than at decision time.
    """
    _unwrap(_module().load(schema, policies))


def is_authorized(
    principal: str,
    action: str,
    resource: str,
    entities: Union[str, list],
    context: Union[str, dict, None] = None,
) -> bool:
    """Decide a single request.

    Args:
        principal: Entity uid, e.g. ``'Realm::User::"alice"'``.
        action: Entity uid, e.g. ``'Realm::Action::"entity.get"'``.
        resource: Entity uid.
        entities: Cedar entity JSON, encoded or as a Python list.
        context: Facts about the request rather than the data — notably which
            extension a call originated from, if any.
    """
    reply = _unwrap(
        _module().is_authorized(
            principal,
            action,
            resource,
            _as_json(entities),
            _as_json(context) or "",
        )
    )
    return reply.get("decision") == "allow"


def authorize_many(
    principal: str,
    action: str,
    resources: Union[str, List[str]],
    entities: Union[str, list],
    context: Union[str, dict, None] = None,
) -> List[str]:
    """Filter ``resources`` down to those the principal may act on.

    This is the listing path. It crosses into Rust once and reuses one parsed
    entity store for every candidate row, rather than paying that cost per row.
    """
    reply = _unwrap(
        _module().authorize_many(
            principal,
            action,
            _as_json(resources),
            _as_json(entities),
            _as_json(context) or "",
        )
    )
    allowed = reply.get("allowed")
    if not isinstance(allowed, list):
        raise CedarError(f"expected an 'allowed' list, got {reply!r}")
    return allowed
