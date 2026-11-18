"""Lakehouse（Databricks SQL）接続。Statement Execution API をユーザー自身の ID で呼ぶ。"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    Disposition,
    Format,
    StatementParameterListItem,
    StatementState,
)

from .config import settings


class DbsqlError(RuntimeError):
    pass


class Dbsql:
    def __init__(self, w: WorkspaceClient | None = None):
        self.w = w or WorkspaceClient(profile=settings.profile)

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        resp = self.w.statement_execution.execute_statement(
            statement=sql,
            warehouse_id=settings.warehouse_id,
            wait_timeout="30s",
            disposition=Disposition.INLINE,
            format=Format.JSON_ARRAY,
            parameters=[
                StatementParameterListItem(name=k, value=None if v is None else str(v))
                for k, v in (params or {}).items()
            ]
            or None,
        )
        while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            resp = self.w.statement_execution.get_statement(resp.statement_id)
        if resp.status.state != StatementState.SUCCEEDED:
            err = resp.status.error
            raise DbsqlError(err.message if err else str(resp.status.state))
        if not resp.manifest or not resp.manifest.schema or not resp.manifest.schema.columns:
            return []
        cols = [(c.name, c.type_name.value if c.type_name else "") for c in resp.manifest.schema.columns]
        rows = (resp.result.data_array if resp.result else None) or []
        return [{name: _cast(v, t) for (name, t), v in zip(cols, r)} for r in rows]


def _cast(v, type_name: str):
    if v is None:
        return None
    if type_name in ("INT", "LONG", "SHORT", "BYTE"):
        return int(v)
    if type_name in ("DOUBLE", "FLOAT", "DECIMAL"):
        return float(v)
    if type_name == "BOOLEAN":
        return v == "true"
    return v
