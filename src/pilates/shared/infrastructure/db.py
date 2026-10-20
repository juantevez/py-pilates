from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str, pool_size: int = 5, max_overflow: int = 10) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow, future=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)
