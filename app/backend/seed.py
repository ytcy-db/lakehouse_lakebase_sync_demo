"""テーブル定義と初期データ。セットアップスクリプトとアプリの「初期状態に戻す」で共有する。"""
from .config import settings as S
from .dbsql import Dbsql
from .lakebase import Lakebase

PRODUCTS = [
    ("P-1001", "AeroBuds Pro", "ワイヤレスイヤホン", 24800, "販売中", "ノイズキャンセリング搭載の完全ワイヤレスイヤホン"),
    ("P-1002", "AeroBuds Lite", "ワイヤレスイヤホン", 12800, "販売中", "軽さ4.2gのエントリーモデル"),
    ("P-2001", "PulseWatch S", "スマートウォッチ", 39800, "販売中", "心拍・睡眠・GPSを1台で計測"),
    ("P-2002", "PulseWatch Kids", "スマートウォッチ", 14800, "販売中", "見守り機能付きの子ども向けモデル"),
    ("P-3001", "SoundBar 300", "ホームオーディオ", 49800, "販売中", "Dolby Atmos対応のサウンドバー"),
    ("P-4001", "CleanBot Mini", "生活家電", 29800, "販売中", "段差に強い小型ロボット掃除機"),
]

SURVEYS = [
    ("P-1001", "佐藤 花子", 5, "ノイキャンが強力で通勤が快適になりました", "web"),
    ("P-1001", "鈴木 一郎", 4, "音質は満足。ケースが少し大きい", "app"),
    ("P-2001", "高橋 美咲", 3, "バッテリーが2日しか持たないのが残念", "web"),
    ("P-3001", "田中 健", 5, "映画の迫力が段違い", "store"),
    ("P-4001", "伊藤 愛", 2, "ラグの上で止まることがある", "app"),
]

UC_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {S.uc_catalog}.{S.uc_schema} "
    "COMMENT 'Lakehouse x Lakebase 双方向連携デモ（アンケート×製品マスタ）'",
    f"""CREATE TABLE IF NOT EXISTS {S.uc_products} (
      product_id STRING NOT NULL COMMENT '製品ID',
      product_name STRING COMMENT '製品名',
      category STRING COMMENT 'カテゴリ',
      price INT COMMENT '税込価格(円)',
      status STRING COMMENT '販売中/販売終了',
      description STRING COMMENT '製品説明',
      updated_at TIMESTAMP COMMENT '最終更新日時',
      CONSTRAINT products_pk PRIMARY KEY (product_id)
    ) COMMENT '社内システムで管理する製品マスタ（同期テーブルでLakebaseへ配信）'
    TBLPROPERTIES (delta.enableChangeDataFeed = true)""",  # Continuous 同期の必須条件
]

LB_DDL = f"""
CREATE SCHEMA IF NOT EXISTS {S.lb_app_schema};

CREATE TABLE IF NOT EXISTS {S.lb_surveys} (
    response_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id    TEXT        NOT NULL,
    customer_name TEXT        NOT NULL,
    rating        INTEGER     NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    channel       TEXT        NOT NULL DEFAULT 'web',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lakebase CDF の必須条件：変更前の行も WAL に残す
ALTER TABLE {S.lb_surveys} REPLICA IDENTITY FULL;
"""


def _sql_str(v) -> str:
    return "'" + str(v).replace("'", "''") + "'" if isinstance(v, str) else str(v)


def seed_products(sql: Dbsql) -> None:
    """製品マスタを作り、初期 6 製品だけの状態にする。"""
    for st in UC_DDL:
        sql.query(st)
    values = ",\n".join(
        "(" + ", ".join(_sql_str(v) for v in row) + ", current_timestamp())" for row in PRODUCTS
    )
    sql.query(f"DELETE FROM {S.uc_products}")
    sql.query(f"INSERT INTO {S.uc_products} VALUES\n{values}")


def create_surveys(lb: Lakebase, drop: bool = False) -> int:
    """アンケートテーブルを作り、空なら初期 5 件を入れる。件数を返す。"""
    with lb.connect() as conn:
        if drop:
            conn.execute(f"DROP TABLE IF EXISTS {S.lb_surveys}")
        conn.execute(LB_DDL)
        if conn.execute(f"SELECT count(*) AS n FROM {S.lb_surveys}").fetchone()["n"] == 0:
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {S.lb_surveys} (product_id, customer_name, rating, comment, channel) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    SURVEYS,
                )
        conn.commit()
        return conn.execute(f"SELECT count(*) AS n FROM {S.lb_surveys}").fetchone()["n"]
