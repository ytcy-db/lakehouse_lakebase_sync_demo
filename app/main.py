"""Lakehouse × Lakebase 双方向連携デモ（ローカル実行用 FastAPI）。

  顧客アンケートアプリ   … Lakebase（Postgres）に直接読み書き
  社内 製品マスタ管理    … Lakehouse（Databricks SQL）に読み書き
  データフロー           … ① 同期テーブル ② Lakebase CDF の到達をリアルタイム表示

起動: .venv/bin/uvicorn main:app --app-dir app --port 8000
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from databricks.sdk import WorkspaceClient
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import settings as S
from backend.dbsql import Dbsql, DbsqlError
from backend.lakebase import Lakebase
from backend.reset import Resetter
from backend.tracker import Tracker

w = WorkspaceClient(profile=S.profile)
lb = Lakebase(w)
sql = Dbsql(w)
pool = ThreadPoolExecutor(max_workers=8)


def _cached(check, ttl: int = 30):
    """存在確認を ttl 秒キャッシュする。例外は「なし」とみなす。"""
    cache = {"at": 0.0, "ok": False}

    def available() -> bool:
        if time.time() - cache["at"] > ttl:
            try:
                cache["ok"] = bool(check())
            except Exception:  # noqa: BLE001
                cache["ok"] = False
            cache["at"] = time.time()
        return cache["ok"]

    return available


# ② Lakebase CDF の履歴テーブルがあるか（デフォルトストレージのカタログには作れない）
cdf_available = _cached(lambda: w.tables.exists(S.uc_cdf_history).table_exists)


tracker = Tracker(lb, sql, {"cdf": cdf_available})
resetter = Resetter(w, lb, sql, on_start=tracker.clear)
app = FastAPI(title="Lakehouse x Lakebase Sync Demo")
STATIC = Path(__file__).parent / "static"


def timed(fn, *a, **kw):
    t0 = time.perf_counter()
    rows = fn(*a, **kw)
    return rows, round((time.perf_counter() - t0) * 1000)


def now_ms_iso() -> str:
    """ミリ秒に丸めた UTC 時刻（DBSQL の結果はミリ秒精度のため、照合用に揃える）。"""
    dt = datetime.now(timezone.utc)
    return dt.replace(microsecond=dt.microsecond // 1000 * 1000).isoformat().replace("+00:00", "Z")


@app.exception_handler(DbsqlError)
async def _dbsql_error(_, exc: DbsqlError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=502, content={"detail": f"DBSQL エラー: {exc}"})


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------
@app.get("/api/config")
def config():
    host = w.config.host.rstrip("/")
    return {
        "user": lb.user,
        "host": host,
        "warehouse_id": S.warehouse_id,
        "lakebase": {"project": S.lb_project, "branch": S.lb_branch, "host": lb.host, "database": S.lb_database},
        "tables": {
            "uc_products": S.uc_products,
            "lb_synced_products": f"{S.lb_database}.{S.lb_synced_products}",
            "lb_surveys": f"{S.lb_database}.{S.lb_surveys}",
            "uc_cdf_history": S.uc_cdf_history,
        },
        "cdf_available": cdf_available(),
        "links": {
            "uc_schema": f"{host}/explore/data/{S.uc_catalog}/{S.uc_schema}",
            "lakebase_project": f"{host}/lakebase/projects/{S.lb_project}",
        },
    }


@app.get("/api/events")
def events():
    return tracker.list()


# ---------------------------------------------------------------------------
# デモを初期状態に戻す
# ---------------------------------------------------------------------------
@app.post("/api/admin/reset", status_code=202)
def start_reset():
    if not resetter.start():
        raise HTTPException(409, "初期化を実行中です")
    return resetter.status()


@app.get("/api/admin/reset")
def reset_status():
    return resetter.status()


# ---------------------------------------------------------------------------
# 顧客アンケートアプリ（裏側 = Lakebase）
# ---------------------------------------------------------------------------
class SurveyIn(BaseModel):
    product_id: str
    customer_name: str = Field(min_length=1, max_length=40)
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=400)
    channel: str = "web"


class SurveyPatch(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=400)


SURVEY_TARGETS = ["cdf"]


@app.get("/api/customer/products")
def customer_products():
    rows, ms = timed(
        lb.query,
        f"""
        SELECT p.product_id, p.product_name, p.category, p.price, p.description, p.updated_at,
               count(s.response_id) AS responses,
               round(avg(s.rating)::numeric, 1) AS avg_rating
        FROM {S.lb_synced_products} p
        LEFT JOIN {S.lb_surveys} s ON s.product_id = p.product_id
        WHERE p.status = '販売中'
        GROUP BY p.product_id, p.product_name, p.category, p.price, p.description, p.updated_at
        ORDER BY p.product_id
        """,
    )
    return {"rows": rows, "elapsed_ms": ms, "source": f"Lakebase: {S.lb_synced_products}（同期テーブル）"}


@app.get("/api/customer/responses")
def customer_responses():
    rows, ms = timed(
        lb.query,
        f"""
        SELECT s.*, p.product_name
        FROM {S.lb_surveys} s LEFT JOIN {S.lb_synced_products} p USING (product_id)
        ORDER BY s.updated_at DESC LIMIT 30
        """,
    )
    return {"rows": rows, "elapsed_ms": ms}


@app.post("/api/customer/responses")
def create_response(body: SurveyIn):
    t0 = time.perf_counter()
    row = lb.execute_returning(
        f"""
        INSERT INTO {S.lb_surveys} (product_id, customer_name, rating, comment, channel, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s,
                date_trunc('milliseconds', clock_timestamp()), date_trunc('milliseconds', clock_timestamp()))
        RETURNING response_id, updated_at
        """,
        (body.product_id, body.customer_name, body.rating, body.comment, body.channel),
    )
    ms = round((time.perf_counter() - t0) * 1000)
    ev = tracker.add(
        "survey_insert", "lakebase", f"アンケート登録 #{row['response_id']}（{body.customer_name} / ★{body.rating}）",
        str(row["response_id"]), {"updated_at": row["updated_at"]}, SURVEY_TARGETS,
    )
    return {"response_id": row["response_id"], "elapsed_ms": ms, "event_id": ev.id}


@app.put("/api/customer/responses/{response_id}")
def update_response(response_id: int, body: SurveyPatch):
    t0 = time.perf_counter()
    row = lb.execute_returning(
        f"""
        UPDATE {S.lb_surveys}
        SET rating = %s, comment = %s, updated_at = date_trunc('milliseconds', clock_timestamp())
        WHERE response_id = %s RETURNING response_id, updated_at
        """,
        (body.rating, body.comment, response_id),
    )
    if not row:
        raise HTTPException(404, "回答が見つかりません")
    ms = round((time.perf_counter() - t0) * 1000)
    ev = tracker.add(
        "survey_update", "lakebase", f"アンケート更新 #{response_id}（★{body.rating}）",
        str(response_id), {"updated_at": row["updated_at"]}, SURVEY_TARGETS,
    )
    return {"elapsed_ms": ms, "event_id": ev.id}


@app.delete("/api/customer/responses/{response_id}")
def delete_response(response_id: int):
    t0 = time.perf_counter()
    row = lb.execute_returning(
        f"DELETE FROM {S.lb_surveys} WHERE response_id = %s RETURNING response_id, updated_at",
        (response_id,),
    )
    if not row:
        raise HTTPException(404, "回答が見つかりません")
    ms = round((time.perf_counter() - t0) * 1000)
    ev = tracker.add(
        "survey_delete", "lakebase", f"アンケート削除 #{response_id}",
        str(response_id), {"updated_at": row["updated_at"]}, SURVEY_TARGETS,
    )
    return {"elapsed_ms": ms, "event_id": ev.id}


# ---------------------------------------------------------------------------
# 社内 製品マスタ管理（裏側 = Databricks SQL / Unity Catalog）
# ---------------------------------------------------------------------------
class ProductIn(BaseModel):
    product_id: str | None = None
    product_name: str = Field(min_length=1, max_length=60)
    category: str = Field(min_length=1, max_length=30)
    price: int = Field(ge=0, le=10_000_000)
    status: str = Field(default="販売中", pattern="^(販売中|販売終了)$")
    description: str = Field(default="", max_length=200)


@app.get("/api/internal/products")
def internal_products():
    rows, ms = timed(
        sql.query,
        f"SELECT product_id, product_name, category, price, status, description, updated_at "
        f"FROM {S.uc_products} ORDER BY product_id",
    )
    return {"rows": rows, "elapsed_ms": ms, "source": f"UC: {S.uc_products}"}


def _next_product_id() -> str:
    rows = sql.query(
        f"SELECT max(CAST(substr(product_id, 3) AS INT)) AS n FROM {S.uc_products} WHERE product_id LIKE 'P-9%'"
    )
    n = rows[0]["n"] if rows and rows[0]["n"] else 9000
    return f"P-{n + 1}"


@app.post("/api/internal/products")
def upsert_product(body: ProductIn):
    pid = body.product_id or _next_product_id()
    ts = now_ms_iso()
    _, ms = timed(
        sql.query,
        f"""
        MERGE INTO {S.uc_products} t
        USING (SELECT :product_id AS product_id, :product_name AS product_name, :category AS category,
                      CAST(:price AS INT) AS price, :status AS status, :description AS description,
                      CAST(:updated_at AS TIMESTAMP) AS updated_at) s
        ON t.product_id = s.product_id
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """,
        {
            "product_id": pid, "product_name": body.product_name, "category": body.category,
            "price": body.price, "status": body.status, "description": body.description, "updated_at": ts,
        },
    )
    verb = "登録/更新" if body.product_id else "新規登録"
    ev = tracker.add(
        "product_upsert", "lakehouse", f"製品{verb} {pid}（{body.product_name} / ¥{body.price:,} / {body.status}）",
        pid, {"updated_at": ts}, ["synced"],
    )
    return {"product_id": pid, "elapsed_ms": ms, "event_id": ev.id}


@app.delete("/api/internal/products/{product_id}")
def delete_product(product_id: str):
    _, ms = timed(sql.query, f"DELETE FROM {S.uc_products} WHERE product_id = :pid", {"pid": product_id})
    ev = tracker.add("product_delete", "lakehouse", f"製品削除 {product_id}", product_id, {}, ["synced"])
    return {"elapsed_ms": ms, "event_id": ev.id}


def _analytics_cdf():
    return sql.query(
        f"""
        WITH latest AS (
          SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY response_id ORDER BY _sort_by DESC) AS rn
            FROM {S.uc_cdf_history}
            WHERE _pg_change_type IN ('insert', 'update_postimage', 'delete')
          ) WHERE rn = 1 AND _pg_change_type <> 'delete'
        )
        SELECT p.product_id, p.product_name, p.status,
               count(l.response_id) AS responses, round(avg(l.rating), 2) AS avg_rating,
               max(l.updated_at) AS last_updated
        FROM {S.uc_products} p LEFT JOIN latest l ON l.product_id = p.product_id
        GROUP BY ALL ORDER BY p.product_id
        """
    )


def _recent_changes():
    return sql.query(
        f"""
        SELECT _pg_change_type, _timestamp, response_id, product_id, customer_name, rating, comment
        FROM {S.uc_cdf_history} ORDER BY _sort_by DESC LIMIT 15
        """
    )


@app.get("/api/internal/analytics")
def internal_analytics():
    """Lakebase のアンケートを Lakehouse 側（CDF の履歴テーブル）から集計する。"""
    if not cdf_available():
        return {"unavailable": True}
    f_sum = pool.submit(timed, _analytics_cdf)
    f_chg = pool.submit(timed, _recent_changes)
    out = {"source": S.uc_cdf_history}
    for k, f in (("summary", f_sum), ("changes", f_chg)):
        try:
            rows, ms = f.result()
            out[k] = {"rows": rows, "elapsed_ms": ms}
        except DbsqlError as e:
            out[k] = {"error": str(e)}
    return out


# ---------------------------------------------------------------------------
# データフロー（両側のスナップショット＋同期状態）
# ---------------------------------------------------------------------------
def _sync_status():
    try:
        st = w.postgres.get_synced_table(name=f"synced_tables/{S.uc_synced_products}").as_dict()["status"]
        last = st.get("last_sync", {})
        return {
            "state": st.get("detailed_state"),
            "last_sync_end": last.get("sync_end_time"),
            "delta_commit_version": last.get("delta_table_sync_info", {}).get("delta_commit_version"),
            "pipeline_id": st.get("pipeline_id"),
        }
    except Exception as e:  # noqa: BLE001
        return {"state": "ERROR", "error": str(e)[:200]}


def _cdf_status():
    rows = lb.query(
        "SELECT t.status, t.last_write_time, t.committed_lsn::text AS committed_lsn, c.relname "
        "FROM wal2delta.tables t LEFT JOIN pg_class c ON c.oid = t.table_oid"
    )
    return rows


@app.get("/api/flow/snapshot")
def flow_snapshot():
    jobs = {
        "uc_products": pool.submit(timed, sql.query,
            f"SELECT product_id, product_name, price, status, updated_at FROM {S.uc_products} ORDER BY product_id"),
        "lb_products": pool.submit(timed, lb.query,
            f"SELECT product_id, product_name, price, status, updated_at FROM {S.lb_synced_products} ORDER BY product_id"),
        "lb_surveys": pool.submit(timed, lb.query,
            f"SELECT response_id, product_id, customer_name, rating, comment, updated_at FROM {S.lb_surveys} "
            "ORDER BY updated_at DESC LIMIT 8"),
        "sync_status": pool.submit(timed, _sync_status),
    }
    if cdf_available():
        jobs["cdf_history"] = pool.submit(timed, sql.query,
            f"SELECT _pg_change_type, _pg_lsn, _timestamp, response_id, product_id, customer_name, rating, updated_at "
            f"FROM {S.uc_cdf_history} ORDER BY _sort_by DESC LIMIT 12")
        jobs["cdf_status"] = pool.submit(timed, _cdf_status)
    out = {}
    for k, f in jobs.items():
        try:
            rows, ms = f.result()
            out[k] = {"rows": rows, "elapsed_ms": ms}
        except Exception as e:  # noqa: BLE001
            out[k] = {"error": str(e)[:300]}
    for k in ("cdf_history", "cdf_status"):
        out.setdefault(k, {"unavailable": True})
    return out


# ---------------------------------------------------------------------------
# 静的ファイル
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
