#!/usr/bin/env bash
# 2 つの連携を作成する。接続先は app/.env を読む。
#   ./setup/03_sync_setup.sh [all|synced|cdf]
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source app/.env; set +a
PROFILE=${DATABRICKS_PROFILE}
CAT=${UC_CATALOG}; SCH=${UC_SCHEMA:-survey_sync_demo}
DB=${DATABRICKS_CLI:-/opt/homebrew/bin/databricks}
export DATABRICKS_CLI_DO_NOT_EXECUTE_NEWER_VERSION=1
BRANCH=projects/${LB_PROJECT:-survey-sync-demo}/branches/${LB_BRANCH:-production}

step=${1:-all}

if [[ $step == all || $step == synced ]]; then
  echo "== ① 同期テーブル: $CAT.$SCH.products → Lakebase $SCH.products_synced (CONTINUOUS)"
  $DB postgres create-synced-table "$CAT.$SCH.products_synced" --no-wait -p "$PROFILE" --json '{
    "spec": {
      "source_table_full_name": "'"$CAT.$SCH"'.products",
      "primary_key_columns": ["product_id"],
      "scheduling_policy": "CONTINUOUS",
      "branch": "'"$BRANCH"'",
      "postgres_database": "databricks_postgres",
      "create_database_objects_if_missing": true,
      "new_pipeline_spec": {"storage_catalog": "'"$CAT"'", "storage_schema": "'"$SCH"'"}
    }}'
fi

if [[ $step == all || $step == cdf ]]; then
  echo "== ② Lakebase CDF: Postgres survey_app → UC $CAT.$SCH"
  $DB postgres create-cdf-config "$BRANCH/databases/databricks-postgres" \
    "$CAT" "$SCH" survey_app --cdf-config-id survey_app_feed -p "$PROFILE"
fi
