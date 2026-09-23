"""Application settings in the database; secrets encrypted at rest.

The key comes from ``JEV_SECRET_KEY``, or else lives in ``DATA_DIR/secret.key``
-- outside the database either way, so a copied database file does not carry
usable API keys with it.
"""

from __future__ import annotations

import os
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import AppSetting

#: key -> (default, is_secret)
DEFAULTS: dict[str, tuple[Any, bool]] = {
    "typesafe.api_key": (None, True),
    "typesafe.endpoint": ("https://api.typesafe.ai/v1/systemone", False),
    "typesafe.model": ("jev-latest", False),
    "judge.workers": (8, False),
    #: USD per million input tokens, for the cost estimate shown after a run.
    "judge.cost_per_mtok": (0.042, False),
}


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.secret_key:
        return Fernet(settings.secret_key.encode())
    path = settings.secret_key_path
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
        os.chmod(path, 0o600)
    return Fernet(path.read_bytes())


def get(session: Session, key: str) -> Any:
    row = session.get(AppSetting, key)
    default = DEFAULTS.get(key, (None, False))[0]
    if row is None or row.value is None:
        return default
    if row.is_secret:
        return _fernet().decrypt(str(row.value).encode()).decode()
    return row.value


def put(session: Session, key: str, value: Any, user_id: int | None = None) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}")
    secret = DEFAULTS[key][1]
    stored = value
    if secret and value not in (None, ""):
        stored = _fernet().encrypt(str(value).encode()).decode()
    elif secret:
        stored = None
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=stored, is_secret=secret, updated_by=user_id))
    else:
        row.value, row.is_secret, row.updated_by = stored, secret, user_id


def public_view(session: Session) -> dict[str, Any]:
    """Every setting, with secrets reduced to whether they are set."""
    out: dict[str, Any] = {}
    for key, (_default, secret) in DEFAULTS.items():
        value = get(session, key)
        if secret:
            out[key] = {"set": bool(value),
                        "hint": f"…{value[-4:]}" if value and len(value) > 8 else None}
        else:
            out[key] = value
    return out


def api_config(session: Session):
    """The Jev client configuration for a run.

    With no key stored here, the core client falls back to ``TYPESAFE_API_KEY``
    in the environment -- the same place the CLI reads it.
    """
    from jev_diff.judge.client import ApiConfig

    return ApiConfig(
        key=get(session, "typesafe.api_key") or None,
        endpoint=get(session, "typesafe.endpoint"),
        model=get(session, "typesafe.model"),
    )
