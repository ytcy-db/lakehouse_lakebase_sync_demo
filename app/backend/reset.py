"""デモを初期状態に戻す処理（アプリのボタンと setup/04_reset_demo.py から呼ぶ）。

  1. 製品マスタ（UC）を初期 6 製品に戻す
  2. Lakebase CDF を止める
  3. 履歴テーブル（UC）とアンケートテーブル（Lakebase）を作り直す
  4. Lakebase CDF を再開する
  5. 初期 5 件が履歴テーブルに届くのを待つ
  6. ① 同期テーブルが初期 6 製品になるのを待つ
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.postgres import CdfConfig

from . import seed
from .config import settings as S
from .dbsql import Dbsql
from .lakebase import Lakebase
from .tracker import to_epoch

CDF_CONFIG_ID = "survey_app_feed"
WAIT_SEC = 180

STEPS = [
    "製品マスタ（UC）を初期 6 製品に戻す",
    "Lakebase CDF を止める",
    "履歴テーブルとアンケートテーブルを作り直す",
    "Lakebase CDF を再開する",
    "初期データが履歴テーブルに届くのを待つ",
    "同期テーブル（Lakebase）への反映を待つ",
]


@dataclass
class Step:
    label: str
    status: str = "pending"  # pending / running / done / error
    detail: str = ""
    sec: float | None = None


@dataclass
class ResetState:
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    steps: list[Step] = field(default_factory=list)


class Resetter:
    def __init__(self, w: WorkspaceClient, lb: Lakebase, sql: Dbsql, on_start=None):
        self.w, self.lb, self.sql = w, lb, sql
        self.on_start = on_start  # 開始時に呼ぶ（トラッカーの履歴を消す等）
        self.state = ResetState()
        self._lock = threading.Lock()

    def status(self) -> dict:
        with self._lock:
            return asdict(self.state)

    def start(self) -> bool:
        with self._lock:
            if self.state.running:
                return False
            self.state = ResetState(running=True, started_at=time.time(), steps=[Step(s) for s in STEPS])
        if self.on_start:
            self.on_start()
        threading.Thread(target=self._run, daemon=True).start()
        return True

    # ------------------------------------------------------------------
    def _run(self) -> None:
        actions = [
            self._reset_products,
            self._stop_cdf,
            self._recreate_tables,
            self._start_cdf,
            self._wait_history,
            self._wait_synced,
        ]
        try:
            for step, action in zip(self.state.steps, actions):
                step.status, t0 = "running", time.time()
                step.detail = action() or ""
                step.status, step.sec = "done", round(time.time() - t0, 1)
        except Exception as e:  # noqa: BLE001 - 画面にそのまま出す
            for step in self.state.steps:
                if step.status == "running":
                    step.status = "error"
            self.state.error = str(e)[:400]
        finally:
            self.state.running = False
            self.state.finished_at = time.time()

    def run_blocking(self, log=print) -> None:
        """スクリプト用：進捗を表示しながら同期実行する。"""
        if not self.start():
            raise RuntimeError("すでに実行中です")
        shown = set()
        while True:
            st = self.status()
            for i, s in enumerate(st["steps"]):
                if s["status"] in ("done", "error") and i not in shown:
                    shown.add(i)
                    log(f"{'✓' if s['status'] == 'done' else '✗'} {s['label']}（{s['sec'] or 0} 秒）{s['detail']}")
            if not st["running"]:
                if st["error"]:
                    raise RuntimeError(st["error"])
                return
            time.sleep(1)

    # ---- 各ステップ -----------------------------------------------------
    def _reset_products(self) -> str:
        seed.seed_products(self.sql)
        return f"{len(seed.PRODUCTS)} 製品"

    def _stop_cdf(self) -> str:
        try:
            op = self.w.postgres.delete_cdf_config(name=f"{S.database_path}/cdf-configs/{CDF_CONFIG_ID}")
            _wait(op)
            return "停止しました"
        except NotFound:
            return "もともと停止中"

    def _recreate_tables(self) -> str:
        self.sql.query(f"DROP TABLE IF EXISTS {S.uc_cdf_history}")
        n = seed.create_surveys(self.lb, drop=True)
        return f"アンケート {n} 件"

    def _start_cdf(self) -> str:
        op = self.w.postgres.create_cdf_config(
            parent=S.database_path,
            cdf_config=CdfConfig(catalog=S.uc_catalog, schema=S.uc_schema, postgres_schema=S.lb_app_schema),
            cdf_config_id=CDF_CONFIG_ID,
        )
        _wait(op)
        return f"{S.lb_app_schema} → {S.uc_catalog}.{S.uc_schema}"

    def _wait_history(self) -> str:
        need = len(seed.SURVEYS)
        return _poll(lambda: self._history_count() >= need, f"{need} 件の insert が届きました")

    def _history_count(self) -> int:
        try:
            return self.sql.query(f"SELECT count(*) AS n FROM {S.uc_cdf_history}")[0]["n"]
        except Exception:  # noqa: BLE001 - 作成前はテーブルがない
            return 0

    def _wait_synced(self) -> str:
        want = {p[0] for p in seed.PRODUCTS}

        def ok() -> bool:
            uc = {r["product_id"]: r["updated_at"] for r in self.sql.query(
                f"SELECT product_id, updated_at FROM {S.uc_products}")}
            lb = {r["product_id"]: r["updated_at"] for r in self.lb.query(
                f"SELECT product_id, updated_at FROM {S.lb_synced_products}")}
            return set(lb) == want and all(abs(lb[k].timestamp() - to_epoch(uc[k])) < 0.0015 for k in want)

        return _poll(ok, f"{len(want)} 製品が一致しました", interval=2)


def _wait(op) -> None:
    if hasattr(op, "wait"):
        op.wait()


def _poll(check, done_msg: str, interval: float = 3) -> str:
    t0 = time.time()
    while time.time() - t0 < WAIT_SEC:
        if check():
            return done_msg
        time.sleep(interval)
    raise TimeoutError(f"{WAIT_SEC} 秒待っても完了しませんでした")

