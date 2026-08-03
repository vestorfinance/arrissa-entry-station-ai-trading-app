"""Postgres access (psycopg 3). Simple per-request connections."""
import psycopg
from psycopg.rows import dict_row

import config


def connect():
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)


def init_schema():
    from pathlib import Path
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with connect() as conn:
        conn.execute(sql)
        conn.commit()


if __name__ == "__main__":
    init_schema()
    print("schema initialized")
