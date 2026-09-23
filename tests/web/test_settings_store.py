"""Secrets encrypt with ``JEV_SECRET_KEY`` when set, so every instance agrees."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


def test_env_key_is_used_instead_of_a_key_file(tmp_path, monkeypatch):
    from jev_web import config
    from jev_web.services import settings_store

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("JEV_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JEV_SECRET_KEY", key)
    config.get_settings.cache_clear()
    try:
        token = settings_store._fernet().encrypt(b"sk-test")
        assert Fernet(key.encode()).decrypt(token) == b"sk-test"
        assert not (tmp_path / "secret.key").exists()
    finally:
        config.get_settings.cache_clear()


def test_key_file_is_generated_without_env_key(tmp_path, monkeypatch):
    from jev_web import config
    from jev_web.services import settings_store

    monkeypatch.setenv("JEV_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JEV_SECRET_KEY", raising=False)
    config.get_settings.cache_clear()
    try:
        settings_store._fernet()
        assert (tmp_path / "secret.key").exists()
    finally:
        config.get_settings.cache_clear()
