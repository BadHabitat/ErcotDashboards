import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -----------------------------
# CONFIG
# -----------------------------
AUTH_URL = (
    "https://ercotb2c.b2clogin.com/"
    "ercotb2c.onmicrosoft.com/"
    "B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
)

CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"
SCOPE = f"openid {CLIENT_ID} offline_access"

st.set_page_config(page_title="ERCOT Dashboard", layout="wide")


# -----------------------------
# SESSION HELPERS
# -----------------------------
def clear_login_state() -> None:
    for key in [
        "ercot_username",
        "ercot_password",
        "ercot_subscription_key",
        "ercot_logged_in",
    ]:
        st.session_state.pop(key, None)


def get_session_credentials() -> tuple[str, str, str]:
    username = st.session_state.get("ercot_username", "").strip()
    password = st.session_state.get("ercot_password", "")
    subscription_key = st.session_state.get("ercot_subscription_key", "").strip()

    if not username or not password or not subscription_key:
        raise ValueError("Missing ERCOT login credentials in session.")

    return username, password, subscription_key


# -----------------------------
# AUTH TEST
# -----------------------------
def test_ercot_credentials(username: str, password: str, subscription_key: str) -> None:
    token = get_id_token(username, password)

    test_headers = {
        "Authorization": f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": subscription_key,
    }

    # Small public test call to confirm auth + subscription key
    r = requests.get(
        "https://api.ercot.com/api/public-reports/np6-86-cd",
        headers=test_headers,
        timeout=60,
    )
    r.raise_for_status()


# -----------------------------
# LOGIN FORM
# -----------------------------
def auth_form() -> None:
    st.title("ERCOT Dashboard Login")

    with st.form("ercot_login_form", clear_on_submit=False):
        username = st.text_input("ERCOT Username")
        password = st.text_input("ERCOT Password", type="password")
        subscription_key = st.text_input("ERCOT Subscription Key", type="password")

        submitted = st.form_submit_button("Log In")

    if not submitted:
        return

    username = username.strip()
    subscription_key = subscription_key.strip()

    if not username or not password or not subscription_key:
        st.error("Enter username, password, and subscription key.")
        st.stop()

    try:
        test_ercot_credentials(username, password, subscription_key)
    except requests.HTTPError as e:
        st.error(f"Login failed: {e}")
        st.stop()
    except Exception as e:
        st.error(f"Unable to validate credentials: {e}")
        st.stop()

    st.session_state["ercot_username"] = username
    st.session_state["ercot_password"] = password
    st.session_state["ercot_subscription_key"] = subscription_key
    st.session_state["ercot_logged_in"] = True

    # clear cached auth/data from any prior session
    get_id_token.clear()
    st.cache_data.clear()
    st.rerun()


def logout_button() -> None:
    with st.sidebar:
        st.markdown("### Session")
        logged_in_user = st.session_state.get("ercot_username")
        if logged_in_user:
            st.caption(f"Signed in as: {logged_in_user}")

        if st.button("Log Out", use_container_width=True):
            clear_login_state()
            get_id_token.clear()
            st.cache_data.clear()
            st.rerun()


# -----------------------------
# AUTH
# -----------------------------
@st.cache_data(ttl=3300, show_spinner=False)
def get_id_token(username: str, password: str) -> str:
    payload = {
        "username": username,
        "password": password,
        "grant_type": "password",
        "scope": SCOPE,
        "client_id": CLIENT_ID,
        "response_type": "id_token",
    }

    r = requests.post(
        AUTH_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=100,
    )
    r.raise_for_status()

    body = r.json()
    token = body.get("id_token")
    if not token:
        raise ValueError("ERCOT auth response did not include an id_token.")

    return token


def get_headers() -> dict:
    username, password, subscription_key = get_session_credentials()
    token = get_id_token(username, password)

    return {
        "Authorization": f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": subscription_key,
    }


# -----------------------------
# LOGIN GATE
# -----------------------------
if not st.session_state.get("ercot_logged_in", False):
    auth_form()
    st.stop()

logout_button()

# TAB 1 - keep exactly as-is
PRODUCT_URL = "https://api.ercot.com/api/public-reports/np6-86-cd"

# TAB 2 - replace with the ERCOT wind product you want
WIND_PRODUCT_URL = "https://data.ercot.com/data-product-archive/NP4-743-CD"

# Refresh every 5 minutes
# st_autorefresh(interval=300_000, key="ercot_refresh")

st.set_page_config(page_title="ERCOT Dashboard", layout="wide")

st.title("ERCOT Dashboard")

with st.sidebar:
    st.markdown("### Navigation")
    page = st.radio(
        "Select Page",
        [
            "SCED Constraints",
            "Wind Trader View",
            "Solar Trader View",
            "Load Forecast View",
        ],
        key="main_nav"
    )


# -----------------------------
# GENERIC HELPERS FOR ENDPOINTS
# -----------------------------
@st.cache_data(ttl=300)
def get_artifact_endpoint(product_url: str) -> str:
    r = requests.get(product_url, headers=get_headers(), timeout=60)
    r.raise_for_status()
    product = r.json()

    artifacts = product.get("artifacts", [])
    if not artifacts:
        raise ValueError(f"No artifacts found for product: {product_url}")

    return artifacts[0]["_links"]["endpoint"]["href"]


@st.cache_data(ttl=300)
def load_report_from_product(product_url: str, size: int = 100) -> tuple[pd.DataFrame, str]:
    endpoint = get_artifact_endpoint(product_url)

    params = {
        "size": size
    }

    r = requests.get(endpoint, headers=get_headers(), params=params, timeout=100)
    r.raise_for_status()
    payload = r.json()

    fields = payload.get("fields", [])
    data_rows = payload.get("data", [])

    if not fields:
        raise ValueError("No fields returned from ERCOT.")
    if data_rows is None:
        raise ValueError("No data returned from ERCOT.")

    columns = [f["name"] for f in fields]
    df = pd.DataFrame(data_rows, columns=columns)

    return df, endpoint


# -----------------------------
# HELPERS
# -----------------------------
def pick_shadow_price_column(df: pd.DataFrame) -> str:
    candidates = [
        c for c in df.columns
        if "shadow" in c.lower() and "max" not in c.lower()
    ]
    if not candidates:
        raise ValueError(f"Could not find shadow price column. Columns: {list(df.columns)}")
    return candidates[0]


def convert_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Convert date/time-like columns
    for c in df.columns:
        cl = c.lower()
        if "date" in cl or "time" in cl or "interval" in cl or "timestamp" in cl or "datetime" in cl:
            try:
                df[c] = pd.to_datetime(df[c], errors="ignore")
            except Exception:
                pass

    # Convert numeric-like columns
    for c in df.columns:
        if df[c].dtype == object:
            converted = pd.to_numeric(df[c], errors="ignore")
            df[c] = converted

    return df


def find_timestamp_column(df: pd.DataFrame) -> str | None:
    preferred = [
        "timestamp",
        "postedDatetime",
        "intervalEnding",
        "scedTimestamp",
        "datetime",
    ]

    for p in preferred:
        if p in df.columns:
            return p

    candidates = [c for c in df.columns if "timestamp" in c.lower()]
    if candidates:
        return candidates[0]

    candidates = [
        c for c in df.columns
        if "posted" in c.lower()
        or "datetime" in c.lower()
        or "time" in c.lower()
        or "date" in c.lower()
    ]
    return candidates[0] if candidates else None


def build_row_signature(df: pd.DataFrame) -> pd.Series:
    cols = [c for c in df.columns if c != "_is_new"]
    temp = df[cols].copy()

    for c in temp.columns:
        temp[c] = temp[c].map(lambda x: "" if pd.isna(x) else str(x))

    return temp.apply(lambda row: " | ".join(row.tolist()), axis=1)

    # -----------------------------
    # TAB 1 - ORIGINAL REPORT
    # -----------------------------
if page == "SCED Constraints":
        st.caption("Showing only rows where shadow price > 0")

        try:
            # -----------------------------
            # LOAD + PREP
            # -----------------------------
            df, endpoint = load_report_from_product(PRODUCT_URL)
            df = convert_columns(df)

            shadow_col = pick_shadow_price_column(df)
            df[shadow_col] = pd.to_numeric(df[shadow_col], errors="coerce")

            filtered = df[df[shadow_col] > 0].copy()
            timestamp_col = find_timestamp_column(filtered)

            if filtered.empty:
                st.warning("No rows found where shadow price > 0.")
                st.stop()

            # -----------------------------
            # DETECT NEW ROWS SINCE LAST REFRESH
            # -----------------------------
            row_sigs = build_row_signature(filtered)
            current_signatures = set(row_sigs)
            previous_signatures = st.session_state.get("previous_signatures_tab1", set())
            new_signatures = current_signatures - previous_signatures

            filtered["_is_new"] = row_sigs.isin(new_signatures)
            st.session_state["previous_signatures_tab1"] = current_signatures

            new_count = int(filtered["_is_new"].sum())
            if new_count > 0:
                st.warning(f"New positive-shadow-price rows since last refresh: {new_count}")
            else:
                st.info("No new positive-shadow-price rows since last refresh.")

            # -----------------------------
            # CONTROLS
            # -----------------------------
            st.subheader("Display Controls")

            default_cols = []
            for c in [
                "_is_new",
                timestamp_col,
                "postedDatetime",
                "intervalEnding",
                "constraintName",
                "contingencyName",
                "elementName",
                "fromStationName",
                "toStationName",
                "kv",
                shadow_col,
                "maxShadowPrice",
                "limit",
                "flow",
            ]:
                if c and c in filtered.columns and c not in default_cols:
                    default_cols.append(c)

            if not default_cols:
                default_cols = list(filtered.columns[:10])

            selected_cols = st.multiselect(
                "Columns to display",
                options=list(filtered.columns),
                default=default_cols,
                key="tab1_selected_cols",
            )

            sort_candidates = list(filtered.columns)
            default_sort = timestamp_col if timestamp_col in sort_candidates else shadow_col

            col1, col2, col3 = st.columns(3)

            with col1:
                sort_col = st.selectbox(
                    "Sort by",
                    options=sort_candidates,
                    index=sort_candidates.index(default_sort) if default_sort in sort_candidates else 0,
                    key="tab1_sort_col",
                )

            with col2:
                ascending = st.checkbox("Ascending sort", value=False, key="tab1_ascending")

            with col3:
                show_only_new = st.checkbox("Show only new rows", value=False, key="tab1_show_only_new")

            secondary_sort = st.selectbox(
                "Secondary sort (optional)",
                options=["<none>"] + sort_candidates,
                index=0,
                key="tab1_secondary_sort",
            )

            sort_cols = [sort_col]
            sort_dirs = [ascending]

            if secondary_sort != "<none>":
                sort_cols.append(secondary_sort)
                sort_dirs.append(ascending)

            display_df = filtered.copy()

            if show_only_new:
                display_df = display_df[display_df["_is_new"]].copy()

            try:
                display_df = display_df.sort_values(
                    by=sort_cols,
                    ascending=sort_dirs,
                    na_position="last",
                )
            except Exception:
                pass

            if selected_cols:
                display_df = display_df[selected_cols].copy()

            st.subheader("Filtered Table")

            # -----------------------------
            # HIGHLIGHT SHADOW PRICE COLUMN
            # -----------------------------
            def highlight_shadow_column(col):
                if col.name != shadow_col:
                    return [""] * len(col)

                styles = []
                for val in col:
                    try:
                        v = float(val)
                        if v >= 500:
                            styles.append("background-color: #ff4d4d; color: white; font-weight: bold;")
                        elif v >= 100:
                            styles.append("background-color: #ff944d; color: black; font-weight: bold;")
                        elif v >= 25:
                            styles.append("background-color: #ffd24d; color: black;")
                        else:
                            styles.append("background-color: #90ee90; color: black;")
                    except Exception:
                        styles.append("")
                return styles

            if shadow_col in display_df.columns:
                styled_df = display_df.style.apply(highlight_shadow_column, axis=0)
                st.dataframe(
                    styled_df,
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                )

            csv = display_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download visible table as CSV",
                data=csv,
                file_name="np6_86_shadow_price_gt_0_visible_columns.csv",
                mime="text/csv",
                key="tab1_download",
            )

            with st.expander("All columns"):
                st.write(list(filtered.columns))

            if timestamp_col:
                st.info(f"Detected timestamp column: {timestamp_col}")
            else:
                st.warning("Could not detect timestamp column.")

        except Exception as e:
            st.error(str(e))
        # -----------------------------
        # TAB 2 - WIND TRADER VIEW
        # -----------------------------
