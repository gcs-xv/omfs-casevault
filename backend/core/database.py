from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from backend.core.config import get_settings

class Base(DeclarativeBase): pass

url = get_settings().database_url
if url.startswith("sqlite:///./"):
    Path(url.removeprefix("sqlite:///./")).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})

@event.listens_for(engine, "connect")
def _sqlite_fk(dbapi_connection, _):
    if url.startswith("sqlite"):
        cur = dbapi_connection.cursor(); cur.execute("PRAGMA foreign_keys=ON"); cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

