"""IT Application Catalogue & Contract Register: Streamlit app (local SQLite or Databricks)."""
from pathlib import Path

import pandas as pd
import streamlit as st

from data_layer import (
    CONTRACT_DATA_VALUES, DEFAULT_RENEWAL_WINDOW_DAYS, DOMAIN_VALUES, PORTFOLIO_VALUES,
    add_status, get_backend,
)

st.set_page_config(page_title="IT Application Catalogue", page_icon="📋", layout="wide")

LOGO = Path(__file__).parent / "assets" / "landal_logo.png"

DISPLAY = [
    ("application_id", "ApplicationID"), ("application_name", "ApplicationName"),
    ("portfolio_status", "PortfolioStatus"), ("domain", "Domain"),
    ("contract_data_status", "ContractDataStatus"), ("status", "Status"),
    ("year", "Year"), ("quarter", "Quarter"), ("month", "Month"), ("day", "Day"),
    ("rolling_term", "RollingTerm"), ("notice_period", "NoticePeriod"),
    ("business_owner", "BusinessOwner"), ("business_spoc", "BusinessSPOC"), ("it_owner", "ITOwner"),
]
# (column, label). Row 1 holds the first four, row 2 the last four, as in the Power BI sample.
FILTERS = [
    ("status", "Status"), ("portfolio_status", "PortfolioStatus"), ("business_owner", "BusinessOwner"),
    ("it_owner", "ITOwner"), ("application_name", "ApplicationName"), ("domain", "Domain"),
    ("business_spoc", "BusinessSPOC"), ("contract_data_status", "ContractDataStatus"),
]
STATUS_ORDER = ["Active", "Renewal Due", "Expired", "No Contract"]
STATUS_STYLE = {
    "Active": "background-color:#12e65c;color:#101010", "Renewal Due": "background-color:#e8d417;color:#101010",
    "Expired": "background-color:#d9434b;color:#ffffff", "No Contract": "background-color:#9a9a9a;color:#101010",
}
PORTFOLIO_STYLE = {
    "Keep": "background-color:#d5eee3", "Revisit": "background-color:#fbe9b7",
    "Sunset": "background-color:#ecb6b3", "Unknown": "background-color:#e0e0e0",
}

st.markdown(
    """
    <style>
      .block-container{padding-top:1.4rem;max-width:1500px}
      .app-title{font-size:1.9rem;font-weight:700;margin:0;line-height:1.15}
      .app-sub{color:#5a7fa0;margin:.15rem 0 0}
      .kpis{display:grid;grid-template-columns:1fr 1fr;gap:8px}
      .kpi{border-radius:6px;padding:7px 12px 8px;box-shadow:0 1px 2px rgba(0,0,0,.08)}
      .kpi .l{font-size:.78rem;font-weight:600}.kpi .v{font-size:1.45rem;font-weight:700;line-height:1.15}
      .k-total{background:#d4eee3;color:#16322a;border-top:6px solid #8fd3b6}
      .k-due{background:#d9c914;color:#2b2800}.k-exp{background:#cf4d47;color:#fff}.k-none{background:#7a7a7a;color:#fff}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- plumbing
@st.cache_resource
def backend():
    return get_backend()


@st.cache_data(ttl=30, show_spinner="Loading applications…")
def load_data():
    return backend().load()


def current_user() -> str:
    """Databricks Apps forwards the signed-in user's identity in request headers. Locally there is none."""
    try:
        h = st.context.headers
        return h.get("X-Forwarded-Email") or h.get("X-Forwarded-Preferred-Username") or h.get("X-Forwarded-User") or "local-user"
    except Exception:
        return "local-user"


def _s(v) -> str:
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)


def _d(v):
    return None if v is None or pd.isna(v) else pd.Timestamp(v).date()


def _select(label, options, current, key):
    opts = list(options)
    if current and current not in opts:
        opts.insert(0, current)
    return st.selectbox(label, opts, index=opts.index(current) if current in opts else 0, key=key)


