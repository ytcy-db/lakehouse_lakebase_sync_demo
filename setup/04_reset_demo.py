"""デモを初期状態に戻す（アプリの「初期状態に戻す」ボタンと同じ処理）。

実行: .venv/bin/python setup/04_reset_demo.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from databricks.sdk import WorkspaceClient  # noqa: E402

from backend.config import settings as S  # noqa: E402
from backend.dbsql import Dbsql  # noqa: E402
from backend.lakebase import Lakebase  # noqa: E402
from backend.reset import Resetter  # noqa: E402

if __name__ == "__main__":
    w = WorkspaceClient(profile=S.profile)
    Resetter(w, Lakebase(w), Dbsql(w)).run_blocking()
