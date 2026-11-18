"""Lakebase 側の初期セットアップ（スキーマ・アンケートテーブル・サンプルデータ）。

実行: .venv/bin/python setup/02_lakebase_setup.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from backend import seed  # noqa: E402
from backend.lakebase import Lakebase  # noqa: E402


def main() -> None:
    print("rows:", seed.create_surveys(Lakebase()))


if __name__ == "__main__":
    main()
