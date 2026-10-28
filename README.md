# IT Application Catalogue & Contract Register

Streamlit app with filters, KPI tiles, a colour-coded register, add/edit forms and an audit trail.
It runs against a **local SQLite file** today and against **Delta tables in Databricks** later. Only configuration changes.

## 1. Run it locally (no Databricks needed)

    pip install -r requirements-local.txt
    streamlit run app.py

On first start it creates `data/catalogue.db` from the workbook in `data/`. Edits and new applications are saved
there, with an audit row per changed field. Delete `data/catalogue.db` to start over.

Check the data layer without the UI:  `python tests/test_data_layer.py`

## 2. Move it to Databricks (needs a schema where you can create tables, and Databricks Apps enabled)

1. Run `sql/01_setup_tables.sql` in the SQL Editor (replace `<catalog>` and `<schema>`).
2. Load the workbook once: set `CATALOGUE_CATALOG`, `CATALOGUE_SCHEMA`, `DATABRICKS_WAREHOUSE_ID`, then `python seed_databricks.py`.
3. In the workspace go to **Compute > Apps > Create app**, choose a custom/Streamlit app and upload this folder
   (or deploy with the Databricks CLI).
4. In the app's configuration add the SQL warehouse as a resource with the key `sql-warehouse`.
   `app.yaml` already reads it with `valueFrom: "sql-warehouse"`. Fill in the catalog and schema in `app.yaml`.
5. Run `sql/02_grant_app_access.sql` with the app's service principal ID.
6. Open the app. The sidebar should show "Databricks · catalog.schema".

## Things to know

- **Who changed what.** Databricks Apps passes the signed-in user in request headers, and the app records that name in
  `updated_by` and the audit table. The app itself writes as its service principal. Check with a test edit that the
  header you expect arrives in your workspace.
- **Status is derived**, never stored: No Contract (no end date), Expired, Renewal Due (within the renewal window,
  default 180 days), otherwise Active.
- **Not yet run against a live workspace.** The Databricks backend follows the documented connector and Apps
  patterns but has not been executed. Expect to fix small things on the first run.
- **Known limits of this POC.** New IDs are max+1 (two people adding at the same second could collide); the update and
  its audit rows are separate statements; every statement opens a short connection.
