from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection


class Database:
    def __init__(self, dsn: str):
        self._dsn = dsn

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with psycopg.connect(self._dsn) as connection:
            with connection.transaction():
                yield connection