def _fields(row: dict, prefix: str) -> dict:
    """The shared edit/add form body. Returns the entered values keyed by column name."""
    a, b = st.columns(2)
    with a:
        portfolio = _select("Portfolio status", PORTFOLIO_VALUES, _s(row.get("portfolio_status")) or "Unknown", f"{prefix}_portfolio")
        owner = st.text_input("Business owner", _s(row.get("business_owner")), key=f"{prefix}_bo")
        it_owner = st.text_input("IT owner", _s(row.get("it_owner")), key=f"{prefix}_ito")
        start = st.date_input("Contract start", _d(row.get("contract_start_date")), format="YYYY-MM-DD", key=f"{prefix}_start")
        rolling = st.text_input("Rolling / auto-renew", _s(row.get("rolling_term")), key=f"{prefix}_roll")
    with b:
        domain = _select("Domain", DOMAIN_VALUES, _s(row.get("domain")) or "TBD", f"{prefix}_domain")
        spoc = st.text_input("Business SPOC", _s(row.get("business_spoc")), key=f"{prefix}_bs")
        cds = _select("Contract data status", CONTRACT_DATA_VALUES, _s(row.get("contract_data_status")) or "Not Stated", f"{prefix}_cds")
        end = st.date_input("Contract end", _d(row.get("contract_end_date")), format="YYYY-MM-DD", key=f"{prefix}_end")
        notice = st.text_input("Notice period", _s(row.get("notice_period")), key=f"{prefix}_notice")
    comments = st.text_area("Comments", _s(row.get("comments")), key=f"{prefix}_comments")
    return {
        "portfolio_status": portfolio, "domain": domain, "contract_data_status": cds,
        "contract_start_date": start, "contract_end_date": end, "rolling_term": rolling, "notice_period": notice,
        "business_owner": owner, "business_spoc": spoc, "it_owner": it_owner, "comments": comments,
    }


def _date_error(v: dict):
    s, e = v["contract_start_date"], v["contract_end_date"]
    return "Contract end must be on or after the contract start." if s and e and e < s else None


@st.dialog("Edit application", width="large")
def edit_dialog(app_id: str):
    row = load_data().set_index("application_id").loc[app_id].to_dict()
    nonce = st.session_state.get("nonce", 0)  # bumped after every save so form widgets never show stale values
    st.markdown(f"**{app_id} · {row['application_name']}**")
    if _s(row.get("description")):
        st.caption(_s(row["description"]))
    with st.form("edit_form"):
        values = _fields(row, f"edit_{app_id}_{nonce}")
        saved = st.form_submit_button("Save changes", type="primary")
    if saved:
        if (err := _date_error(values)):
            st.error(err)
            return
        n = backend().update(app_id, values, current_user())
        load_data.clear()
        st.session_state["nonce"] = nonce + 1
        st.session_state["flash"] = f"{app_id}: {n} field(s) updated." if n else f"{app_id}: nothing changed."
        st.rerun()


@st.dialog("Add application", width="large")
def add_dialog():
    nonce = st.session_state.get("nonce", 0)
    with st.form("add_form"):
        name = st.text_input("Application name *", key=f"add_name_{nonce}")
        desc = st.text_input("Description", key=f"add_desc_{nonce}")
        values = _fields({}, f"add_{nonce}")
        saved = st.form_submit_button("Create application", type="primary")
    if saved:
        if not name.strip():
            st.error("Please enter an application name.")
            return
        if (err := _date_error(values)):
            st.error(err)
            return
        new_id = backend().insert({**values, "application_name": name, "description": desc}, current_user())
        load_data.clear()
        st.session_state["nonce"] = nonce + 1
        st.session_state["flash"] = f"Created {new_id}."
        st.rerun()


# --------------------------------------------------------------------------- page
if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"))

with st.sidebar:
    st.subheader("Settings")
    window = st.number_input("Renewal window (days)", min_value=1, max_value=1095, value=DEFAULT_RENEWAL_WINDOW_DAYS, step=30,
                             help="A contract ending within this many days is counted as Renewal Due.")
    if st.button("Refresh data"):
        load_data.clear()
        st.rerun()
    st.caption(f"Data source: {backend().label}")
    st.caption(f"Signed in as: {current_user()}")

df = add_status(load_data(), int(window))

head_logo, head_title, head_kpi = st.columns([1, 5, 5], vertical_alignment="center")
with head_logo:
    if LOGO.exists():
        st.image(str(LOGO), width=110)
