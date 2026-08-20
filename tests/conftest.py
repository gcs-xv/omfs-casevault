import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.core.database import Base

@pytest.fixture
def db():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False});Base.metadata.create_all(engine)
    s=sessionmaker(bind=engine,expire_on_commit=False)()
    try:yield s
    finally:s.close()