elif page == "Wind Trader View":
        st.caption("ERCOT Wind Trader View")

        ACTUAL_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-743-cd"
        INTRAHOUR_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-751-cd"
        NP4732_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-732-cd"
        NP4442_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-442-cd"


        # =====================================================
        # API HELPERS
        # =====================================================
        @st.cache_data(ttl=300)
        def get_product_metadata(product_url: str) -> dict:
            r = requests.get(product_url, headers=get_headers(), timeout=60)
            r.raise_for_status()
            return r.json()


        def choose_best_artifact(artifacts):
            if not artifacts:
                raise ValueError("No artifacts returned for product.")

            scored = []
            for a in artifacts:
                endpoint = a.get("_links", {}).get("endpoint", {}).get("href", "")
                name = f"{a.get('friendlyName', '')} {a.get('name', '')}".lower()
                blob = f"{endpoint} {name}".lower()

                score = 0
                if endpoint:
                    score += 1
                if "csv" in blob:
                    score += 10
                if "xml" in blob:
                    score -= 1
                if "zip" in blob:
                    score -= 2
                if "view" in blob or "data" in blob or "report" in blob:
                    score += 2

                scored.append((score, a))

            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1]


        @st.cache_data(ttl=300)
        def get_artifact_endpoint(product_url: str):
            meta = get_product_metadata(product_url)
            artifacts = meta.get("artifacts", [])
            if not artifacts:
                raise ValueError(f"No artifacts found for product: {product_url}")

            best = choose_best_artifact(artifacts)
            endpoint = best.get("_links", {}).get("endpoint", {}).get("href")
            if not endpoint:
                raise ValueError(f"Could not find artifact endpoint for product: {product_url}")

            return endpoint


        @st.cache_data(ttl=300)
        def load_report(endpoint: str, posted_from=None, posted_to=None, size=10000, timeout=60):
            params = {"size": size}
            if posted_from:
                params["postedDatetimeFrom"] = posted_from
            if posted_to:
                params["postedDatetimeTo"] = posted_to

            session = requests.Session()
            retries = Retry(
                total=2,
                backoff_factor=1.0,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"],
            )
            session.mount("https://", HTTPAdapter(max_retries=retries))

            r = session.get(endpoint, headers=get_headers(), params=params, timeout=timeout)
            r.raise_for_status()

            payload = r.json()
            fields = payload.get("fields", [])
            rows = payload.get("data", [])

            if not fields:
                raise ValueError(f"No fields returned from endpoint: {endpoint}")

            cols = [f["name"] for f in fields]
            return pd.DataFrame(rows, columns=cols)


        def safe_load_product(product_url: str, posted_from=None, posted_to=None, size=10000, timeout=60):
            try:
                endpoint = get_artifact_endpoint(product_url)
                df = load_report(endpoint, posted_from=posted_from, posted_to=posted_to, size=size, timeout=timeout)
                return {
                    "ok": True,
                    "endpoint": endpoint,
                    "df": df,
                    "error": None,
                }
            except Exception as e:
                return {
                    "ok": False,
                    "endpoint": None,
                    "df": pd.DataFrame(),
                    "error": str(e),
                }


        # =====================================================
        # GENERIC HELPERS
        # =====================================================
        def normalize_key(s: str) -> str:
            return str(s).strip().lower().replace("_", "").replace("-", "").replace(" ", "")


        def pick_col(df: pd.DataFrame, candidates):
            lower_map = {normalize_key(c): c for c in df.columns}
            for cand in candidates:
                key = normalize_key(cand)
                if key in lower_map:
                    return lower_map[key]
            return None


        def detect_time_col(df: pd.DataFrame):
            preferred = [
                "intervalEnding",
                "timestamp",
                "datetime",
                "deliveryDatetime",
                "deliveryDate",
            ]
            for c in preferred:
                if c in df.columns:
                    return c

            for c in df.columns:
                cl = c.lower()
                if "interval" in cl and "ending" in cl:
                    return c
                if "timestamp" in cl or "datetime" in cl:
                    return c
            return None


        def detect_posted_col(df: pd.DataFrame):
            preferred = [
                "postedDatetime",
                "postedTime",
                "publishTime",
                "issueTime",
                "createdDatetime",
            ]
            for c in preferred:
                if c in df.columns:
                    return c

            for c in df.columns:
                cl = c.lower()
                if "posted" in cl or "publish" in cl or "issue" in cl or "created" in cl:
                    return c
            return None


        def detect_target_col(df: pd.DataFrame, posted_col=None):
            preferred = [
                "intervalEnding",
                "forecastTime",
                "deliveryTime",
                "deliveryDatetime",
                "timestamp",
                "datetime",
            ]
            for c in preferred:
                if c in df.columns and c != posted_col:
                    return c

            for c in df.columns:
                if c == posted_col:
                    continue
                cl = c.lower()
                if "interval" in cl and "ending" in cl:
                    return c
                if "forecast" in cl and "time" in cl:
                    return c
                if "delivery" in cl and ("time" in cl or "date" in cl):
                    return c
                if "timestamp" in cl or "datetime" in cl:
                    return c
            return None


        def align_index(df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp):
            if df.empty:
                return df
            return df[(df.index >= start_ts) & (df.index <= end_ts)].copy()


        def build_base_figure(height=500, yaxis_title="MW"):
            fig = go.Figure()
            fig.update_layout(
                height=height,
                hovermode="x unified",
                yaxis_title=yaxis_title,
                xaxis_title="Time",
                legend_title_text="Series",
                margin=dict(l=20, r=20, t=20, b=20),
            )
            return fig


        def make_error_frame(actual_series: pd.Series, forecast_series: pd.Series):
            combined = pd.concat(
                [
                    actual_series.rename("actual"),
                    forecast_series.rename("forecast"),
                ],
                axis=1
            ).dropna()

            if combined.empty:
                return combined

            combined["error_mw"] = combined["forecast"] - combined["actual"]
            combined["abs_error_mw"] = combined["error_mw"].abs()
            combined["pct_error"] = np.where(
                combined["actual"].abs() > 1e-9,
                (combined["error_mw"] / combined["actual"]) * 100.0,
                np.nan
            )
            combined["ape"] = combined["pct_error"].abs()
            return combined


        # =====================================================
        # MAIN WIND REGION HELPERS (NP4-743 / NP4-751)
        # =====================================================
        def main_region_order():
            return ["ERCOT Total", "Panhandle", "Coastal", "South", "West", "North"]


        def main_region_aliases():
            return {
                "panhandle": "Panhandle",
                "coastal": "Coastal",
                "south": "South",
                "west": "West",
                "north": "North",
                "systemwide": "ERCOT Total",
                "systemtotal": "ERCOT Total",
                "ercottotal": "ERCOT Total",
                "total": "ERCOT Total",
            }


        def normalize_main_region_name(x):
            if pd.isna(x):
                return None
            s_norm = normalize_key(x)
            aliases = main_region_aliases()
            for alias, display in aliases.items():
                if s_norm == alias or alias in s_norm:
                    return display
            return None


        def find_main_wide_region_columns(df: pd.DataFrame):
            aliases = main_region_aliases()
            found = {}

            for col in df.columns:
                norm = normalize_key(col)
                for alias, display in aliases.items():
                    if alias in norm:
                        found[display] = col

            return found


        def find_long_region_and_value_columns(df: pd.DataFrame):
            region_col = None
            value_col = None

            for c in df.columns:
                cl = c.lower()
                if region_col is None and ("region" in cl or "zone" in cl or "geograph" in cl):
                    region_col = c

            for c in df.columns:
                cl = c.lower()
                if any(x in cl for x in ["mw", "gen", "actual", "forecast", "output", "production", "value", "hsl"]):
                    numeric_test = pd.to_numeric(df[c], errors="coerce")
                    if numeric_test.notna().sum() > 0:
                        value_col = c
                        break

            return region_col, value_col


        def add_main_total_if_missing(df: pd.DataFrame):
            parts = [c for c in ["Panhandle", "Coastal", "South", "West", "North"] if c in df.columns]
            if "ERCOT Total" not in df.columns and parts:
                df["ERCOT Total"] = df[parts].sum(axis=1, min_count=1)

            ordered = [c for c in main_region_order() if c in df.columns]
            return df[ordered] if ordered else df


        def normalize_actual_regional_df(df: pd.DataFrame):
            df = convert_columns(df.copy())

            time_col = detect_time_col(df)
            if not time_col:
                raise ValueError(f"Actuals: Could not detect timestamp column. Columns: {list(df.columns)}")

            df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
            df = df.dropna(subset=[time_col]).copy()

            wide_cols = find_main_wide_region_columns(df)
            if wide_cols:
                out = df[[time_col] + list(wide_cols.values())].copy()
                out = out.rename(columns={v: k for k, v in wide_cols.items()})

                for c in out.columns:
                    if c != time_col:
                        out[c] = pd.to_numeric(out[c], errors="coerce")

                out = (
                    out.sort_values(time_col)
                    .drop_duplicates(subset=[time_col], keep="last")
                    .set_index(time_col)
                    .resample("5min")
                    .mean()
                )

                return add_main_total_if_missing(out), time_col

            region_col, value_col = find_long_region_and_value_columns(df)
            if region_col and value_col:
                temp = df[[time_col, region_col, value_col]].copy()
                temp[value_col] = pd.to_numeric(temp[value_col], errors="coerce")
                temp = temp.dropna(subset=[value_col]).copy()
                temp["series"] = temp[region_col].map(normalize_main_region_name)
                temp = temp.dropna(subset=["series"]).copy()

                out = (
                    temp.pivot_table(
                        index=time_col,
                        columns="series",
                        values=value_col,
                        aggfunc="mean"
                    )
                    .sort_index()
                    .resample("5min")
                    .mean()
                )

                return add_main_total_if_missing(out), time_col

            raise ValueError(f"Actuals: Could not detect regional actuals format. Columns: {list(df.columns)}")


        def normalize_intrahour_forecast_long(df: pd.DataFrame):
            df = convert_columns(df.copy())

            posted_col = detect_posted_col(df)
            target_col = detect_target_col(df, posted_col)

            if not posted_col:
                raise ValueError(
                    f"Intra-hour forecast: Could not detect posted timestamp column. Columns: {list(df.columns)}")
            if not target_col:
                raise ValueError(
                    f"Intra-hour forecast: Could not detect target timestamp column. Columns: {list(df.columns)}")

            df[posted_col] = pd.to_datetime(df[posted_col], errors="coerce")
            df[target_col] = pd.to_datetime(df[target_col], errors="coerce")
            df = df.dropna(subset=[posted_col, target_col]).copy()

            wide_cols = find_main_wide_region_columns(df)
            if wide_cols:
                keep_cols = [posted_col, target_col] + list(wide_cols.values())
                out = df[keep_cols].copy()
                out = out.rename(columns={v: k for k, v in wide_cols.items()})

                region_names = [c for c in main_region_order() if c in out.columns]
                for c in region_names:
                    out[c] = pd.to_numeric(out[c], errors="coerce")

                long_df = out.melt(
                    id_vars=[posted_col, target_col],
                    value_vars=region_names,
                    var_name="series",
                    value_name="mw"
                ).dropna(subset=["mw"])

                long_df = long_df.rename(columns={
                    posted_col: "posted_ts",
                    target_col: "target_ts"
                })

                return long_df, posted_col, target_col

            region_col, value_col = find_long_region_and_value_columns(df)
            if region_col and value_col:
                temp = df[[posted_col, target_col, region_col, value_col]].copy()
                temp[value_col] = pd.to_numeric(temp[value_col], errors="coerce")
                temp = temp.dropna(subset=[value_col]).copy()
                temp["series"] = temp[region_col].map(normalize_main_region_name)
                temp = temp.dropna(subset=["series"]).copy()

                temp = temp.rename(columns={
                    posted_col: "posted_ts",
                    target_col: "target_ts",
                    value_col: "mw"
                })

                return temp[["posted_ts", "target_ts", "series", "mw"]], posted_col, target_col

            raise ValueError(
                f"Intra-hour forecast: Could not detect regional forecast format. Columns: {list(df.columns)}")


        def build_intrahour_lead_curve(forecast_long: pd.DataFrame, target_lead_minutes: int):
            df = forecast_long.copy()
            df["lead_minutes"] = (df["target_ts"] - df["posted_ts"]).dt.total_seconds() / 60.0
            df = df[df["lead_minutes"] >= 0].copy()
            df = df[df["lead_minutes"] <= 180].copy()

            if df.empty:
                return pd.DataFrame()

            df["score"] = (df["lead_minutes"] - target_lead_minutes).abs()
            df = df.sort_values(
                by=["series", "target_ts", "score", "posted_ts"],
                ascending=[True, True, True, False]
            )

            picked = df.groupby(["series", "target_ts"], as_index=False).first()

            wide = (
                picked.pivot_table(
                    index="target_ts",
                    columns="series",
                    values="mw",
                    aggfunc="mean"
                )
                .sort_index()
                .resample("5min")
                .mean()
            )

            return add_main_total_if_missing(wide)


        # =====================================================
        # NP4-732 PARSER
        # =====================================================
        def normalize_np4732_hourly(df: pd.DataFrame):
            df = df.copy()

            # -----------------------------
            # flexible column picker
            # -----------------------------
            def norm(s):
                return str(s).strip().lower().replace("_", "").replace("-", "").replace(" ", "")

            col_map = {norm(c): c for c in df.columns}

            def pick(*candidates):
                for cand in candidates:
                    key = norm(cand)
                    if key in col_map:
                        return col_map[key]
                return None

            delivery_col = pick("DELIVERY_DATE", "deliveryDate")
            he_col = pick("HOUR_ENDING", "hourEnding")
            posted_col = pick("postedDatetime")
            dst_col = pick("DSTFlag")

            missing = []
            if not delivery_col:
                missing.append("DELIVERY_DATE/deliveryDate")
            if not he_col:
                missing.append("HOUR_ENDING/hourEnding")

            if missing:
                raise ValueError(
                    f"NP4-732 missing required columns: {missing}. Columns: {list(df.columns)}"
                )

            df[delivery_col] = pd.to_datetime(df[delivery_col], errors="coerce")
            df[he_col] = pd.to_numeric(df[he_col], errors="coerce")
            if posted_col:
                df[posted_col] = pd.to_datetime(df[posted_col], errors="coerce")

            df = df.dropna(subset=[delivery_col, he_col]).copy()

            # ERCOT HE 1-24 => ending timestamp
            df["target_ts"] = df[delivery_col] + pd.to_timedelta(df[he_col], unit="h")

            # -----------------------------
            # column mappings for both schema variants
            # -----------------------------
            metric_region_map = {
                # system wide
                "SYSTEM_WIDE_GEN": ("Actual Gen", "ERCOT Total"),
                "genSystemWide": ("Actual Gen", "ERCOT Total"),

                "COP_HSL_SYSTEM_WIDE": ("COP HSL", "ERCOT Total"),
                "COPHSLSystemWide": ("COP HSL", "ERCOT Total"),

                "STWPF_SYSTEM_WIDE": ("STWPF", "ERCOT Total"),
                "STWPFSystemWide": ("STWPF", "ERCOT Total"),

                "WGRPP_SYSTEM_WIDE": ("WGRPP", "ERCOT Total"),
                "WGRPPSystemWide": ("WGRPP", "ERCOT Total"),

                "SYSTEM_WIDE_HSL": ("System HSL", "ERCOT Total"),
                "HSLSystemWide": ("System HSL", "ERCOT Total"),

                # south houston
                "GEN_LZ_SOUTH_HOUSTON": ("Actual Gen", "South Houston"),
                "genLoadZoneSouthHouston": ("Actual Gen", "South Houston"),

                "COP_HSL_LZ_SOUTH_HOUSTON": ("COP HSL", "South Houston"),
                "COPHSLLoadZoneSouthHouston": ("COP HSL", "South Houston"),

                "STWPF_LZ_SOUTH_HOUSTON": ("STWPF", "South Houston"),
                "STWPFLoadZoneSouthHouston": ("STWPF", "South Houston"),

                "WGRPP_LZ_SOUTH_HOUSTON": ("WGRPP", "South Houston"),
                "WGRPPLoadZoneSouthHouston": ("WGRPP", "South Houston"),

                # west
                "GEN_LZ_WEST": ("Actual Gen", "West"),
                "genLoadZoneWest": ("Actual Gen", "West"),

                "COP_HSL_LZ_WEST": ("COP HSL", "West"),
                "COPHSLLoadZoneWest": ("COP HSL", "West"),

                "STWPF_LZ_WEST": ("STWPF", "West"),
                "STWPFLoadZoneWest": ("STWPF", "West"),

                "WGRPP_LZ_WEST": ("WGRPP", "West"),
                "WGRPPLoadZoneWest": ("WGRPP", "West"),

                # north
                "GEN_LZ_NORTH": ("Actual Gen", "North"),
                "genLoadZoneNorth": ("Actual Gen", "North"),

                "COP_HSL_LZ_NORTH": ("COP HSL", "North"),
                "COPHSLLoadZoneNorth": ("COP HSL", "North"),

                "STWPF_LZ_NORTH": ("STWPF", "North"),
                "STWPFLoadZoneNorth": ("STWPF", "North"),

                "WGRPP_LZ_NORTH": ("WGRPP", "North"),
                "WGRPPLoadZoneNorth": ("WGRPP", "North"),
            }

            long_frames = []

            for raw_col, (metric_name, region_name) in metric_region_map.items():
                if raw_col not in df.columns:
                    continue

                temp = df[["target_ts", raw_col]].copy()
                if posted_col:
                    temp["posted_ts"] = df[posted_col]

                temp["metric"] = metric_name
                temp["region"] = region_name
                temp["mw"] = pd.to_numeric(temp[raw_col], errors="coerce")
                temp = temp.dropna(subset=["mw"]).copy()

                keep_cols = ["target_ts", "region", "metric", "mw"]
                if posted_col:
                    keep_cols.insert(1, "posted_ts")

                long_frames.append(temp[keep_cols])

            if not long_frames:
                raise ValueError(
                    f"NP4-732 parser found no usable metric columns. Columns: {list(df.columns)}"
                )

            long_df = pd.concat(long_frames, ignore_index=True)

            # if posted timestamp exists, keep the latest row per target/metric/region
            if "posted_ts" in long_df.columns:
                long_df = (
                    long_df.sort_values(["target_ts", "metric", "region", "posted_ts"])
                    .drop_duplicates(subset=["target_ts", "metric", "region"], keep="last")
                    .copy()
                )

            wide = (
                long_df.pivot_table(
                    index="target_ts",
                    columns=["metric", "region"],
                    values="mw",
                    aggfunc="mean"
                )
                .sort_index()
            )

            return long_df, wide
        # =====================================================
        # OPTIONAL NP4-442 COP MODEL STATUS PARSER
        # =====================================================
        def parse_np4442_cop_model_status(df: pd.DataFrame):
            if df.empty:
                return pd.DataFrame(), None

            work = convert_columns(df.copy())
            posted_col = detect_posted_col(work)
            if posted_col and posted_col in work.columns:
                work[posted_col] = pd.to_datetime(work[posted_col], errors="coerce")

            region_col = pick_col(work, ["Region", "region", "WeatherZone", "zone", "Geography", "geography"])
            model_col = pick_col(work, ["Model", "model", "ForecastModel", "forecastModel", "UsedModel", "usedModel"])
            inuse_col = pick_col(work,
                                 ["InUseFlag", "inUseFlag", "UsedToPopulateCOP", "usedToPopulateCOP", "UsedForCOP",
                                  "usedForCOP", "UsedFlag"])
            target_col = detect_target_col(work, posted_col)

            if region_col is None or model_col is None:
                return pd.DataFrame(), posted_col

            if target_col and target_col in work.columns:
                work[target_col] = pd.to_datetime(work[target_col], errors="coerce")

            # try both main regions and NP4-732 regions
            def normalize_any_region(x):
                v = normalize_main_region_name(x)
                if v:
                    return v
                s = normalize_key(x)
                if "southhouston" in s:
                    return "South Houston"
                return None

            work["Region"] = work[region_col].map(normalize_any_region)
            work = work.dropna(subset=["Region"]).copy()

            if inuse_col and inuse_col in work.columns:
                active = work[
                    work[inuse_col].astype(str).str.strip().str.upper().isin(["Y", "YES", "TRUE", "1"])].copy()
                if not active.empty:
                    work = active

            sort_cols = []
            if posted_col and posted_col in work.columns:
                sort_cols.append(posted_col)
            if target_col and target_col in work.columns:
                sort_cols.append(target_col)

            if sort_cols:
                work = work.sort_values(sort_cols)

            status = (
                work.groupby("Region", as_index=False)
                .tail(1)[["Region", model_col] + ([posted_col] if posted_col and posted_col in work.columns else [])]
                .rename(columns={model_col: "COP Model"})
                .sort_values("Region")
            )

            return status, posted_col


        # =====================================================
        # CONTROLS
        # =====================================================
        try:
            st.subheader("Controls")

            now = pd.Timestamp.now(tz="America/Chicago").tz_localize(None).floor("5min")

            top1, top2, top3 = st.columns(3)

            with top1:
                trader_window = st.selectbox(
                    "Trader View Window",
                    ["Last 24 / Next 6", "Last 24 / Next 24", "Last 12 / Next 6", "Last 48 / Next 24"],
                    index=0,
                    key="wind_trader_window_v6"
                )

            with top2:
                history_window = st.selectbox(
                    "History Window",
                    ["Last 24", "Last 48", "Last 72", "Last 168"],
                    index=0,
                    key="wind_history_window_v6"
                )

            with top3:
                np4732_window = st.selectbox(
                    "NP4-732 Window",
                    ["Next 24", "Next 48", "Next 72", "Next 168", "Trader Span"],
                    index=2,
                    key="wind_np4732_window_v6"
                )

            use_exact_trader_range = st.checkbox(
                "Use exact Trader View range",
                value=False,
                key="wind_exact_trader_range_v6"
            )


            def parse_window(label):
                if label == "Last 24 / Next 6":
                    return now - pd.Timedelta(hours=24), now + pd.Timedelta(hours=6)
                if label == "Last 24 / Next 24":
                    return now - pd.Timedelta(hours=24), now + pd.Timedelta(hours=24)
                if label == "Last 12 / Next 6":
                    return now - pd.Timedelta(hours=12), now + pd.Timedelta(hours=6)
                if label == "Last 48 / Next 24":
                    return now - pd.Timedelta(hours=48), now + pd.Timedelta(hours=24)
                if label == "Last 24":
                    return now - pd.Timedelta(hours=24), now
                if label == "Last 48":
                    return now - pd.Timedelta(hours=48), now
                if label == "Last 72":
                    return now - pd.Timedelta(hours=72), now
                if label == "Last 168":
                    return now - pd.Timedelta(hours=168), now
                if label == "Next 24":
                    return now, now + pd.Timedelta(hours=24)
                if label == "Next 48":
                    return now, now + pd.Timedelta(hours=48)
                if label == "Next 72":
                    return now, now + pd.Timedelta(hours=72)
                if label == "Next 168":
                    return now, now + pd.Timedelta(hours=168)
                return now - pd.Timedelta(hours=24), now + pd.Timedelta(hours=6)


            trader_start, trader_end = parse_window(trader_window)
            history_start, history_end = parse_window(history_window)

            if np4732_window == "Trader Span":
                np4732_start, np4732_end = trader_start, trader_end
            else:
                np4732_start, np4732_end = parse_window(np4732_window)

            if use_exact_trader_range:
                ec1, ec2 = st.columns(2)
                with ec1:
                    trader_start_date = st.date_input("Trader start date", value=trader_start.date(),
                                                      key="wind_trader_start_date_v6")
                    trader_start_time = st.time_input(
                        "Trader start time",
                        value=trader_start.to_pydatetime().time().replace(second=0, microsecond=0),
                        step=300,
                        key="wind_trader_start_time_v6"
                    )
                with ec2:
                    trader_end_date = st.date_input("Trader end date", value=trader_end.date(),
                                                    key="wind_trader_end_date_v6")
                    trader_end_time = st.time_input(
                        "Trader end time",
                        value=trader_end.to_pydatetime().time().replace(second=0, microsecond=0),
                        step=300,
                        key="wind_trader_end_time_v6"
                    )

                trader_start = pd.Timestamp.combine(trader_start_date, trader_start_time).floor("5min")
                trader_end = pd.Timestamp.combine(trader_end_date, trader_end_time).floor("5min")

                if trader_end <= trader_start:
                    st.warning("Trader View end time must be later than start time.")
                    st.stop()

            main_regions = st.multiselect(
                "Main chart regions",
                options=["ERCOT Total", "Panhandle", "Coastal", "South", "West", "North"],
                default=["ERCOT Total"],
                key="wind_main_regions_v6"
            )
            if not main_regions:
                st.warning("Select at least one main chart region.")
                st.stop()

            layer_row1, layer_row2, layer_row3, layer_row4 = st.columns(4)
            with layer_row1:
                show_actual = st.checkbox("Show Actual", value=True, key="wind_show_actual_v6")
            with layer_row2:
                show_latest = st.checkbox("Show Latest Intra-hour", value=True, key="wind_show_latest_v6")
            with layer_row3:
                show_1h = st.checkbox("Show 1h Ago", value=True, key="wind_show_1h_v6")
            with layer_row4:
                show_2h = st.checkbox("Show 2h Ago", value=False, key="wind_show_2h_v6")

            layer_row5, layer_row6, layer_row7 = st.columns(3)
            with layer_row5:
                error_metric = st.selectbox(
                    "Error Metric",
                    ["MW Error", "Absolute Error", "Percent Error"],
                    index=0,
                    key="wind_error_metric_v6"
                )
            with layer_row6:
                include_np4732_in_error = st.checkbox("Include NP4-732 STWPF in error chart", value=True,
                                                      key="wind_include_np4732_error_v6")
            with layer_row7:
                load_np4442 = st.checkbox("Load NP4-442 COP model status", value=False, key="wind_load_np4442_v6")

            st.subheader("NP4-732 Chart Controls")
            npc1, npc2 = st.columns(2)
            with npc1:
                np4732_metric = st.selectbox(
                    "NP4-732 metric",
                    options=["STWPF", "WGRPP", "COP HSL", "Actual Gen", "System HSL"],
                    index=0,
                    key="wind_np4732_metric_v6"
                )
            with npc2:
                np4732_regions = st.multiselect(
                    "NP4-732 regions",
                    options=["ERCOT Total", "South Houston", "West", "North"],
                    default=["ERCOT Total", "West", "North"],
                    key="wind_np4732_regions_v6"
                )
            if not np4732_regions:
                st.warning("Select at least one NP4-732 region.")
                st.stop()

            show_table = st.checkbox("Show data tables", value=False, key="wind_show_tables_v6")

            selected_leads = ["Latest"]
            if show_1h:
                selected_leads.append("1 hour ago")
            if show_2h:
                selected_leads.append("2 hour ago")

            lead_map = {
                "Latest": 0,
                "1 hour ago": 60,
                "2 hour ago": 120,
            }

            st.caption(
                f"Trader: {trader_start:%Y-%m-%d %H:%M} to {trader_end:%Y-%m-%d %H:%M} | "
                f"History: {history_start:%Y-%m-%d %H:%M} to {history_end:%Y-%m-%d %H:%M} | "
                f"NP4-732: {np4732_start:%Y-%m-%d %H:%M} to {np4732_end:%Y-%m-%d %H:%M}"
            )

            # =====================================================
            # LOAD FEEDS
            # =====================================================
            trader_hours = max((trader_end - trader_start).total_seconds() / 3600.0, 0.0)
            if trader_hours <= 24:
                actual_size = 12000
                intrahour_size = 25000
            elif trader_hours <= 72:
                actual_size = 25000
                intrahour_size = 50000
            else:
                actual_size = 40000
                intrahour_size = 90000

            np4732_size = 1000
            np4442_size = 15000

            actual_from = min(trader_start, history_start).strftime("%Y-%m-%dT%H:%M")
            actual_to = max(trader_end, history_end, now).strftime("%Y-%m-%dT%H:%M")

            intrahour_from = (min(trader_start, history_start, now) - pd.Timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
            intrahour_to = max(trader_end, history_end).strftime("%Y-%m-%dT%H:%M")

            actual_res = safe_load_product(
                ACTUAL_PRODUCT_URL,
                posted_from=actual_from,
                posted_to=actual_to,
                size=actual_size,
                timeout=60,
            )

            intrahour_res = safe_load_product(
                INTRAHOUR_PRODUCT_URL,
                posted_from=intrahour_from,
                posted_to=intrahour_to,
                size=intrahour_size,
                timeout=60,
            )

            np4732_res = safe_load_product(
                NP4732_PRODUCT_URL,
                posted_from=None,
                posted_to=None,
                size=np4732_size,
                timeout=45,
            )

            if load_np4442:
                np4442_res = safe_load_product(
                    NP4442_PRODUCT_URL,
                    posted_from=None,
                    posted_to=None,
                    size=np4442_size,
                    timeout=45,
                )
            else:
                np4442_res = {
                    "ok": False,
                    "endpoint": None,
                    "df": pd.DataFrame(),
                    "error": "NP4-442 not loaded",
                }

            # =====================================================
            # NORMALIZE FEEDS
            # =====================================================
            actual_df = pd.DataFrame()
            actual_error = None
            actual_time_col = None
            if actual_res["ok"]:
                try:
                    actual_df, actual_time_col = normalize_actual_regional_df(actual_res["df"])
                except Exception as e:
                    actual_error = str(e)
            else:
                actual_error = actual_res["error"]

            intrahour_long = pd.DataFrame()
            intrahour_error = None
            intrahour_posted_col = None
            intrahour_target_col = None
            if intrahour_res["ok"]:
                try:
                    intrahour_long, intrahour_posted_col, intrahour_target_col = normalize_intrahour_forecast_long(
                        intrahour_res["df"])
                except Exception as e:
                    intrahour_error = str(e)
            else:
                intrahour_error = intrahour_res["error"]

            np4732_long = pd.DataFrame()
            np4732_wide = pd.DataFrame()
            np4732_error = None
            if np4732_res["ok"]:
                try:
                    np4732_long, np4732_wide = normalize_np4732_hourly(np4732_res["df"])
                except Exception as e:
                    np4732_error = str(e)
            else:
                np4732_error = np4732_res["error"]

            cop_status_df = pd.DataFrame()
            cop_posted_col = None
            np4442_error = None
            if np4442_res["ok"]:
                try:
                    cop_status_df, cop_posted_col = parse_np4442_cop_model_status(np4442_res["df"])
                except Exception as e:
                    np4442_error = str(e)
            else:
                np4442_error = np4442_res["error"]

            actual_trader = align_index(actual_df, trader_start, trader_end) if not actual_df.empty else pd.DataFrame()
            actual_history = align_index(actual_df, history_start,
                                         history_end) if not actual_df.empty else pd.DataFrame()

            if not intrahour_long.empty:
                intrahour_trader_long = intrahour_long[
                    (intrahour_long["target_ts"] >= trader_start) &
                    (intrahour_long["target_ts"] <= trader_end)
                    ].copy()

                intrahour_history_long = intrahour_long[
                    (intrahour_long["target_ts"] >= history_start) &
                    (intrahour_long["target_ts"] <= history_end)
                    ].copy()
            else:
                intrahour_trader_long = pd.DataFrame()
                intrahour_history_long = pd.DataFrame()

            intrahour_trader_curves = {}
            intrahour_history_curves = {}
            for label in selected_leads:
                if not intrahour_trader_long.empty:
                    intrahour_trader_curves[label] = align_index(
                        build_intrahour_lead_curve(intrahour_trader_long, lead_map[label]),
                        trader_start,
                        trader_end,
                    )
                else:
                    intrahour_trader_curves[label] = pd.DataFrame()

                if not intrahour_history_long.empty:
                    intrahour_history_curves[label] = align_index(
                        build_intrahour_lead_curve(intrahour_history_long, lead_map[label]),
                        history_start,
                        history_end,
                    )
                else:
                    intrahour_history_curves[label] = pd.DataFrame()

            if not np4732_wide.empty:
                np4732_chart = align_index(np4732_wide, np4732_start, np4732_end)
                np4732_history = align_index(np4732_wide, history_start, history_end)
            else:
                np4732_chart = pd.DataFrame()
                np4732_history = pd.DataFrame()

            available_main_series = [
                s for s in main_regions
                if s in actual_df.columns
                   or any((not c.empty and s in c.columns) for c in intrahour_trader_curves.values())
                   or s == "ERCOT Total"
            ]
            if not available_main_series:
                st.warning("No selected main chart regions were found in the returned data.")
                st.stop()

            colors = px.colors.qualitative.Plotly
            color_map = {r: colors[i % len(colors)] for i, r in enumerate(main_region_order())}
            np4732_color_map = {
                "ERCOT Total": colors[0],
                "South Houston": colors[1],
                "West": colors[2],
                "North": colors[3],
            }

            # =====================================================
            # STATUS STRIP
            # =====================================================
            st.subheader("Status")

            s1, s2, s3 = st.columns(3)

            with s1:
                if not actual_trader.empty and "ERCOT Total" in actual_trader.columns and actual_trader[
                    "ERCOT Total"].dropna().any():
                    val = actual_trader["ERCOT Total"].dropna().iloc[-1]
                    ts = actual_trader["ERCOT Total"].dropna().index[-1]
                    st.metric("Latest Actual", f"{val:,.0f} MW", help=f"As of {ts:%Y-%m-%d %H:%M}")
                else:
                    st.metric("Latest Actual", "n/a")

            with s2:
                latest_curve = intrahour_trader_curves.get("Latest", pd.DataFrame())
                if not latest_curve.empty and "ERCOT Total" in latest_curve.columns and latest_curve[
                    "ERCOT Total"].dropna().any():
                    val = latest_curve["ERCOT Total"].dropna().iloc[-1]
                    ts = latest_curve["ERCOT Total"].dropna().index[-1]
                    st.metric("Latest Intra-hour", f"{val:,.0f} MW", help=f"Target {ts:%Y-%m-%d %H:%M}")
                else:
                    st.metric("Latest Intra-hour", "n/a")

            with s3:
                selected_metric_for_status = ("STWPF", "ERCOT Total")
                if not np4732_chart.empty and selected_metric_for_status in np4732_chart.columns and np4732_chart[
                    selected_metric_for_status].dropna().any():
                    val = np4732_chart[selected_metric_for_status].dropna().iloc[0]
                    ts = np4732_chart[selected_metric_for_status].dropna().index[0]
                    st.metric("NP4-732 STWPF", f"{val:,.0f} MW", help=f"First target {ts:%Y-%m-%d %H:%M}")
                else:
                    st.metric("NP4-732 STWPF", "n/a")

            if load_np4442:
                st.subheader("COP Model Status (NP4-442)")
                if not cop_status_df.empty:
                    st.dataframe(cop_status_df, use_container_width=True, hide_index=True)
                else:
                    st.info("COP model status not available from NP4-442 for this load.")

            # =====================================================
            # CHART 1 - MAIN TRADER VIEW
            # =====================================================
            st.subheader("Trader View: Recent + Near-Term")

            trader_fig = build_base_figure(height=720, yaxis_title="MW")
            trader_has_data = False

            for r in available_main_series:
                if show_actual and not actual_trader.empty and r in actual_trader.columns:
                    x = actual_trader[r].dropna()
                    if not x.empty:
                        trader_has_data = True
                        trader_fig.add_trace(
                            go.Scatter(
                                x=x.index,
                                y=x.values,
                                name=f"{r} Actual",
                                line=dict(color=color_map[r], width=2),
                                hovertemplate=f"{r} Actual<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                            )
                        )

                if show_latest:
                    latest_curve = intrahour_trader_curves.get("Latest", pd.DataFrame())
                    if not latest_curve.empty and r in latest_curve.columns:
                        x = latest_curve[r].dropna()
                        if not x.empty:
                            trader_has_data = True
                            trader_fig.add_trace(
                                go.Scatter(
                                    x=x.index,
                                    y=x.values,
                                    name=f"{r} Latest",
                                    line=dict(color=color_map[r], dash="dash"),
                                    hovertemplate=f"{r} Latest<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                                )
                            )

                if show_1h:
                    curve_1h = intrahour_trader_curves.get("1 hour ago", pd.DataFrame())
                    if not curve_1h.empty and r in curve_1h.columns:
                        x = curve_1h[r].dropna()
                        if not x.empty:
                            trader_has_data = True
                            trader_fig.add_trace(
                                go.Scatter(
                                    x=x.index,
                                    y=x.values,
                                    name=f"{r} 1h Ago",
                                    line=dict(color=color_map[r], dash="dot"),
                                    hovertemplate=f"{r} 1h Ago<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                                )
                            )

                if show_2h:
                    curve_2h = intrahour_trader_curves.get("2 hour ago", pd.DataFrame())
                    if not curve_2h.empty and r in curve_2h.columns:
                        x = curve_2h[r].dropna()
                        if not x.empty:
                            trader_has_data = True
                            trader_fig.add_trace(
                                go.Scatter(
                                    x=x.index,
                                    y=x.values,
                                    name=f"{r} 2h Ago",
                                    line=dict(color=color_map[r], dash="dashdot"),
                                    hovertemplate=f"{r} 2h Ago<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                                )
                            )

            if trader_has_data:
                st.plotly_chart(trader_fig, use_container_width=True, key="wind_trader_chart_v6")
            else:
                st.info("No trader-view data available for the selected window.")

            # =====================================================
            # CHART 2 - NP4-732 SEPARATE CHART
            # =====================================================
            st.subheader("NP4-732 Hourly Outlook")

            np4732_fig = build_base_figure(height=500, yaxis_title="MW")
            np4732_has_data = False

            metric_name_map = {
                "STWPF": "STWPF",
                "WGRPP": "WGRPP",
                "COP HSL": "COP HSL",
                "Actual Gen": "Actual Gen",
                "System HSL": "System HSL",
            }

            selected_metric = metric_name_map[np4732_metric]

            if not np4732_chart.empty:
                for region in np4732_regions:
                    col_key = (selected_metric, region)
                    if col_key in np4732_chart.columns:
                        series = np4732_chart[col_key].dropna()
                        if not series.empty:
                            np4732_has_data = True
                            np4732_fig.add_trace(
                                go.Scatter(
                                    x=series.index,
                                    y=series.values,
                                    mode="lines",
                                    name=f"{region} {selected_metric}",
                                    line=dict(color=np4732_color_map.get(region)),
                                    hovertemplate=f"{region} {selected_metric}<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                                )
                            )

            if np4732_has_data:
                st.plotly_chart(np4732_fig, use_container_width=True, key="wind_np4732_chart_v6")
            else:
                st.info("No NP4-732 data available for the selected metric / regions / window.")

            # =====================================================
            # CHART 3 - HISTORICAL ERROR
            # =====================================================
            st.subheader("Historical Forecast Error")

            metric_map = {
                "MW Error": "error_mw",
                "Absolute Error": "abs_error_mw",
                "Percent Error": "pct_error",
            }
            y_col = metric_map.get(error_metric, "error_mw")
            y_title = {
                "error_mw": "Forecast - Actual (MW)",
                "abs_error_mw": "Absolute Error (MW)",
                "pct_error": "Forecast Error (%)",
            }[y_col]

            error_fig = build_base_figure(height=420, yaxis_title=y_title)
            error_has_data = False

            for r in available_main_series:
                if not actual_history.empty and r in actual_history.columns:
                    if show_latest:
                        latest_hist = intrahour_history_curves.get("Latest", pd.DataFrame())
                        if not latest_hist.empty and r in latest_hist.columns:
                            err = make_error_frame(actual_history[r], latest_hist[r])
                            if not err.empty and y_col in err.columns:
                                error_has_data = True
                                error_fig.add_trace(
                                    go.Scatter(
                                        x=err.index,
                                        y=err[y_col],
                                        name=f"{r} Latest Error",
                                        line=dict(color=color_map[r], dash="dash"),
                                        hovertemplate=f"{r} Latest Error<br>%{{x}}<br>%{{y}}<extra></extra>",
                                    )
                                )

                    if include_np4732_in_error:
                        stwpf_key = ("STWPF", r)
                        if not np4732_history.empty and stwpf_key in np4732_history.columns:
                            err = make_error_frame(actual_history[r], np4732_history[stwpf_key])
                            if not err.empty and y_col in err.columns:
                                error_has_data = True
                                error_fig.add_trace(
                                    go.Scatter(
                                        x=err.index,
                                        y=err[y_col],
                                        name=f"{r} NP4-732 STWPF Error",
                                        line=dict(color=color_map[r], dash="longdash"),
                                        hovertemplate=f"{r} NP4-732 STWPF Error<br>%{{x}}<br>%{{y}}<extra></extra>",
                                    )
                                )

            if error_has_data:
                if y_col != "abs_error_mw":
                    error_fig.add_hline(y=0, line_dash="solid", line_width=1)
                st.plotly_chart(error_fig, use_container_width=True, key="wind_error_chart_v6")
            else:
                st.info("No overlapping actual / forecast history exists for the selected error window.")

            # =====================================================
            # CHART 4 - HISTORICAL REVISIONS
            # =====================================================
            st.subheader("Historical Forecast Revisions")

            revision_fig = build_base_figure(height=420, yaxis_title="Latest - Older Forecast (MW)")
            revision_has_data = False

            latest_hist = intrahour_history_curves.get("Latest", pd.DataFrame())
            curve_1h = intrahour_history_curves.get("1 hour ago", pd.DataFrame())
            curve_2h = intrahour_history_curves.get("2 hour ago", pd.DataFrame())

            for r in available_main_series:
                if not latest_hist.empty and r in latest_hist.columns:
                    if show_1h and not curve_1h.empty and r in curve_1h.columns:
                        combined = pd.concat(
                            [
                                latest_hist[[r]].rename(columns={r: "latest"}),
                                curve_1h[[r]].rename(columns={r: "older"})
                            ],
                            axis=1
                        ).dropna()

                        if not combined.empty:
                            revision_has_data = True
                            combined["revision"] = combined["latest"] - combined["older"]
                            revision_fig.add_trace(
                                go.Scatter(
                                    x=combined.index,
                                    y=combined["revision"],
                                    name=f"{r} Revision vs 1h Ago",
                                    line=dict(color=color_map[r], dash="dot"),
                                    hovertemplate=f"{r} Revision vs 1h Ago<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                                )
                            )

                    if show_2h and not curve_2h.empty and r in curve_2h.columns:
                        combined = pd.concat(
                            [
                                latest_hist[[r]].rename(columns={r: "latest"}),
                                curve_2h[[r]].rename(columns={r: "older"})
                            ],
                            axis=1
                        ).dropna()

                        if not combined.empty:
                            revision_has_data = True
                            combined["revision"] = combined["latest"] - combined["older"]
                            revision_fig.add_trace(
                                go.Scatter(
                                    x=combined.index,
                                    y=combined["revision"],
                                    name=f"{r} Revision vs 2h Ago",
                                    line=dict(color=color_map[r], dash="dashdot"),
                                    hovertemplate=f"{r} Revision vs 2h Ago<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                                )
                            )

            if revision_has_data:
                revision_fig.add_hline(y=0, line_dash="solid", line_width=1)
                st.plotly_chart(revision_fig, use_container_width=True, key="wind_revision_chart_v6")
            else:
                st.info("No revision overlap exists for the selected history window.")

            # =====================================================
            # DATA TABLES
            # =====================================================
            if show_table:
                st.subheader("Data Tables")

                trader_table = pd.DataFrame(index=actual_trader.index if not actual_trader.empty else pd.Index([]))
                for r in available_main_series:
                    if show_actual and not actual_trader.empty and r in actual_trader.columns:
                        trader_table[f"{r} Actual"] = actual_trader[r]

                    if show_latest:
                        latest_curve = intrahour_trader_curves.get("Latest", pd.DataFrame())
                        if not latest_curve.empty and r in latest_curve.columns:
                            trader_table[f"{r} Latest"] = latest_curve[r]

                    if show_1h:
                        curve_1h = intrahour_trader_curves.get("1 hour ago", pd.DataFrame())
                        if not curve_1h.empty and r in curve_1h.columns:
                            trader_table[f"{r} 1h Ago"] = curve_1h[r]

                    if show_2h:
                        curve_2h = intrahour_trader_curves.get("2 hour ago", pd.DataFrame())
                        if not curve_2h.empty and r in curve_2h.columns:
                            trader_table[f"{r} 2h Ago"] = curve_2h[r]

                np4732_table = pd.DataFrame(index=np4732_chart.index if not np4732_chart.empty else pd.Index([]))
                for region in np4732_regions:
                    col_key = (selected_metric, region)
                    if not np4732_chart.empty and col_key in np4732_chart.columns:
                        np4732_table[f"{region} {selected_metric}"] = np4732_chart[col_key]

                st.markdown("**Main Trader Data**")
                st.dataframe(trader_table.reset_index(), use_container_width=True, hide_index=True)

                st.markdown("**NP4-732 Data**")
                st.dataframe(np4732_table.reset_index(), use_container_width=True, hide_index=True)

                trader_csv = trader_table.reset_index().to_csv(index=False).encode("utf-8")
                np4732_csv = np4732_table.reset_index().to_csv(index=False).encode("utf-8")

                d1, d2 = st.columns(2)
                with d1:
                    st.download_button(
                        "Download trader data CSV",
                        data=trader_csv,
                        file_name="ercot_wind_trader_main.csv",
                        mime="text/csv",
                        key="wind_trader_main_download_v6"
                    )
                with d2:
                    st.download_button(
                        "Download NP4-732 data CSV",
                        data=np4732_csv,
                        file_name="ercot_wind_np4732.csv",
                        mime="text/csv",
                        key="wind_trader_np4732_download_v6"
                    )

    # =====================================================
    # DEBUG
    # =====================================================
            with st.expander("Debug info"):
                st.write("Feed status:")
                st.write({
                    "actual_ok": actual_res["ok"],
                    "intrahour_ok": intrahour_res["ok"],
                    "np4732_ok": np4732_res["ok"],
                    "np4442_ok": np4442_res["ok"],
                })

                if actual_res["endpoint"]:
                    st.write(f"Actual endpoint: {actual_res['endpoint']}")
                if intrahour_res["endpoint"]:
                    st.write(f"Intra-hour endpoint: {intrahour_res['endpoint']}")
                if np4732_res["endpoint"]:
                    st.write(f"NP4-732 endpoint: {np4732_res['endpoint']}")
                if np4442_res["endpoint"]:
                    st.write(f"NP4-442 endpoint: {np4442_res['endpoint']}")

                if actual_error:
                    st.write(f"Actual parse/load error: {actual_error}")
                if intrahour_error:
                    st.write(f"Intra-hour parse/load error: {intrahour_error}")
                if np4732_error:
                    st.write(f"NP4-732 parse/load error: {np4732_error}")
                if np4442_error:
                    st.write(f"NP4-442 parse/load error: {np4442_error}")

                if actual_res["ok"]:
                    st.write("Actual columns:")
                    st.write(list(actual_res["df"].columns))
                if intrahour_res["ok"]:
                    st.write("Intra-hour columns:")
                    st.write(list(intrahour_res["df"].columns))
                if np4732_res["ok"]:
                    st.write("NP4-732 columns:")
                    st.write(list(np4732_res["df"].columns))
                if np4442_res["ok"]:
                    st.write("NP4-442 columns:")
                    st.write(list(np4442_res["df"].columns))

        except Exception as e:
            st.error(str(e))
# -----------------------------
# TAB 3 - SOLAR DATA
# -----------------------------
elif page == "Solar Trader View":
    st.caption("ERCOT Solar Trader View")

    ACTUAL_5MIN_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-746-cd"
    HOURLY_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-745-cd"
    MODEL_STATUS_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-443-cd"

    # =====================================================
    # API HELPERS
    # =====================================================
    @st.cache_data(ttl=300)
    def get_product_metadata(product_url: str) -> dict:
        r = requests.get(product_url, headers=get_headers(), timeout=60)
        r.raise_for_status()
        return r.json()

    def choose_best_artifact(artifacts):
        if not artifacts:
            raise ValueError("No artifacts returned for product.")

        scored = []
        for a in artifacts:
            endpoint = a.get("_links", {}).get("endpoint", {}).get("href", "")
            name = f"{a.get('friendlyName', '')} {a.get('name', '')}".lower()
            blob = f"{endpoint} {name}".lower()

            score = 0
            if endpoint:
                score += 1
            if "csv" in blob:
                score += 10
            if "xml" in blob:
                score -= 1
            if "zip" in blob:
                score -= 2
            if "view" in blob or "data" in blob or "report" in blob:
                score += 2

            scored.append((score, a))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    @st.cache_data(ttl=300)
    def get_artifact_endpoint(product_url: str):
        meta = get_product_metadata(product_url)
        artifacts = meta.get("artifacts", [])
        if not artifacts:
            raise ValueError(f"No artifacts found for product: {product_url}")

        best = choose_best_artifact(artifacts)
        endpoint = best.get("_links", {}).get("endpoint", {}).get("href")
        if not endpoint:
            raise ValueError(f"Could not find artifact endpoint for product: {product_url}")

        return endpoint

    @st.cache_data(ttl=300)
    def load_report(endpoint: str, posted_from=None, posted_to=None, size=10000, timeout=60):
        params = {"size": size}
        if posted_from:
            params["postedDatetimeFrom"] = posted_from
        if posted_to:
            params["postedDatetimeTo"] = posted_to

        session = requests.Session()
        retries = Retry(
            total=2,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        session.mount("https://", HTTPAdapter(max_retries=retries))

        r = session.get(endpoint, headers=get_headers(), params=params, timeout=timeout)
        r.raise_for_status()

        payload = r.json()
        fields = payload.get("fields", [])
        rows = payload.get("data", [])

        if not fields:
            raise ValueError(f"No fields returned from endpoint: {endpoint}")

        cols = [f["name"] for f in fields]
        return pd.DataFrame(rows, columns=cols)

    def safe_load_product(product_url: str, posted_from=None, posted_to=None, size=10000, timeout=60):
        try:
            endpoint = get_artifact_endpoint(product_url)
            df = load_report(endpoint, posted_from=posted_from, posted_to=posted_to, size=size, timeout=timeout)
            return {
                "ok": True,
                "endpoint": endpoint,
                "df": df,
                "error": None,
            }
        except Exception as e:
            return {
                "ok": False,
                "endpoint": None,
                "df": pd.DataFrame(),
                "error": str(e),
            }

    # =====================================================
    # GENERIC HELPERS
    # =====================================================
    def normalize_key(s: str) -> str:
        return str(s).strip().lower().replace("_", "").replace("-", "").replace(" ", "")

    def pick_col(df: pd.DataFrame, candidates):
        lower_map = {normalize_key(c): c for c in df.columns}
        for cand in candidates:
            key = normalize_key(cand)
            if key in lower_map:
                return lower_map[key]
        return None

    def align_index(df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp):
        if df.empty:
            return df
        return df[(df.index >= start_ts) & (df.index <= end_ts)].copy()

    def build_base_figure(height=500, yaxis_title="MW"):
        fig = go.Figure()
        fig.update_layout(
            height=height,
            hovermode="x unified",
            yaxis_title=yaxis_title,
            xaxis_title="Time",
            legend_title_text="Series",
            margin=dict(l=20, r=20, t=20, b=20),
        )
        return fig

    def make_error_frame(actual_series: pd.Series, forecast_series: pd.Series):
        combined = pd.concat(
            [
                actual_series.rename("actual"),
                forecast_series.rename("forecast"),
            ],
            axis=1
        ).dropna()

        if combined.empty:
            return combined

        combined["error_mw"] = combined["forecast"] - combined["actual"]
        combined["abs_error_mw"] = combined["error_mw"].abs()
        combined["pct_error"] = np.where(
            combined["actual"].abs() > 1e-9,
            (combined["error_mw"] / combined["actual"]) * 100.0,
            np.nan
        )
        combined["ape"] = combined["pct_error"].abs()
        return combined

    def summarize_error_metrics(error_df: pd.DataFrame, label: str, series_name: str):
        if error_df.empty:
            return None

        return {
            "Series": series_name,
            "Forecast": label,
            "Count": int(len(error_df)),
            "Bias MW": float(error_df["error_mw"].mean()),
            "MAE MW": float(error_df["abs_error_mw"].mean()),
            "RMSE MW": float(np.sqrt((error_df["error_mw"] ** 2).mean())),
            "MAPE %": float(error_df["ape"].mean()) if error_df["ape"].notna().any() else np.nan,
        }

    # =====================================================
    # WINDOW HELPER
    # =====================================================
    def parse_local_window(label, now):
        if label == "Last 24 / Next 2":
            return now - pd.Timedelta(hours=24), now + pd.Timedelta(hours=2)
        if label == "Last 12 / Next 2":
            return now - pd.Timedelta(hours=12), now + pd.Timedelta(hours=2)
        if label == "Last 24 / Next 6":
            return now - pd.Timedelta(hours=24), now + pd.Timedelta(hours=6)
        if label == "Last 24 / Next 24":
            return now - pd.Timedelta(hours=24), now + pd.Timedelta(hours=24)
        if label == "Last 24":
            return now - pd.Timedelta(hours=24), now
        if label == "Last 48":
            return now - pd.Timedelta(hours=48), now
        if label == "Last 72":
            return now - pd.Timedelta(hours=72), now
        if label == "Last 168":
            return now - pd.Timedelta(hours=168), now
        if label == "Next 24":
            return now, now + pd.Timedelta(hours=24)
        if label == "Next 48":
            return now, now + pd.Timedelta(hours=48)
        if label == "Next 72":
            return now, now + pd.Timedelta(hours=72)
        if label == "Next 168":
            return now, now + pd.Timedelta(hours=168)
        return now - pd.Timedelta(hours=24), now + pd.Timedelta(hours=2)

    # =====================================================
    # SOLAR REGION HELPERS
    # =====================================================
    def solar_region_order():
        return [
            "ERCOT Total",
            "Center West",
            "North West",
            "Far West",
            "Far East",
            "South East",
            "Center East",
        ]

    def add_solar_total_if_missing(df: pd.DataFrame):
        parts = [c for c in ["Center West", "North West", "Far West", "Far East", "South East", "Center East"] if c in df.columns]
        if "ERCOT Total" not in df.columns and parts:
            df["ERCOT Total"] = df[parts].sum(axis=1, min_count=1)
        ordered = [c for c in solar_region_order() if c in df.columns]
        return df[ordered] if ordered else df

    # =====================================================
    # NP4-746 - 5 MIN ACTUALS
    # =====================================================
    def normalize_np4746_actual(df: pd.DataFrame):
        df = df.copy()

        time_col = pick_col(df, ["INTERVAL_ENDING", "IntervalEnding", "intervalEnding"])
        if not time_col:
            raise ValueError(f"NP4-746: could not detect interval ending column. Columns: {list(df.columns)}")

        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        df = df.dropna(subset=[time_col]).copy()

        region_map = {
            "SYSTEM_WIDE_GEN": "ERCOT Total",
            "systemWideGen": "ERCOT Total",
            "CenterWest_GEN": "Center West",
            "centerWestGen": "Center West",
            "NorthWest_GEN": "North West",
            "northWestGen": "North West",
            "FarWest_GEN": "Far West",
            "farWestGen": "Far West",
            "FarEast_GEN": "Far East",
            "farEastGen": "Far East",
            "SouthEast_GEN": "South East",
            "southEastGen": "South East",
            "CenterEast_GEN": "Center East",
            "centerEastGen": "Center East",
            "SYSTEM_WIDE_HSL": "ERCOT Total HSL",
            "systemWideHSL": "ERCOT Total HSL",
        }

        keep = [time_col]
        rename_map = {}
        for raw_col, display in region_map.items():
            if raw_col in df.columns:
                keep.append(raw_col)
                rename_map[raw_col] = display

        out = df[keep].copy().rename(columns=rename_map)
        for c in out.columns:
            if c != time_col:
                out[c] = pd.to_numeric(out[c], errors="coerce")

        out = (
            out.sort_values(time_col)
            .drop_duplicates(subset=[time_col], keep="last")
            .set_index(time_col)
            .resample("5min")
            .mean()
        )

        out = add_solar_total_if_missing(out)
        return out, time_col

    # =====================================================
    # NP4-745 - HOURLY ACTUAL / STPPF / PVGRPP / HSL
    # =====================================================
    def normalize_np4745_hourly(df: pd.DataFrame):
        df = df.copy()

        def norm(s):
            return str(s).strip().lower().replace("_", "").replace("-", "").replace(" ", "")

        col_map = {norm(c): c for c in df.columns}

        def pick(*candidates):
            for cand in candidates:
                key = norm(cand)
                if key in col_map:
                    return col_map[key]
            return None

        delivery_col = pick("DELIVERY_DATE", "deliveryDate")
        he_col = pick("HOUR_ENDING", "hourEnding")
        posted_col = pick("postedDatetime")

        missing = []
        if not delivery_col:
            missing.append("DELIVERY_DATE/deliveryDate")
        if not he_col:
            missing.append("HOUR_ENDING/hourEnding")
        if missing:
            raise ValueError(f"NP4-745 missing required columns: {missing}. Columns: {list(df.columns)}")

        df[delivery_col] = pd.to_datetime(df[delivery_col], errors="coerce")
        df[he_col] = pd.to_numeric(df[he_col], errors="coerce")
        if posted_col:
            df[posted_col] = pd.to_datetime(df[posted_col], errors="coerce")

        df = df.dropna(subset=[delivery_col, he_col]).copy()
        df["target_ts"] = df[delivery_col] + pd.to_timedelta(df[he_col], unit="h")

        metric_region_map = {
            "SYSTEM_WIDE_GEN": ("Actual Gen", "ERCOT Total"),
            "genSystemWide": ("Actual Gen", "ERCOT Total"),
            "COP_HSL_SYSTEM_WIDE": ("COP HSL", "ERCOT Total"),
            "COPHSLSystemWide": ("COP HSL", "ERCOT Total"),
            "STPPF_SYSTEM_WIDE": ("STPPF", "ERCOT Total"),
            "STPPFSystemWide": ("STPPF", "ERCOT Total"),
            "PVGRPP_SYSTEM_WIDE": ("PVGRPP", "ERCOT Total"),
            "PVGRPPSystemWide": ("PVGRPP", "ERCOT Total"),
            "SYSTEM_WIDE_HSL": ("System HSL", "ERCOT Total"),
            "HSLSystemWide": ("System HSL", "ERCOT Total"),

            "GEN_CenterWest": ("Actual Gen", "Center West"),
            "genCenterWest": ("Actual Gen", "Center West"),
            "COP_HSL_CenterWest": ("COP HSL", "Center West"),
            "COPHSLCenterWest": ("COP HSL", "Center West"),
            "STPPF_CenterWest": ("STPPF", "Center West"),
            "STPPFCenterWest": ("STPPF", "Center West"),
            "PVGRPP_CenterWest": ("PVGRPP", "Center West"),
            "PVGRPPCenterWest": ("PVGRPP", "Center West"),

            "GEN_NorthWest": ("Actual Gen", "North West"),
            "genNorthWest": ("Actual Gen", "North West"),
            "COP_HSL_NorthWest": ("COP HSL", "North West"),
            "COPHSLNorthWest": ("COP HSL", "North West"),
            "STPPF_NorthWest": ("STPPF", "North West"),
            "STPPFNorthWest": ("STPPF", "North West"),
            "PVGRPP_NorthWest": ("PVGRPP", "North West"),
            "PVGRPPNorthWest": ("PVGRPP", "North West"),

            "GEN_FarWest": ("Actual Gen", "Far West"),
            "genFarWest": ("Actual Gen", "Far West"),
            "COP_HSL_FarWest": ("COP HSL", "Far West"),
            "COPHSLFarWest": ("COP HSL", "Far West"),
            "STPPF_FarWest": ("STPPF", "Far West"),
            "STPPFFarWest": ("STPPF", "Far West"),
            "PVGRPP_FarWest": ("PVGRPP", "Far West"),
            "PVGRPPFarWest": ("PVGRPP", "Far West"),

            "GEN_FarEast": ("Actual Gen", "Far East"),
            "genFarEast": ("Actual Gen", "Far East"),
            "COP_HSL_FarEast": ("COP HSL", "Far East"),
            "COPHSLFarEast": ("COP HSL", "Far East"),
            "STPPF_FarEast": ("STPPF", "Far East"),
            "STPPFFarEast": ("STPPF", "Far East"),
            "PVGRPP_FarEast": ("PVGRPP", "Far East"),
            "PVGRPPFarEast": ("PVGRPP", "Far East"),

            "GEN_SouthEast": ("Actual Gen", "South East"),
            "genSouthEast": ("Actual Gen", "South East"),
            "COP_HSL_SouthEast": ("COP HSL", "South East"),
            "COPHSLSouthEast": ("COP HSL", "South East"),
            "STPPF_SouthEast": ("STPPF", "South East"),
            "STPPFSouthEast": ("STPPF", "South East"),
            "PVGRPP_SouthEast": ("PVGRPP", "South East"),
            "PVGRPPSouthEast": ("PVGRPP", "South East"),

            "GEN_CenterEast": ("Actual Gen", "Center East"),
            "genCenterEast": ("Actual Gen", "Center East"),
            "COP_HSL_CenterEast": ("COP HSL", "Center East"),
            "COPHSLCenterEast": ("COP HSL", "Center East"),
            "STPPF_CenterEast": ("STPPF", "Center East"),
            "STPPFCenterEast": ("STPPF", "Center East"),
            "PVGRPP_CenterEast": ("PVGRPP", "Center East"),
            "PVGRPPCenterEast": ("PVGRPP", "Center East"),
        }

        long_frames = []

        for raw_col, (metric_name, region_name) in metric_region_map.items():
            if raw_col not in df.columns:
                continue

            temp = df[["target_ts", raw_col]].copy()
            if posted_col:
                temp["posted_ts"] = df[posted_col]

            temp["metric"] = metric_name
            temp["region"] = region_name
            temp["mw"] = pd.to_numeric(temp[raw_col], errors="coerce")
            temp = temp.dropna(subset=["mw"]).copy()

            keep_cols = ["target_ts", "region", "metric", "mw"]
            if posted_col:
                keep_cols.insert(1, "posted_ts")

            long_frames.append(temp[keep_cols])

        if not long_frames:
            raise ValueError(f"NP4-745 parser found no usable metric columns. Columns: {list(df.columns)}")

        long_df = pd.concat(long_frames, ignore_index=True)

        if "posted_ts" in long_df.columns:
            long_df = (
                long_df.sort_values(["target_ts", "metric", "region", "posted_ts"])
                .drop_duplicates(subset=["target_ts", "metric", "region"], keep="last")
                .copy()
            )

        wide = (
            long_df.pivot_table(
                index="target_ts",
                columns=["metric", "region"],
                values="mw",
                aggfunc="mean"
            )
            .sort_index()
        )

        return long_df, wide

    # =====================================================
    # NP4-443 - ACTIVE MODEL STATUS
    # =====================================================
    def parse_np4443_active_model_status(df: pd.DataFrame):
        if df.empty:
            return pd.DataFrame()

        work = df.copy()

        delivery_col = pick_col(work, ["DeliveryDate", "deliveryDate"])
        he_col = pick_col(work, ["HourEnding", "hourEnding"])
        region_col = pick_col(work, ["Region", "region"])
        model_col = pick_col(work, ["Model", "model"])
        inuse_col = pick_col(work, ["InUseFlag", "inUseFlag"])

        missing = []
        for col_name, actual_col in {
            "DeliveryDate": delivery_col,
            "HourEnding": he_col,
            "Region": region_col,
            "Model": model_col,
            "InUseFlag": inuse_col,
        }.items():
            if actual_col is None:
                missing.append(col_name)

        if missing:
            return pd.DataFrame()

        work[delivery_col] = pd.to_datetime(work[delivery_col], errors="coerce")
        work[he_col] = pd.to_numeric(work[he_col], errors="coerce")
        work = work.dropna(subset=[delivery_col, he_col]).copy()
        work["target_ts"] = work[delivery_col] + pd.to_timedelta(work[he_col], unit="h")

        active = work[work[inuse_col].astype(str).str.upper() == "Y"].copy()
        if active.empty:
            return pd.DataFrame()

        def normalize_region(x):
            s = normalize_key(x)
            if s == "systemtotal":
                return "ERCOT Total"
            mapping = {
                "centereast": "Center East",
                "centerwest": "Center West",
                "northwest": "North West",
                "farwest": "Far West",
                "fareast": "Far East",
                "southeast": "South East",
            }
            return mapping.get(s)

        active["RegionStd"] = active[region_col].map(normalize_region)
        active = active.dropna(subset=["RegionStd"]).copy()

        latest_target = active["target_ts"].max()
        latest = active[active["target_ts"] == latest_target].copy()

        status = (
            latest[["RegionStd", model_col, "target_ts"]]
            .rename(columns={"RegionStd": "Region", model_col: "Active Model", "target_ts": "Target Time"})
            .sort_values("Region")
        )

        return status

    # =====================================================
    # PAGE
    # =====================================================
    try:
        now = pd.Timestamp.now(tz="America/Chicago").tz_localize(None).floor("5min")

        st.subheader("Page Options")
        page_opt1, page_opt2 = st.columns(2)
        with page_opt1:
            load_np4443 = st.checkbox(
                "Load NP4-443 active model status",
                value=False,
                key="solar_load_np4443_v3"
            )
        with page_opt2:
            show_tables = st.checkbox(
                "Show data tables",
                value=False,
                key="solar_show_tables_v3"
            )

        actual_from = (now - pd.Timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
        actual_to = now.strftime("%Y-%m-%dT%H:%M")

        actual_res = safe_load_product(
            ACTUAL_5MIN_PRODUCT_URL,
            posted_from=actual_from,
            posted_to=actual_to,
            size=40000,
            timeout=60,
        )

        hourly_res = safe_load_product(
            HOURLY_PRODUCT_URL,
            posted_from=None,
            posted_to=None,
            size=1000,
            timeout=45,
        )

        if load_np4443:
            model_status_res = safe_load_product(
                MODEL_STATUS_PRODUCT_URL,
                posted_from=None,
                posted_to=None,
                size=20000,
                timeout=45,
            )
        else:
            model_status_res = {
                "ok": False,
                "endpoint": None,
                "df": pd.DataFrame(),
                "error": None,
            }

        actual_df = pd.DataFrame()
        actual_error = None
        actual_time_col = None
        if actual_res["ok"]:
            try:
                actual_df, actual_time_col = normalize_np4746_actual(actual_res["df"])
            except Exception as e:
                actual_error = str(e)
        else:
            actual_error = actual_res["error"]

        hourly_long = pd.DataFrame()
        hourly_wide = pd.DataFrame()
        hourly_error = None
        if hourly_res["ok"]:
            try:
                hourly_long, hourly_wide = normalize_np4745_hourly(hourly_res["df"])
            except Exception as e:
                hourly_error = str(e)
        else:
            hourly_error = hourly_res["error"]

        model_status_df = pd.DataFrame()
        model_status_error = None
        if model_status_res["ok"]:
            try:
                model_status_df = parse_np4443_active_model_status(model_status_res["df"])
            except Exception as e:
                model_status_error = str(e)
        else:
            model_status_error = None

        palette = px.colors.qualitative.Plotly
        color_map = {series: palette[i % len(palette)] for i, series in enumerate(solar_region_order())}

        # =====================================================
        # STATUS STRIP
        # =====================================================
        st.subheader("Status")

        s1, s2, s3 = st.columns(3)

        with s1:
            if not actual_df.empty and "ERCOT Total" in actual_df.columns and actual_df["ERCOT Total"].dropna().any():
                val = actual_df["ERCOT Total"].dropna().iloc[-1]
                ts = actual_df["ERCOT Total"].dropna().index[-1]
                st.metric("Latest 5-Min Actual", f"{val:,.0f} MW", help=f"As of {ts:%Y-%m-%d %H:%M}")
            else:
                st.metric("Latest 5-Min Actual", "n/a")

        with s2:
            key = ("STPPF", "ERCOT Total")
            if not hourly_wide.empty and key in hourly_wide.columns and hourly_wide[key].dropna().any():
                val = hourly_wide[key].dropna().iloc[0]
                ts = hourly_wide[key].dropna().index[0]
                st.metric("Hourly STPPF", f"{val:,.0f} MW", help=f"First target {ts:%Y-%m-%d %H:%M}")
            else:
                st.metric("Hourly STPPF", "n/a")

        with s3:
            key = ("PVGRPP", "ERCOT Total")
            if not hourly_wide.empty and key in hourly_wide.columns and hourly_wide[key].dropna().any():
                val = hourly_wide[key].dropna().iloc[0]
                ts = hourly_wide[key].dropna().index[0]
                st.metric("Hourly PVGRPP", f"{val:,.0f} MW", help=f"First target {ts:%Y-%m-%d %H:%M}")
            else:
                st.metric("Hourly PVGRPP", "n/a")

        if load_np4443:
            st.subheader("Active Hourly Model Status (NP4-443)")
            if not model_status_df.empty:
                st.dataframe(model_status_df, use_container_width=True, hide_index=True)
            else:
                st.info("Active hourly model status not available for this load.")

        # =====================================================
        # CHART 1 - TRADER VIEW
        # =====================================================
        st.subheader("Trader View: Recent + Near-Term")

        tv_c1, tv_c2, tv_c3 = st.columns(3)
        with tv_c1:
            trader_window = st.selectbox(
                "Trader window",
                ["Last 24 / Next 2", "Last 12 / Next 2", "Last 24 / Next 6", "Last 24 / Next 24"],
                index=0,
                key="solar_trader_window_v3"
            )
        with tv_c2:
            trader_regions = st.multiselect(
                "Trader regions",
                options=solar_region_order(),
                default=["ERCOT Total"],
                key="solar_trader_regions_v3"
            )
        with tv_c3:
            trader_show_actual = st.checkbox(
                "Show 5-minute actuals",
                value=True,
                key="solar_trader_show_actual_v3"
            )
            trader_show_stppf = st.checkbox(
                "Show STPPF",
                value=True,
                key="solar_trader_show_stppf_v3"
            )

        trader_start, trader_end = parse_local_window(trader_window, now)

        actual_trader = align_index(actual_df, trader_start, trader_end) if not actual_df.empty else pd.DataFrame()
        hourly_trader = align_index(hourly_wide, trader_start, trader_end) if not hourly_wide.empty else pd.DataFrame()

        available_trader_series = [
            s for s in trader_regions
            if s in actual_df.columns
            or (not hourly_wide.empty and ("STPPF", s) in hourly_wide.columns)
        ]

        trader_fig = build_base_figure(height=720, yaxis_title="MW")
        trader_has_data = False

        # STPPF first
        for r in available_trader_series:
            if trader_show_stppf and not hourly_trader.empty:
                stppf_key = ("STPPF", r)
                if stppf_key in hourly_trader.columns:
                    x = hourly_trader[stppf_key].dropna()
                    if not x.empty:
                        trader_has_data = True
                        trader_fig.add_trace(
                            go.Scatter(
                                x=x.index,
                                y=x.values,
                                name=f"{r} STPPF",
                                line=dict(color=color_map.get(r, None), dash="dash", width=2),
                                line_shape="hv",
                                hovertemplate=f"{r} STPPF<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                            )
                        )

        # actuals last
        for r in available_trader_series:
            if trader_show_actual and not actual_trader.empty and r in actual_trader.columns:
                x = actual_trader[r].dropna()
                if not x.empty:
                    trader_has_data = True
                    trader_fig.add_trace(
                        go.Scatter(
                            x=x.index,
                            y=x.values,
                            name=f"{r} Actual",
                            line=dict(color=color_map.get(r, None), width=4),
                            hovertemplate=f"{r} Actual<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                        )
                    )

        if trader_has_data:
            trader_fig.add_vline(
                x=now,
                line_width=2,
                line_dash="dash",
            )
            st.plotly_chart(trader_fig, use_container_width=True, key="solar_trader_chart_v3")
        else:
            st.info("No trader-view data available for the selected window.")

        # =====================================================
        # CHART 2 - HOURLY OUTLOOK
        # =====================================================
        st.subheader("Hourly Solar Outlook (NP4-745)")

        ho_c1, ho_c2, ho_c3, ho_c4 = st.columns(4)
        with ho_c1:
            hourly_window = st.selectbox(
                "Hourly window",
                ["Next 24", "Next 48", "Next 72", "Next 168", "Trader Span", "Last 24 / Next 24"],
                index=2,
                key="solar_hourly_window_v3"
            )
        with ho_c2:
            hourly_metric = st.selectbox(
                "Hourly forecast metric",
                ["STPPF", "PVGRPP", "COP HSL", "System HSL"],
                index=0,
                key="solar_hourly_metric_v3"
            )
        with ho_c3:
            hourly_regions = st.multiselect(
                "Hourly regions",
                options=solar_region_order(),
                default=["ERCOT Total", "Far West", "Far East"],
                key="solar_hourly_regions_v3"
            )
        with ho_c4:
            hourly_show_actual = st.checkbox(
                "Overlay hourly actuals",
                value=True,
                key="solar_hourly_show_actual_v3"
            )

        if hourly_window == "Trader Span":
            hourly_start, hourly_end = trader_start, trader_end
        elif hourly_window == "Last 24 / Next 24":
            hourly_start, hourly_end = now - pd.Timedelta(hours=24), now + pd.Timedelta(hours=24)
        else:
            hourly_start, hourly_end = parse_local_window(hourly_window, now)

        hourly_chart = align_index(hourly_wide, hourly_start, hourly_end) if not hourly_wide.empty else pd.DataFrame()

        hourly_fig = build_base_figure(height=520, yaxis_title="MW")
        hourly_has_data = False

        if not hourly_chart.empty:
            for region in hourly_regions:
                forecast_key = (hourly_metric, region)
                if forecast_key in hourly_chart.columns:
                    series = hourly_chart[forecast_key].dropna()
                    if not series.empty:
                        hourly_has_data = True
                        hourly_fig.add_trace(
                            go.Scatter(
                                x=series.index,
                                y=series.values,
                                name=f"{region} {hourly_metric}",
                                line=dict(color=color_map.get(region, None), dash="dash", width=2),
                                line_shape="hv",
                                hovertemplate=f"{region} {hourly_metric}<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                            )
                        )

            if hourly_show_actual:
                for region in hourly_regions:
                    actual_key = ("Actual Gen", region)
                    if actual_key in hourly_chart.columns:
                        series = hourly_chart[actual_key].dropna()
                        if not series.empty:
                            hourly_has_data = True
                            hourly_fig.add_trace(
                                go.Scatter(
                                    x=series.index,
                                    y=series.values,
                                    name=f"{region} Actual Gen",
                                    line=dict(color=color_map.get(region, None), width=4),
                                    line_shape="hv",
                                    hovertemplate=f"{region} Actual Gen<br>%{{x}}<br>%{{y:,.0f}} MW<extra></extra>",
                                )
                            )

        if hourly_has_data:
            hourly_fig.add_vline(
                x=now,
                line_width=2,
                line_dash="dash",
            )
            st.plotly_chart(hourly_fig, use_container_width=True, key="solar_hourly_chart_v3")
        else:
            st.info("No NP4-745 data available for the selected metric / regions / window.")

        # =====================================================
        # CHART 3 - HISTORICAL ERROR
        # =====================================================
        st.subheader("Historical Hourly Forecast Error")

        er_c1, er_c2, er_c3 = st.columns(3)
        with er_c1:
            history_window = st.selectbox(
                "Error history window",
                ["Last 24", "Last 48", "Last 72", "Last 168"],
                index=0,
                key="solar_error_history_window_v3"
            )
        with er_c2:
            error_metric = st.selectbox(
                "Error metric",
                ["MW Error", "Absolute Error", "Percent Error"],
                index=0,
                key="solar_error_metric_v3"
            )
        with er_c3:
            hourly_error_source = st.selectbox(
                "Forecast source",
                ["STPPF", "PVGRPP"],
                index=0,
                key="solar_hourly_error_source_v3"
            )

        error_regions = st.multiselect(
            "Error regions",
            options=solar_region_order(),
            default=["ERCOT Total", "Far West", "Far East"],
            key="solar_error_regions_v3"
        )

        history_start, history_end = parse_local_window(history_window, now)
        hourly_history = align_index(hourly_wide, history_start, history_end) if not hourly_wide.empty else pd.DataFrame()

        metric_map = {
            "MW Error": "error_mw",
            "Absolute Error": "abs_error_mw",
            "Percent Error": "pct_error",
        }
        y_col = metric_map.get(error_metric, "error_mw")
        y_title = {
            "error_mw": "Forecast - Actual (MW)",
            "abs_error_mw": "Absolute Error (MW)",
            "pct_error": "Forecast Error (%)",
        }[y_col]

        error_fig = build_base_figure(height=420, yaxis_title=y_title)
        error_has_data = False

        for r in error_regions:
            actual_key = ("Actual Gen", r)
            forecast_key = (hourly_error_source, r)

            if not hourly_history.empty and actual_key in hourly_history.columns and forecast_key in hourly_history.columns:
                err = make_error_frame(hourly_history[actual_key], hourly_history[forecast_key])
                if not err.empty and y_col in err.columns:
                    error_has_data = True
                    error_fig.add_trace(
                        go.Scatter(
                            x=err.index,
                            y=err[y_col],
                            name=f"{r} {hourly_error_source} Error",
                            line=dict(color=color_map.get(r, None)),
                            hovertemplate=f"{r} {hourly_error_source} Error<br>%{{x}}<br>%{{y}}<extra></extra>",
                        )
                    )

        if error_has_data:
            if y_col != "abs_error_mw":
                error_fig.add_hline(y=0, line_dash="solid", line_width=1)
            st.plotly_chart(error_fig, use_container_width=True, key="solar_error_chart_v3")
        else:
            st.info("No overlapping hourly actual / forecast history exists for the selected error window.")

        # =====================================================
        # SUMMARY TABLE
        # =====================================================
        st.subheader("Hourly Error Summary")

        summary_rows = []
        for r in error_regions:
            actual_key = ("Actual Gen", r)
            for src in ["STPPF", "PVGRPP"]:
                forecast_key = (src, r)
                if not hourly_history.empty and actual_key in hourly_history.columns and forecast_key in hourly_history.columns:
                    err = make_error_frame(hourly_history[actual_key], hourly_history[forecast_key])
                    row = summarize_error_metrics(err, src, r)
                    if row:
                        summary_rows.append(row)

        summary_df = pd.DataFrame(summary_rows)
        if not summary_df.empty:
            st.dataframe(summary_df, use_container_width=True, hide_index=True)
        else:
            st.info("No overlapping hourly history available for summary metrics.")

        # =====================================================
        # TABLES / EXPORT
        # =====================================================
        if show_tables:
            st.subheader("Data Tables")

            trader_table = pd.DataFrame(index=actual_trader.index if not actual_trader.empty else pd.Index([]))
            for r in available_trader_series:
                if trader_show_actual and not actual_trader.empty and r in actual_trader.columns:
                    trader_table[f"{r} Actual"] = actual_trader[r]
                if trader_show_stppf and not hourly_trader.empty and ("STPPF", r) in hourly_trader.columns:
                    trader_table[f"{r} STPPF"] = hourly_trader[("STPPF", r)]

            hourly_table = pd.DataFrame(index=hourly_chart.index if not hourly_chart.empty else pd.Index([]))
            for region in hourly_regions:
                forecast_key = (hourly_metric, region)
                if not hourly_chart.empty and forecast_key in hourly_chart.columns:
                    hourly_table[f"{region} {hourly_metric}"] = hourly_chart[forecast_key]
                actual_key = ("Actual Gen", region)
                if hourly_show_actual and not hourly_chart.empty and actual_key in hourly_chart.columns:
                    hourly_table[f"{region} Actual Gen"] = hourly_chart[actual_key]

            st.markdown("**Main Trader Data**")
            st.dataframe(trader_table.reset_index(), use_container_width=True, hide_index=True)

            st.markdown("**Hourly Outlook Data**")
            st.dataframe(hourly_table.reset_index(), use_container_width=True, hide_index=True)

            trader_csv = trader_table.reset_index().to_csv(index=False).encode("utf-8")
            hourly_csv = hourly_table.reset_index().to_csv(index=False).encode("utf-8")

            d1, d2 = st.columns(2)
            with d1:
                st.download_button(
                    "Download trader data CSV",
                    data=trader_csv,
                    file_name="ercot_solar_trader_main.csv",
                    mime="text/csv",
                    key="solar_trader_main_download_v3"
                )
            with d2:
                st.download_button(
                    "Download hourly data CSV",
                    data=hourly_csv,
                    file_name="ercot_solar_hourly_np4745.csv",
                    mime="text/csv",
                    key="solar_hourly_download_v3"
                )

        # =====================================================
        # DEBUG
        # =====================================================
        with st.expander("Debug info"):
            st.write("Feed status:")
            st.write({
                "np4746_ok": actual_res["ok"],
                "np4745_ok": hourly_res["ok"],
                "np4443_ok": model_status_res["ok"],
            })

            if actual_res["endpoint"]:
                st.write(f"NP4-746 endpoint: {actual_res['endpoint']}")
            if hourly_res["endpoint"]:
                st.write(f"NP4-745 endpoint: {hourly_res['endpoint']}")
            if model_status_res["endpoint"]:
                st.write(f"NP4-443 endpoint: {model_status_res['endpoint']}")

            if actual_error:
                st.write(f"NP4-746 parse/load error: {actual_error}")
            if hourly_error:
                st.write(f"NP4-745 parse/load error: {hourly_error}")
            if load_np4443 and model_status_error:
                st.write(f"NP4-443 parse/load error: {model_status_error}")
            elif not load_np4443:
                st.write("NP4-443 status: skipped")

            if actual_res["ok"]:
                st.write("NP4-746 columns:")
                st.write(list(actual_res["df"].columns))
            if hourly_res["ok"]:
                st.write("NP4-745 columns:")
                st.write(list(hourly_res["df"].columns))
            if model_status_res["ok"]:
                st.write("NP4-443 columns:")
                st.write(list(model_status_res["df"].columns))

    except Exception as e:
        st.error(str(e))
# -----------------------------
# TAB 4 - NET LOAD DATA
# -----------------------------
if page == "Load Forecast View":
    st.caption("ERCOT Intra-Hour Load Forecast by Weather Zone")

    LOAD_FORECAST_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np3-562-cd"

# ---------------------------------------------------
# API HELPERS
# ---------------------------------------------------
    @st.cache_data(ttl=300)
    def get_product_metadata_tab4(product_url):
        r = requests.get(product_url, headers=get_headers(), timeout=60)
        r.raise_for_status()
        return r.json()

    def choose_best_artifact_tab4(artifacts):
        best = None
        best_score = -999

        for a in artifacts:
            endpoint = a.get("_links", {}).get("endpoint", {}).get("href", "")
            name = f"{a.get('friendlyName', '')} {a.get('name', '')}".lower()
            blob = f"{endpoint} {name}".lower()

            score = 0
            if endpoint:
                score += 1
            if "csv" in blob:
                score += 5
            if "xml" in blob:
                score -= 2
            if "zip" in blob:
                score -= 2
            if "report" in blob or "view" in blob or "data" in blob:
                score += 2

            if score > best_score:
                best_score = score
                best = a

        return best

    @st.cache_data(ttl=300)
    def get_endpoint_tab4(product_url):
        product = get_product_metadata_tab4(product_url)
        artifacts = product.get("artifacts", [])

        if not artifacts:
            raise ValueError(f"No artifacts found for {product_url}")

        best = choose_best_artifact_tab4(artifacts)
        endpoint = best.get("_links", {}).get("endpoint", {}).get("href")

        if not endpoint:
            raise ValueError(f"No endpoint found for {product_url}")

        return endpoint

    @st.cache_data(ttl=300)
    def load_report_tab4(endpoint, size=5000):
        r = requests.get(
            endpoint,
            headers=get_headers(),
            params={"size": size},
            timeout=120
        )
        r.raise_for_status()

        payload = r.json()
        fields = payload.get("fields", [])
        rows = payload.get("data", [])

        if not fields:
            raise ValueError(f"No fields returned from endpoint: {endpoint}")

        cols = [f["name"] for f in fields]
        return pd.DataFrame(rows, columns=cols)

# ---------------------------------------------------
# HELPERS
# ---------------------------------------------------
    def detect_time_col_tab4(df):
        preferred = ["IntervalEnding", "intervalEnding", "timestamp", "datetime"]
        for c in preferred:
            if c in df.columns:
                return c

        for c in df.columns:
            cl = c.lower()
            if "interval" in cl and "ending" in cl:
                return c
            if "time" in cl or "date" in cl:
                return c

        return None

    def clean_region_name(raw_col):
        mapping = {
            "SystemTotal": "ERCOT Total",
            "Coast": "Coast",
            "East": "East",
            "FarWest": "Far West",
            "North": "North",
            "NorthCentral": "North Central",
            "SouthCentral": "South Central",
            "Southern": "South",
            "West": "West",
        }
        return mapping.get(raw_col, raw_col)

    def detect_region_columns_tab4(df):
        exclude = {"Model", "InUseFlag", "DSTFlag"}
        time_col = detect_time_col_tab4(df)

        region_cols = []
        for c in df.columns:
            if c == time_col or c in exclude:
                continue

            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().sum() > 0:
                region_cols.append(c)

        return region_cols

    def normalize_forecast_tab4(df):
        time_col = detect_time_col_tab4(df)
        if not time_col:
            raise ValueError(f"Could not detect IntervalEnding column. Columns: {list(df.columns)}")

        region_cols = detect_region_columns_tab4(df)
        if not region_cols:
            raise ValueError(f"Could not detect region columns with data. Columns: {list(df.columns)}")

        out = df.copy()
        out[time_col] = pd.to_datetime(out[time_col], errors="coerce")

        if "InUseFlag" in out.columns:
            active = out[out["InUseFlag"].astype(str).str.upper() == "Y"].copy()
            if not active.empty:
                out = active

        keep_cols = [time_col] + region_cols
        out = out[keep_cols].copy()

        rename_map = {c: clean_region_name(c) for c in region_cols}
        out = out.rename(columns=rename_map)

        for c in rename_map.values():
            out[c] = pd.to_numeric(out[c], errors="coerce")

        out = (
            out.dropna(subset=[time_col])
            .sort_values(time_col)
            .drop_duplicates(subset=[time_col], keep="last")
            .set_index(time_col)
            .sort_index()
        )

        return out

    def align_index_tab4(df, start_ts, end_ts):
        if df.empty:
            return df
        return df[(df.index >= start_ts) & (df.index <= end_ts)].copy()

# ---------------------------------------------------
# CONTROLS
# ---------------------------------------------------
    try:
        st.subheader("Display Controls")

        period = st.selectbox(
            "Window",
            ["Next 2 Hours"],
            index=0,
            key="np3562_period_final"
        )

        line_style = st.selectbox(
            "Forecast Line Style",
            ["Dashed", "Solid", "Dotted"],
            index=0,
            key="np3562_linestyle_final"
        )

        dash_map = {
            "Dashed": "dash",
            "Solid": "solid",
            "Dotted": "dot",
        }

        ercot_now = pd.Timestamp.now(tz="America/Chicago").tz_localize(None).floor("5min")
        start_ts = ercot_now - pd.Timedelta(minutes=5)
        end_ts = ercot_now + pd.Timedelta(hours=2)

# ---------------------------------------------------
# LOAD DATA
 # ---------------------------------------------------
        endpoint = get_endpoint_tab4(LOAD_FORECAST_PRODUCT_URL)
        raw = load_report_tab4(endpoint, size=5000)
        curve = normalize_forecast_tab4(raw)
        curve = align_index_tab4(curve, start_ts, end_ts)

        if curve.empty:
            st.warning("No forecast data returned from NP3-562-CD.")
            st.stop()

        available_regions = [c for c in curve.columns if curve[c].notna().sum() > 0]

        preferred_defaults = [
            "ERCOT Total",
            "Coast",
            "East",
            "Far West",
            "North",
            "North Central",
            "South Central",
            "South",
            "West",
        ]
        default_regions = [c for c in preferred_defaults if c in available_regions]

        selected_regions = st.multiselect(
            "Regions",
            options=available_regions,
            default=default_regions,
            key="np3562_regions_final"
        )

        if not selected_regions:
            st.warning("Select at least one region.")
            st.stop()

# ---------------------------------------------------
 # GRAPH
 # ---------------------------------------------------
        fig = go.Figure()

        for region in selected_regions:
            if region in curve.columns:
                width = 4 if region == "ERCOT Total" else 2

                fig.add_trace(
                    go.Scatter(
                        x=curve.index,
                        y=curve[region],
                        mode="lines",
                        name=region,
                        line=dict(
                            width=width,
                            dash=dash_map[line_style]
                        ),
                        hovertemplate=(
                            f"{region}<br>"
                            "Time: %{x}<br>"
                            "MW: %{y:,.0f}<extra></extra>"
                        )
                    )
                )

        fig.update_layout(
            title="NP3-562-CD Load Forecast by Weather Zone",
            xaxis_title="Time",
            yaxis_title="MW",
            hovermode="x unified",
            height=760,
        )

        st.plotly_chart(fig, use_container_width=True, key="np3562_chart_final")

# ---------------------------------------------------
# DOWNLOAD / TABLE
 # ---------------------------------------------------
        export_df = curve[selected_regions].copy()

        st.download_button(
            "Download CSV",
            data=export_df.reset_index().to_csv(index=False).encode("utf-8"),
            file_name="np3_562_load_forecast.csv",
            mime="text/csv",
            key="np3562_download_final"
        )

        with st.expander("Show data"):
            st.dataframe(export_df.reset_index(), use_container_width=True, hide_index=True)

        with st.expander("Debug info"):
            st.write(f"Endpoint: {endpoint}")
            st.write("Raw columns:")
            st.write(list(raw.columns))
            st.write("Detected data columns:")
            st.write(detect_region_columns_tab4(raw))
            st.write("Available plotted regions:")
            st.write(available_regions)

    except Exception as e:
        st.error(str(e))