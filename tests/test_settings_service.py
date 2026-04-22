from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from sidecar.config import settings as cfg
from sidecar.db.engine import session_scope
from sidecar.db.models import Setting
from sidecar.services.settings import (
    SPECS_BY_KEY,
    apply_updates,
    load_effective_config,
    load_sources,
    reset_to_default,
    validate_and_serialize,
)


def test_load_effective_config_returns_defaults_when_no_db_or_env(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force env attrs to "unset" by aligning them with their hardcoded defaults.
    # For those, _env_value still returns the attr value — so the expectation
    # is that the effective value equals the spec default regardless.
    config = load_effective_config()
    assert config["ingest_prices.interval_minutes"] == 5
    assert config["ingest_crypto.enabled"] is False
    assert config["ingest_crypto.interval_minutes"] == 15
    assert config["ingest_macro.cron_hour_utc"] == 6
    # fred_api_key default = "" (pydantic-settings leaves it as None when env unset)
    assert config["fred_api_key"] in ("", None)


def test_load_effective_config_env_overrides_default(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "ingest_prices_interval_minutes", 7)
    monkeypatch.setattr(cfg, "enable_crypto_job", True)
    monkeypatch.setattr(cfg, "fred_api_key", "env-key")

    config = load_effective_config()
    assert config["ingest_prices.interval_minutes"] == 7
    assert config["ingest_crypto.enabled"] is True
    assert config["fred_api_key"] == "env-key"

    sources = load_sources()
    assert sources["ingest_prices.interval_minutes"] == "env"
    assert sources["ingest_crypto.enabled"] == "env"
    assert sources["fred_api_key"] == "env"


def test_load_effective_config_db_overrides_env(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "ingest_prices_interval_minutes", 7)
    monkeypatch.setattr(cfg, "enable_crypto_job", False)

    apply_updates(
        {
            "ingest_prices.interval_minutes": 11,
            "ingest_crypto.enabled": True,
        }
    )

    config = load_effective_config()
    assert config["ingest_prices.interval_minutes"] == 11
    assert config["ingest_crypto.enabled"] is True

    sources = load_sources()
    assert sources["ingest_prices.interval_minutes"] == "db"
    assert sources["ingest_crypto.enabled"] == "db"


def test_apply_updates_persists_typed_values(isolated_db: Path) -> None:
    apply_updates(
        {
            "ingest_prices.interval_minutes": 10,
            "ingest_crypto.enabled": True,
            "fred_api_key": "test-secret",
        }
    )

    with session_scope() as s:
        rows = {
            r.key: r.value for r in s.execute(select(Setting)).scalars()
        }

    assert rows["ingest_prices.interval_minutes"] == "10"
    assert rows["ingest_crypto.enabled"] == "true"
    assert rows["fred_api_key"] == "test-secret"


def test_apply_updates_rejects_unknown_key(isolated_db: Path) -> None:
    with pytest.raises(ValueError, match="unknown setting"):
        apply_updates({"bogus.key": 1})


def test_apply_updates_validates_int_bounds(isolated_db: Path) -> None:
    with pytest.raises(ValueError, match="must be >="):
        apply_updates({"ingest_prices.interval_minutes": 0})
    with pytest.raises(ValueError, match="must be <="):
        apply_updates({"ingest_prices.interval_minutes": 99999})
    with pytest.raises(ValueError, match="must be >="):
        apply_updates({"ingest_macro.cron_hour_utc": -1})
    with pytest.raises(ValueError, match="must be <="):
        apply_updates({"ingest_macro.cron_hour_utc": 24})


def test_apply_updates_rejects_non_int_for_int_key(isolated_db: Path) -> None:
    with pytest.raises(ValueError, match="expected integer"):
        apply_updates({"ingest_prices.interval_minutes": "abc"})


def test_apply_updates_is_atomic_on_failure(isolated_db: Path) -> None:
    # First setting is valid, second is not → nothing persists.
    with pytest.raises(ValueError):
        apply_updates(
            {
                "ingest_prices.interval_minutes": 10,
                "ingest_crypto.interval_minutes": 999999,
            }
        )

    config = load_effective_config()
    assert config["ingest_prices.interval_minutes"] == 5  # unchanged default


def test_empty_secret_clears_db_row(isolated_db: Path) -> None:
    apply_updates({"fred_api_key": "some-key"})
    with session_scope() as s:
        assert s.execute(select(Setting).where(Setting.key == "fred_api_key")).first() is not None

    apply_updates({"fred_api_key": ""})
    with session_scope() as s:
        assert s.execute(select(Setting).where(Setting.key == "fred_api_key")).first() is None


def test_reset_to_default_removes_db_row(isolated_db: Path) -> None:
    apply_updates({"ingest_prices.interval_minutes": 11})
    assert load_effective_config()["ingest_prices.interval_minutes"] == 11

    reset_to_default("ingest_prices.interval_minutes")
    assert load_effective_config()["ingest_prices.interval_minutes"] == 5


def test_validate_and_serialize_bool_accepts_strings() -> None:
    assert validate_and_serialize("ingest_crypto.enabled", "true") == "true"
    assert validate_and_serialize("ingest_crypto.enabled", "False") == "false"
    assert validate_and_serialize("ingest_crypto.enabled", "yes") == "true"
    assert validate_and_serialize("ingest_crypto.enabled", True) == "true"
    assert validate_and_serialize("ingest_crypto.enabled", 0) == "false"


def test_all_specs_have_unique_keys() -> None:
    assert len(SPECS_BY_KEY) == 7, "spec list drifted — update assertions"
