"""Database engine creation and session factory utilities (§49)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from agentpay.infrastructure.database.base import Base


def create_database_engine(
    url: str = "sqlite:///:memory:",
    echo: bool = False,
    pool_pre_ping: bool = True,
) -> Engine:
    """Create a configured SQLAlchemy engine supporting SQLite and PostgreSQL."""
    connect_args: dict[str, bool] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(
        url,
        echo=echo,
        connect_args=connect_args,
        pool_pre_ping=pool_pre_ping if not url.startswith("sqlite:///:memory:") else False,
    )


def init_db(engine: Engine) -> None:
    """Create all relational tables defined in the metadata."""
    Base.metadata.create_all(bind=engine)


def drop_db(engine: Engine) -> None:
    """Drop all relational tables."""
    Base.metadata.drop_all(bind=engine)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a configured sessionmaker bound to the engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def get_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Context manager for acquiring and safely closing a transactional session."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
