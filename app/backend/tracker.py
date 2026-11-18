"""連携トラッカー。

アプリから書き込んだ 1 件ごとに「書き込み先 → 連携先」の到達をポーリングで検出し、
書き込みから何秒で反映されたかを記録する。

- 製品マスタ（UC / DBSQL で書き込み）  → ① 同期テーブル（Lakebase）
- アンケート（Lakebase で書き込み）     → ② Lakebase CDF（UC の Delta 履歴テーブル）
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .config import settings
from .dbsql import Dbsql
from .lakebase import Lakebase

TIMEOUT_SEC = {"synced": 600, "cdf": 300}
PROBE_INTERVAL_SEC = {"synced": 1.0, "cdf": 2.0}

UNAVAILABLE_DETAIL = {
    "cdf": "CDF の履歴テーブルなし（デフォルトストレージのカタログには作れない）",
}

TARGET_LABEL = {
    "synced": "① 同期テーブル → Lakebase",
    "cdf": "② CDF → UC Delta 履歴",
}


def to_epoch(v) -> float | None:
    """Postgres の datetime / DBSQL の ISO 文字列のどちらでも epoch 秒に揃える。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        s = str(v).replace("Z", "+00:00").replace(" ", "T")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def same_ms(a, b) -> bool:
    ea, eb = to_epoch(a), to_epoch(b)
    return ea is not None and eb is not None and abs(ea - eb) < 0.0015


@dataclass
class Target:
    name: str
    label: str
    status: str = "waiting"  # waiting / arrived / timeout / unavailable / error
    arrived_at: float | None = None
    lag_sec: float | None = None
    detail: str = ""
    delta_processed_at: str | None = None  # CDF の _timestamp（Delta に書いた時刻）
    probes: int = 0
    _last_probe: float = 0.0


@dataclass
class Event:
    id: int
    kind: str  # product_upsert / product_delete / survey_insert / survey_update / survey_delete
    origin: str  # lakehouse / lakebase
    label: str
    key: str
    written_at: float
    expect: dict
    targets: list[Target] = field(default_factory=list)

    def public(self) -> dict:
        d = asdict(self)
        for t in d["targets"]:
            t.pop("_last_probe", None)
        d.pop("expect", None)
        return d


class Tracker:
    def __init__(self, lb: Lakebase, sql: Dbsql, availability: dict):
        self.lb, self.sql = lb, sql
        self.availability = availability  # 連携名 -> callable（その連携が使えるか）
        self.events: list[Event] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._loop, args=({"synced"},), daemon=True).start()
        threading.Thread(target=self._loop, args=({"cdf"},), daemon=True).start()

    # ---- 登録 ---------------------------------------------------------------
    def add(self, kind: str, origin: str, label: str, key: str, expect: dict, targets: list[str]) -> Event:
        ev = Event(next(self._ids), kind, origin, label, key, time.time(), expect)
        for t in targets:
            tgt = Target(t, TARGET_LABEL[t])
            if t in self.availability and not self.availability[t]():
                tgt.status, tgt.detail = "unavailable", UNAVAILABLE_DETAIL[t]
            ev.targets.append(tgt)
        with self._lock:
            self.events.insert(0, ev)
            del self.events[50:]
        return ev

    def clear(self) -> None:
        with self._lock:
            self.events.clear()

    def list(self) -> list[dict]:
        with self._lock:
            return [e.public() for e in self.events[:30]]

    # ---- ポーリング -----------------------------------------------------------
    def _loop(self, names: set[str]) -> None:
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                work = [
                    (e, t)
                    for e in self.events
                    for t in e.targets
                    if t.name in names and t.status == "waiting"
                    and now - t._last_probe >= PROBE_INTERVAL_SEC[t.name]
                ]
            for ev, tgt in work:
                if time.time() - ev.written_at > TIMEOUT_SEC[tgt.name]:
                    tgt.status, tgt.detail = "timeout", f"{TIMEOUT_SEC[tgt.name]}秒以内に未到達"
                    continue
                tgt._last_probe = time.time()
                tgt.probes += 1
                try:
                    hit, detail, extra = self._probe(ev, tgt.name)
                except Exception as e:  # noqa: BLE001 - デモ用に画面へ出す
                    tgt.detail = f"確認中にエラー: {str(e)[:160]}"
                    continue
                if hit:
                    tgt.arrived_at = time.time()
                    tgt.lag_sec = round(tgt.arrived_at - ev.written_at, 1)
                    tgt.status, tgt.detail = "arrived", detail
                    tgt.delta_processed_at = extra
            time.sleep(0.3)

    def _probe(self, ev: Event, target: str) -> tuple[bool, str, str | None]:
        x = ev.expect
        if target == "synced":
            rows = self.lb.query(
                f"SELECT product_name, price, status, updated_at FROM {settings.lb_synced_products} "
                "WHERE product_id = %s",
                (ev.key,),
            )
            if ev.kind == "product_delete":
                return (not rows), "Lakebase から削除を確認", None
            if rows and same_ms(rows[0]["updated_at"], x["updated_at"]):
                r = rows[0]
                return True, f"{r['product_name']} / ¥{r['price']:,} / {r['status']}", None
            return False, "Lakebase に未反映", None

        if target == "cdf":
            change = {"survey_insert": "insert", "survey_update": "update_postimage", "survey_delete": "delete"}[ev.kind]
            rows = self.sql.query(
                f"SELECT _pg_change_type, _timestamp, updated_at, rating FROM {settings.uc_cdf_history} "
                "WHERE response_id = :id AND _pg_change_type = :ct ORDER BY _sort_by DESC LIMIT 5",
                {"id": ev.key, "ct": change},
            )
            for r in rows:
                if same_ms(r["updated_at"], x["updated_at"]):
                    return True, f"_pg_change_type = {change}", r["_timestamp"]
            return False, "Delta 履歴に未反映", None
        raise ValueError(target)
