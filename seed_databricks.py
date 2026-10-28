"""One-off: load the 225 workbook rows into the (empty) Databricks table.

Run from your computer after sql/01_setup_tables.sql, with Databricks authentication configured
(for example `databricks auth login`) and these variables set:

    CATALOGUE_CATALOG, CATALOGUE_SCHEMA, DATABRICKS_WAREHOUSE_ID

    pip install databricks-sql-connector databricks-sdk pandas openpyxl
    python seed_databricks.py
"""
from data_layer import DatabricksBackend, read_workbook

be = DatabricksBackend()
existing = be._exec(f"SELECT count(*) AS n FROM {be.table}", fetch=True)["n"].iloc[0]
if int(existing) > 0:
    raise SystemExit(f"{be.table} already has {existing} rows. Refusing to load again.")
df = read_workbook()
print(f"Loading {len(df)} rows into {be.table} ...")
print("Loaded", be.bulk_load(df), "rows.")
