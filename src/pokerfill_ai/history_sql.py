"""Azure SQL persistence for the service's fixed history queries, never an agent tool."""
import os
import re
from contextlib import contextmanager


class Record(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, rows): self.rows = rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


def translate(sql):
    sql = re.sub(r'\binvestigations\b', '[diagnostics].[Investigation]', sql, flags=re.I)
    sql = re.sub(r'\bowner\b', '[owner]', sql, flags=re.I)
    sql = re.sub(r'LIMIT\s+(\?|\d+)', r'OFFSET 0 ROWS FETCH NEXT \1 ROWS ONLY', sql, flags=re.I)
    return sql.replace('?', '%s')


class Connection:
    def __init__(self, raw): self.raw = raw
    def execute(self, sql, params=()):
        with self.raw.cursor() as cursor:
            cursor.execute(translate(sql), tuple(params))
            if cursor.description:
                keys = [c[0] for c in cursor.description]
                return Result([Record(zip(keys, row)) for row in cursor.fetchall()])
            return Result([])


@contextmanager
def connection():
    import certifi
    import pytds
    raw = pytds.connect(server=os.environ['BACKOFFICE_SQL_SERVER'],
        database=os.environ['BACKOFFICE_SQL_DATABASE'], user=os.environ['BACKOFFICE_SQL_USER'],
        password=os.environ['BACKOFFICE_SQL_PASSWORD'], cafile=certifi.where(), validate_host=True,
        enc_login_only=False, login_timeout=10, timeout=20, autocommit=False)
    try:
        yield Connection(raw)
        raw.commit()
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()
