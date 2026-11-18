"""Lakebase（Postgres）接続。ユーザー自身の ID ＋ OAuth トークンをパスワードとして使う。"""
import threading
import time

import psycopg
from databricks.sdk import WorkspaceClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

TOKEN_TTL_SEC = 45 * 60  # トークンは 60 分有効。余裕を持って取り直す


class Lakebase:
    def __init__(self, w: WorkspaceClient | None = None):
        self.w = w or WorkspaceClient(profile=settings.profile)
        self.user = self.w.current_user.me().user_name
        ep = self.w.postgres.get_endpoint(name=settings.endpoint_path)
        self.host = ep.status.hosts.host
        self._token: str | None = None
        self._token_at = 0.0
        self._lock = threading.Lock()
        self._pool: ConnectionPool | None = None

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.time() - self._token_at > TOKEN_TTL_SEC:
                cred = self.w.postgres.generate_database_credential(endpoint=settings.endpoint_path)
                self._token, self._token_at = cred.token, time.time()
            return self._token

    def conninfo(self) -> dict:
        return dict(
            host=self.host,
            dbname=settings.lb_database,
            user=self.user,
            password=self.token(),
            sslmode="require",
            application_name="survey-sync-demo",
        )

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(**self.conninfo(), row_factory=dict_row)

    @property
    def pool(self) -> ConnectionPool:
        if self._pool is None:
            lb = self

            class OAuthConnection(psycopg.Connection):
                @classmethod
                def connect(cls, conninfo="", **kwargs):
                    kwargs.update(lb.conninfo())
                    return super().connect(conninfo, **kwargs)

            self._pool = ConnectionPool(
                connection_class=OAuthConnection,
                kwargs={"row_factory": dict_row, "autocommit": True},
                min_size=1,
                max_size=6,
                max_lifetime=TOKEN_TTL_SEC,
                check=ConnectionPool.check_connection,
                open=True,
            )
        return self._pool

    def query(self, sql: str, params=None) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def execute_returning(self, sql: str, params=None) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone()
