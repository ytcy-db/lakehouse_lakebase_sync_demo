"""Lakehouse 側（Unity Catalog）の初期セットアップ。接続先は app/.env に従う。

実行: .venv/bin/python setup/01_uc_setup.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from backend import seed  # noqa: E402
from backend.config import settings as S  # noqa: E402
from backend.dbsql import Dbsql  # noqa: E402


def main() -> None:
    sql = Dbsql()
    seed.seed_products(sql)
    for r in sql.query(f"SELECT product_id, product_name, price, status FROM {S.uc_products} ORDER BY 1"):
        print(r)


if __name__ == "__main__":
    main()
