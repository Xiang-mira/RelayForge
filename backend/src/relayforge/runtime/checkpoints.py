from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row

from relayforge.config import Settings


@contextmanager
def postgres_checkpointer(settings: Settings, *, schema: str | None = None):
    url = settings.database_url.get_secret_value().replace("postgresql+psycopg:", "postgresql:", 1)
    kwargs = {"options": "-csearch_path=" + schema} if schema else {}
    if schema and not schema.replace("_", "").isalnum():
        raise ValueError("Invalid checkpoint schema")
    with psycopg.connect(
        url, autocommit=True, row_factory=dict_row, prepare_threshold=0, connect_timeout=3, **kwargs
    ) as connection:
        serializer = JsonPlusSerializer(
            pickle_fallback=False, allowed_json_modules=[], allowed_msgpack_modules=[]
        )
        saver = PostgresSaver(connection, serde=serializer)
        saver.setup()
        yield saver