with head_title:
    st.markdown('<p class="app-title">IT Application Catalogue &amp; Contract Register</p>'
                '<p class="app-sub">Application ownership, contract coverage and renewal risk</p>', unsafe_allow_html=True)
kpi_slot = head_kpi.empty()

# Filters (two rows of four, Reset beside the first row)
options = {}
for col, _ in FILTERS:
    vals = df[col].fillna("(blank)").astype(str).unique().tolist()
    options[col] = ["All"] + (STATUS_ORDER if col == "status" else sorted(vals, key=str.lower))
    if st.session_state.get(f"f_{col}", "All") not in options[col]:
        st.session_state[f"f_{col}"] = "All"


def _reset():
    for c, _ in FILTERS:
        st.session_state[f"f_{c}"] = "All"


with st.container(border=True):
    row1 = st.columns([1, 1, 1, 1, 0.6], vertical_alignment="bottom")
    row2 = st.columns([1, 1, 1, 1, 0.6])
    for i, (col, label) in enumerate(FILTERS):
        (row1 if i < 4 else row2)[i % 4].selectbox(label, options[col], key=f"f_{col}")
    row1[4].button("Reset Filters", on_click=_reset)


def apply_filters(frame: pd.DataFrame, skip: str | None = None) -> pd.DataFrame:
    mask = pd.Series(True, index=frame.index)
    for col, _ in FILTERS:
        chosen = st.session_state.get(f"f_{col}", "All")
        if col != skip and chosen != "All":
            mask &= frame[col].fillna("(blank)").astype(str).eq(chosen)
    return frame[mask]


# KPI tiles follow every filter except Status, so the four numbers stay comparable.
kpi_base = apply_filters(df, skip="status")
count = lambda s: int((kpi_base["status"] == s).sum())
kpi_slot.markdown(
    f"""<div class="kpis">
      <div class="kpi k-total"><div class="l">Total Applications</div><div class="v">{len(kpi_base)}</div></div>
      <div class="kpi k-due"><div class="l">Renewal Due Applications</div><div class="v">{count('Renewal Due')}</div></div>
      <div class="kpi k-exp"><div class="l">Expired Contracts</div><div class="v">{count('Expired')}</div></div>
      <div class="kpi k-none"><div class="l">No Contract Applications</div><div class="v">{count('No Contract')}</div></div>
    </div>""",
    unsafe_allow_html=True,
)

shown = apply_filters(df).sort_values("application_id")
disp = shown[[c for c, _ in DISPLAY]].rename(columns=dict(DISPLAY)).reset_index(drop=True)

tool_a, tool_b, tool_c = st.columns([2, 2, 6], vertical_alignment="center")
if tool_a.button("+ Add application", type="primary"):
    add_dialog()
tool_b.download_button("Download CSV", disp.to_csv(index=False).encode("utf-8"), "application_catalogue.csv", "text/csv")
tool_c.caption(f"Status as of {pd.Timestamp.today():%d %b %Y}. Select a row, then choose Edit.")

styler = (
    disp.style
    .map(lambda v: STATUS_STYLE.get(v, ""), subset=["Status"])
    .map(lambda v: PORTFOLIO_STYLE.get(v, ""), subset=["PortfolioStatus"])
    .format(na_rep="")
)
table_args = dict(hide_index=True, on_select="rerun", selection_mode="single-row", height=520, key="table")
try:
    event = st.dataframe(styler, use_container_width=True, **table_args)
except TypeError:  # newer Streamlit versions replaced the argument
    event = st.dataframe(styler, width="stretch", **table_args)

st.markdown(f"**Total** {len(disp)} of {len(df)} applications")

picked = list(event.selection.rows) if event is not None and event.selection.rows else []
if picked:
    app_id = disp.iloc[picked[0]]["ApplicationID"]
    name = disp.iloc[picked[0]]["ApplicationName"]
    if st.button(f"Edit {app_id} · {name}"):
        edit_dialog(app_id)
    with st.expander("Change history"):
        hist = backend().history(app_id)
        st.dataframe(hist, hide_index=True) if len(hist) else st.caption("No changes recorded yet.")
