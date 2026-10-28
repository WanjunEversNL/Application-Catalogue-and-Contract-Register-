"""Data layer for the IT Application Catalogue app.

Two interchangeable backends with the same methods:

    SqliteBackend      local file, used for development and demos (default)
    DatabricksBackend  Delta tables in Unity Catalog via a SQL warehouse

Pick one with the CATALOGUE_BACKEND environment variable ("sqlite" or "databricks").
The UI (app.py) only talks to the methods below, so switching is a configuration change.

    load()                              -> DataFrame, one row per application
    update(application_id, changes, user) -> number of fields changed (writes audit rows)
    insert(record, user)                -> new ApplicationID (writes an audit row)
    history(application_id)             -> DataFrame of audit rows, newest first
"""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
WORKBOOK = ROOT / "data" / "Application_Catalogue_PowerBI_Ready.xlsx"

COLUMNS = [
    "application_id", "application_name", "portfolio_status", "domain", "contract_data_status",
    "contract_start_date", "contract_end_date", "rolling_term", "notice_period",
    "business_owner", "business_spoc", "it_owner", "it_spoc", "comments", "description",
    "record_origin", "updated_at", "updated_by",
]
# Fields a user may change on an existing application. Everything else is read-only in the app.
EDITABLE = [
    "portfolio_status", "domain", "contract_data_status", "contract_start_date", "contract_end_date",
    "rolling_term", "notice_period", "business_owner", "business_spoc", "it_owner", "comments",
]
DATE_COLS = ("contract_start_date", "contract_end_date")
NEW_FIELDS = ["application_name", "description", "it_spoc"] + EDITABLE

PORTFOLIO_VALUES = ["Keep", "Revisit", "Sunset", "Unknown"]
DOMAIN_VALUES = ["CDO", "CFO", "CHRO", "CIO", "CMDO", "COO", "CRO", "TBD"]
CONTRACT_DATA_VALUES = ["Complete", "Partial", "Not Stated", "No Contract", "Review Required"]
DEFAULT_RENEWAL_WINDOW_DAYS = 180

XLSX_MAP = {
    "ApplicationID": "application_id", "ApplicationName": "application_name",
    "PortfolioStatus": "portfolio_status", "Domain": "domain",
    "ContractDataStatus": "contract_data_status", "ContractStartDate": "contract_start_date",
    "ContractEndDate": "contract_end_date", "RollingTerm": "rolling_term", "NoticePeriod": "notice_period",
    "BusinessOwner": "business_owner", "BusinessSPOC": "business_spoc", "ITOwner": "it_owner",
    "ITSPOC": "it_spoc", "Comments": "comments", "Description": "description",
    "RecordOrigin": "record_origin",
}


