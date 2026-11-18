"""デモの接続先設定。環境変数（または app/.env）で上書きできる。"""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    profile: str = _env("DATABRICKS_PROFILE", "DEFAULT")
    warehouse_id: str = _env("WAREHOUSE_ID", "")

    # Lakebase（顧客アプリの裏側）
    lb_project: str = _env("LB_PROJECT", "survey-sync-demo")
    lb_branch: str = _env("LB_BRANCH", "production")
    lb_endpoint_id: str = _env("LB_ENDPOINT_ID", "primary")
    lb_database: str = _env("LB_DATABASE", "databricks_postgres")
    lb_database_id: str = _env("LB_DATABASE_ID", "databricks-postgres")
    lb_app_schema: str = _env("LB_APP_SCHEMA", "survey_app")

    # Lakehouse（社内システムの裏側）
    uc_catalog: str = _env("UC_CATALOG", "main")
    uc_schema: str = _env("UC_SCHEMA", "survey_sync_demo")

    # ① 同期テーブル（UC 製品マスタ → Lakebase）
    synced_table: str = _env("SYNCED_TABLE", "products_synced")

    @property
    def branch_path(self) -> str:
        return f"projects/{self.lb_project}/branches/{self.lb_branch}"

    @property
    def endpoint_path(self) -> str:
        return f"{self.branch_path}/endpoints/{self.lb_endpoint_id}"

    @property
    def database_path(self) -> str:
        return f"{self.branch_path}/databases/{self.lb_database_id}"

    @property
    def uc_products(self) -> str:
        return f"{self.uc_catalog}.{self.uc_schema}.products"

    @property
    def uc_synced_products(self) -> str:
        return f"{self.uc_catalog}.{self.uc_schema}.{self.synced_table}"

    @property
    def lb_synced_products(self) -> str:
        # 同期テーブルは Postgres 側で UC と同名のスキーマ・テーブルとして作られる
        return f"{self.uc_schema}.{self.synced_table}"

    @property
    def lb_surveys(self) -> str:
        return f"{self.lb_app_schema}.survey_responses"

    @property
    def uc_cdf_history(self) -> str:
        return f"{self.uc_catalog}.{self.uc_schema}.lb_survey_responses_history"


settings = Settings()

if not settings.warehouse_id:
    raise SystemExit("WAREHOUSE_ID が未設定です。app/.env.example を app/.env にコピーして接続先を設定してください。")
