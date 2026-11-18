"""Reject schemas missing columns needed by current repositories."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from agentpay.api.factory import _verify_database_schema_ready
from agentpay.config.config import ConfigValidationError


@pytest.mark.parametrize("revision", ["0005_add_cart_tables", "head"])
def test_schema_readiness_requires_current_columns(tmp_path: Path, revision: str) -> None:
    root = Path(__file__).resolve().parents[4]
    url = f"sqlite:///{tmp_path / 'schema.db'}"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.set_main_option("script_location", str(root / "infrastructure/database/migrations"))
    command.upgrade(config, revision)
    engine = create_engine(url)
    try:
        if revision == "head":
            _verify_database_schema_ready(engine)
        else:
            with pytest.raises(ConfigValidationError, match="Missing required columns"):
                _verify_database_schema_ready(engine)
    finally:
        engine.dispose()