# --------------------------------------------------------------------------- helpers
def _norm(v):
    """Normalise a value for storage and comparison: blanks -> None, dates -> ISO text."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def compute_changes(old: dict, new: dict) -> dict:
    """Return {field: (old, new)} for editable fields whose value really changed."""
    out = {}
    for k in EDITABLE:
        if k in new and _norm(old.get(k)) != _norm(new[k]):
            out[k] = (_norm(old.get(k)), _norm(new[k]))
    return out


def read_workbook(path: Path = WORKBOOK) -> pd.DataFrame:
    """Read the ApplicationCatalogue sheet into the table layout used by both backends."""
    raw = pd.read_excel(path, sheet_name="ApplicationCatalogue")
    df = raw.rename(columns=XLSX_MAP)[list(XLSX_MAP.values())].copy().astype(object)
    for c in df.columns:
        df[c] = df[c].map(_norm)
    return df


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    for c in DATE_COLS:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def add_status(df: pd.DataFrame, window_days: int = DEFAULT_RENEWAL_WINDOW_DAYS, today=None) -> pd.DataFrame:
    """Derive Status plus Year/Quarter/Month/Day from the contract end date.

    No end date -> No Contract; in the past -> Expired; within the window -> Renewal Due; else Active.
    """
    today = pd.Timestamp(today or dt.date.today()).normalize()
    out = df.copy()
    end = out["contract_end_date"]
    days = (end - today).dt.days
    status = pd.Series("Active", index=out.index, dtype=object)
    status[days <= window_days] = "Renewal Due"
    status[days < 0] = "Expired"
    status[end.isna()] = "No Contract"
    out["status"] = status
    out["year"] = end.dt.year.astype("Int64")
    out["quarter"] = end.dt.quarter.map(lambda q: None if pd.isna(q) else f"Qtr {int(q)}")
    out["month"] = end.dt.month_name()
    out["day"] = end.dt.day.astype("Int64")
    return out


def _next_id(existing: list[str]) -> str:
    nums = [int(m.group(1)) for s in existing if (m := re.fullmatch(r"APP-(\d+)", str(s)))]
    return f"APP-{(max(nums) if nums else 0) + 1:04d}"


# --------------------------------------------------------------------------- SQLite (local)
class SqliteBackend:
    label = "Local SQLite file"

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path or os.getenv("CATALOGUE_DB") or ROOT / "data" / "catalogue.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextlib.contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self):
        with self._tx() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS application_catalogue (
                    application_id TEXT PRIMARY KEY, application_name TEXT NOT NULL,
                    portfolio_status TEXT, domain TEXT, contract_data_status TEXT,
                    contract_start_date TEXT, contract_end_date TEXT, rolling_term TEXT, notice_period TEXT,
                    business_owner TEXT, business_spoc TEXT, it_owner TEXT, it_spoc TEXT,
                    comments TEXT, description TEXT, record_origin TEXT, updated_at TEXT, updated_by TEXT);
                CREATE TABLE IF NOT EXISTS application_catalogue_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL,
                    action TEXT NOT NULL, field TEXT, old_value TEXT, new_value TEXT,
                    changed_by TEXT, changed_at TEXT);
                """
            )
            empty = c.execute("SELECT COUNT(*) FROM application_catalogue").fetchone()[0] == 0
            if empty and WORKBOOK.exists():
                df = read_workbook()
                cols = list(df.columns)
                c.executemany(
                    f"INSERT INTO application_catalogue ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [tuple(r) for r in df.itertuples(index=False, name=None)],
                )

    def load(self) -> pd.DataFrame:
        with self._tx() as c:
            df = pd.read_sql_query("SELECT * FROM application_catalogue ORDER BY application_id", c)
        return _finalize(df)

    def update(self, application_id: str, changes: dict, user: str) -> int:
        with self._tx() as c:
            row = c.execute("SELECT * FROM application_catalogue WHERE application_id = ?", (application_id,)).fetchone()
            if row is None:
                raise KeyError(application_id)
            diffs = compute_changes(dict(row), changes)
            if not diffs:
                return 0
            now = _now()
            sets = ", ".join(f"{k} = ?" for k in diffs)  # keys come from the EDITABLE whitelist
            c.execute(
                f"UPDATE application_catalogue SET {sets}, updated_at = ?, updated_by = ? WHERE application_id = ?",
                [*(n for _, n in diffs.values()), now, user, application_id],
            )
            c.executemany(
                "INSERT INTO application_catalogue_audit (application_id, action, field, old_value, new_value, changed_by, changed_at)"
                " VALUES (?, 'update', ?, ?, ?, ?, ?)",
                [(application_id, k, None if o is None else str(o), None if n is None else str(n), user, now)
                 for k, (o, n) in diffs.items()],
            )
            return len(diffs)

    def insert(self, record: dict, user: str) -> str:
        name = _norm(record.get("application_name"))
        if not name:
            raise ValueError("application_name is required")
        with self._tx() as c:
            ids = [r[0] for r in c.execute("SELECT application_id FROM application_catalogue")]
            new_id = _next_id(ids)
            now = _now()
            vals = {k: _norm(record.get(k)) for k in NEW_FIELDS}
            vals.update(application_id=new_id, record_origin="App-added", updated_at=now, updated_by=user)
            cols = list(vals)
            c.execute(f"INSERT INTO application_catalogue ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      list(vals.values()))
            c.execute(
                "INSERT INTO application_catalogue_audit (application_id, action, field, old_value, new_value, changed_by, changed_at)"
                " VALUES (?, 'create', 'application_name', NULL, ?, ?, ?)", (new_id, name, user, now))
        return new_id

    def history(self, application_id: str) -> pd.DataFrame:
        with self._tx() as c:
            return pd.read_sql_query(
                "SELECT changed_at, action, field, old_value, new_value, changed_by FROM application_catalogue_audit"
                " WHERE application_id = ? ORDER BY audit_id DESC", c, params=(application_id,))


# --------------------------------------------------------------------------- Databricks
def _ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", name or ""):
        raise ValueError(f"Invalid catalog/schema name: {name!r}")
    return name


class DatabricksBackend:
    """Reads and writes Delta tables through a SQL warehouse.

    Authentication uses the Databricks SDK default config. Inside a Databricks App this is the app's
    service principal; locally it uses whatever `databricks auth login` or environment variables provide.
    Every statement opens its own short connection: simple and safe for a POC. If the app gets busy, add pooling.
    The update and its audit rows are separate statements, so they are not atomic.
    """

    def __init__(self, catalog: str | None = None, schema: str | None = None, warehouse_id: str | None = None):
        self.catalog = _ident(catalog or os.environ["CATALOGUE_CATALOG"])
        self.schema = _ident(schema or os.environ["CATALOGUE_SCHEMA"])
        self.warehouse_id = warehouse_id or os.environ["DATABRICKS_WAREHOUSE_ID"]
        self.table = f"`{self.catalog}`.`{self.schema}`.application_catalogue"
        self.audit = f"`{self.catalog}`.`{self.schema}`.application_catalogue_audit"
        self.label = f"Databricks · {self.catalog}.{self.schema}"

    def _connect(self):
        from databricks import sql  # imported lazily so local runs do not need the package
        from databricks.sdk.core import Config

        cfg = Config()
        host = cfg.host.replace("https://", "").rstrip("/")
        return sql.connect(
            server_hostname=host,
            http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
            credentials_provider=lambda: cfg.authenticate,
        )

    def _exec(self, statement: str, params: dict | None = None, fetch: bool = False):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(statement, params or {})
            if fetch:
                cols = [d[0] for d in cur.description]
                return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)
        return None

    @staticmethod
    def _bind(name: str, value, params: dict, is_date: bool = False) -> str:
        """Return a SQL fragment for a value: NULL, or a :named parameter (values are never inlined)."""
        value = _norm(value)
        if value is None:
            return "NULL"
        if is_date:
            value = dt.date.fromisoformat(value)
        params[name] = value
        return f":{name}"

    def load(self) -> pd.DataFrame:
        df = self._exec(f"SELECT * FROM {self.table} ORDER BY application_id", fetch=True)
        for c in COLUMNS:
            if c not in df.columns:
                df[c] = None
        return _finalize(df[COLUMNS])

    def update(self, application_id: str, changes: dict, user: str) -> int:
        old = self._exec(f"SELECT * FROM {self.table} WHERE application_id = :id", {"id": application_id}, fetch=True)
        if old.empty:
            raise KeyError(application_id)
        diffs = compute_changes(old.iloc[0].to_dict(), changes)
        if not diffs:
            return 0
        params: dict = {"id": application_id, "user": user}
        sets = ", ".join(f"{k} = {self._bind('v_' + k, n, params, k in DATE_COLS)}" for k, (_, n) in diffs.items())
        self._exec(f"UPDATE {self.table} SET {sets}, updated_at = current_timestamp(), updated_by = :user"
                   f" WHERE application_id = :id", params)
        for k, (o, n) in diffs.items():
            p = {"id": application_id, "field": k, "user": user,
                 "old": None if o is None else str(o), "new": None if n is None else str(n)}
            o_sql = "NULL" if p["old"] is None else ":old"
            n_sql = "NULL" if p["new"] is None else ":new"
            if p["old"] is None: p.pop("old")
            if p["new"] is None: p.pop("new")
            self._exec(
                f"INSERT INTO {self.audit} (application_id, action, field, old_value, new_value, changed_by, changed_at)"
                f" VALUES (:id, 'update', :field, {o_sql}, {n_sql}, :user, current_timestamp())", p)
        return len(diffs)

    def insert(self, record: dict, user: str) -> str:
        name = _norm(record.get("application_name"))
        if not name:
            raise ValueError("application_name is required")
        ids = self._exec(f"SELECT application_id FROM {self.table}", fetch=True)["application_id"].tolist()
        new_id = _next_id(ids)  # two people adding at the same instant could collide; acceptable for a POC
        params: dict = {"id": new_id, "user": user, "origin": "App-added"}
        cols, vals = ["application_id", "record_origin", "updated_at", "updated_by"], [":id", ":origin", "current_timestamp()", ":user"]
        for k in NEW_FIELDS:
            cols.append(k)
            vals.append(self._bind("v_" + k, record.get(k), params, k in DATE_COLS))
        self._exec(f"INSERT INTO {self.table} ({', '.join(cols)}) VALUES ({', '.join(vals)})", params)
        self._exec(
            f"INSERT INTO {self.audit} (application_id, action, field, old_value, new_value, changed_by, changed_at)"
            " VALUES (:id, 'create', 'application_name', NULL, :name, :user, current_timestamp())",
            {"id": new_id, "name": name, "user": user})
        return new_id

    def history(self, application_id: str) -> pd.DataFrame:
        return self._exec(
            f"SELECT changed_at, action, field, old_value, new_value, changed_by FROM {self.audit}"
            " WHERE application_id = :id ORDER BY changed_at DESC", {"id": application_id}, fetch=True)

    def bulk_load(self, df: pd.DataFrame, batch: int = 25) -> int:
        """One-off initial load of the workbook rows into an empty table (see seed_databricks.py)."""
        cols = list(df.columns)
        n = 0
        for start in range(0, len(df), batch):
            chunk = df.iloc[start:start + batch]
            params: dict = {}
            rows = []
            for i, rec in enumerate(chunk.to_dict("records")):
                rows.append("(" + ", ".join(
                    self._bind(f"r{i}_{c}", rec[c], params, c in DATE_COLS) for c in cols) + ")")
            self._exec(f"INSERT INTO {self.table} ({', '.join(cols)}) VALUES {', '.join(rows)}", params)
            n += len(chunk)
        return n


def get_backend():
    kind = os.getenv("CATALOGUE_BACKEND", "sqlite").lower()
    if kind == "databricks":
        return DatabricksBackend()
    if kind == "sqlite":
        return SqliteBackend()
    raise ValueError(f"Unknown CATALOGUE_BACKEND: {kind!r}")
