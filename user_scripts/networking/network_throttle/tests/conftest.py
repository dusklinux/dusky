import os
import pytest
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# 1. Override database path in src.utils to use the test database file
import src.utils
TEST_DB_PATH = src.utils.SETTINGS_DIR / "network_limiter_test.db"
src.utils.DB_PATH = TEST_DB_PATH

# 2. Re-initialize the SQLAlchemy engine and connection pool inside src.database
import src.database
src.database.DATABASE_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
src.database.engine = create_async_engine(src.database.DATABASE_URL, echo=False)
src.database.AsyncSessionLocal = async_sessionmaker(src.database.engine, expire_on_commit=False, class_=AsyncSession)

from src.database import engine

@pytest.fixture(autouse=True)
def clean_db():
    """Ensure SQLite database engine connections are disposed and the test file deleted before and after each test."""
    try:
        asyncio.run(engine.dispose())
    except Exception:
        pass
        
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass
            
    yield
    
    try:
        asyncio.run(engine.dispose())
    except Exception:
        pass
        
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass

