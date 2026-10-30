import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from relayforge.config import Settings
from relayforge.models import Branch, Run, Task


@pytest.fixture
def pg():
    if os.environ.get("RELAYFORGE_TEST_POSTGRES") != "1":
        pytest.skip("Explicit PostgreSQL integration opt-in required; not an acceptance pass")
    url = Settings().database_url.get_secret_value()
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
    schema = "rf_test_" + uuid4().hex
    unavailable = False
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except SQLAlchemyError:
        admin.dispose()
        unavailable = True
    if unavailable:
        pytest.fail(
            "PostgreSQL integration unavailable: connection/schema setup failed (details suppressed)",
            pytrace=False,
        )
    engine = create_engine(
        url,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema}", "connect_timeout": 3},
    )
    try:
        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        yield engine, sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        # Only the fresh, test-owned random schema is eligible for cleanup.
        assert schema.startswith("rf_test_") and len(schema) == 40
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def seeded(pg):
    engine, sessions = pg
    with sessions.begin() as session:
        task = Task(title="Journal acceptance test", template="test")
        session.add(task)
        session.flush()
        branch = Branch(task_id=task.id)
        session.add(branch)
        session.flush()
        run = Run(task_id=task.id, branch_id=branch.id)
        session.add(run)
        session.flush()
        ids = run.id, branch.id
    return engine, sessions, ids
