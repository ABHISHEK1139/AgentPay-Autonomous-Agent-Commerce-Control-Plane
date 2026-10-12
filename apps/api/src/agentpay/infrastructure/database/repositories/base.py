"""Base repository helper for SQLAlchemy persistence (§49)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker


class BaseSqlAlchemyRepository:
    """Base repository helper supporting both Session and sessionmaker instances."""

    def __init__(self, session_or_factory: sessionmaker[Session] | Session) -> None:
        self._session_or_factory = session_or_factory

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        """Provide a transactional session context."""
        if isinstance(self._session_or_factory, Session):
            yield self._session_or_factory
        else:
            with self._session_or_factory() as session:
                try:
                    yield session
                except Exception:
                    session.rollback()
                    raise
                else:
                    if session.is_active:
                        session.commit()
