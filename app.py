import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import numpy as np

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
        # TAB 2 - WIND DATA
        # -----------------------------
elif page == "Wind Trader View":
        st.caption("ERCOT Wind Trader View - Actuals, Forecast Errors, and Revisions")

        ACTUAL_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-743-cd"
        FORECAST_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-751-cd"


        # ---------------------------------------------------
        # PRODUCT / ARTIFACT HELPERS
        # ---------------------------------------------------
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
                    score += 5
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
            product = get_product_metadata(product_url)
            artifacts = product.get("artifacts", [])
            if not artifacts:
                raise ValueError(f"No artifacts found for product: {product_url}")

            best = choose_best_artifact(artifacts)
            endpoint = best.get("_links", {}).get("endpoint", {}).get("href")
            if not endpoint:
                raise ValueError(f"Could not find artifact endpoint for product: {product_url}")

            return endpoint, artifacts


        @st.cache_data(ttl=300)
        def load_report(endpoint: str, posted_from=None, posted_to=None, size=10000):
            params = {"size": size}
            if posted_from:
                params["postedDatetimeFrom"] = posted_from
            if posted_to:
                params["postedDatetimeTo"] = posted_to

            r = requests.get(endpoint, headers=get_headers(), params=params, timeout=120)
            r.raise_for_status()
            payload = r.json()

            fields = payload.get("fields", [])
            rows = payload.get("data", [])

            if not fields:
                raise ValueError(f"No fields returned from endpoint: {endpoint}")

            cols = [f["name"] for f in fields]
            return pd.DataFrame(rows, columns=cols)


        # ---------------------------------------------------
        # DETECTION HELPERS
        # ---------------------------------------------------
        def detect_time_col(df: pd.DataFrame) -> str | None:
            preferred = [
                "intervalEnding",
                "timestamp",
                "datetime",
                "postedDatetime",
            ]
            for c in preferred:
                if c in df.columns:
                    return c

            for c in df.columns:
                cl = c.lower()
                if "interval" in cl and "ending" in cl:
                    return c
            for c in df.columns:
                cl = c.lower()
                if "timestamp" in cl or "datetime" in cl or ("time" in cl and "posted" not in cl):
                    return c
            return None


        def detect_posted_col(df: pd.DataFrame) -> str | None:
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


        def detect_target_col(df: pd.DataFrame, posted_col: str | None) -> str | None:
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
            for c in df.columns:
                if c == posted_col:
                    continue
                cl = c.lower()
                if "forecast" in cl and "time" in cl:
                    return c
            for c in df.columns:
                if c == posted_col:
                    continue
                cl = c.lower()
                if "delivery" in cl and ("time" in cl or "date" in cl):
                    return c
            for c in df.columns:
                if c == posted_col:
                    continue
                cl = c.lower()
                if "time" in cl or "date" in cl or "timestamp" in cl or "datetime" in cl:
                    return c
            return None


        def build_region_aliases():
            return {
                "panhandle": "Panhandle",
                "coastal": "Coastal",
                "south": "South",
                "west": "West",
                "north": "North",
            }


        def find_wide_region_columns(df: pd.DataFrame):
            aliases = build_region_aliases()
            lower_map = {c.lower().strip(): c for c in df.columns}
            found = {}

            for alias, display in aliases.items():
                for lc, orig in lower_map.items():
                    norm = lc.replace("_", "").replace("-", "").replace(" ", "")
                    if norm == alias:
                        found[display] = orig
                        break

            return found


        def find_long_region_and_value_columns(df: pd.DataFrame):
            region_col = None
            value_col = None

            region_candidates = []
            for c in df.columns:
                cl = c.lower()
                if "region" in cl or "zone" in cl or "geograph" in cl:
                    region_candidates.append(c)

            value_candidates = []
            for c in df.columns:
                cl = c.lower()
                if any(x in cl for x in ["mw", "gen", "actual", "forecast", "output", "production", "value", "hsl"]):
                    numeric_test = pd.to_numeric(df[c], errors="coerce")
                    if numeric_test.notna().sum() > 0:
                        value_candidates.append(c)

            if region_candidates:
                region_col = region_candidates[0]
            if value_candidates:
                value_col = value_candidates[0]

            return region_col, value_col


        # ---------------------------------------------------
        # ACTUAL NORMALIZATION
        # ---------------------------------------------------
        def normalize_actual_regional_df(df: pd.DataFrame):
            df = convert_columns(df.copy())

            time_col = detect_time_col(df)
            if not time_col:
                raise ValueError(f"Actuals: Could not detect timestamp column. Columns: {list(df.columns)}")

            df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
            df = df.dropna(subset=[time_col]).copy()

            wide_cols = find_wide_region_columns(df)
            if wide_cols:
                out = df[[time_col] + list(wide_cols.values())].copy()
                out = out.rename(columns={v: k for k, v in wide_cols.items()})

                for c in wide_cols.keys():
                    out[c] = pd.to_numeric(out[c], errors="coerce")

                out = (
                    out.sort_values(time_col)
                    .drop_duplicates(subset=[time_col], keep="last")
                    .set_index(time_col)
                    .resample("5min")
                    .mean()
                )

                region_order = [r for r in ["Panhandle", "Coastal", "South", "West", "North"] if r in out.columns]
                out["ERCOT Total"] = out[region_order].sum(axis=1, min_count=1)
                out = out[["ERCOT Total"] + region_order]
                return out, time_col

            region_col, value_col = find_long_region_and_value_columns(df)
            if region_col and value_col:
                temp = df[[time_col, region_col, value_col]].copy()
                temp[value_col] = pd.to_numeric(temp[value_col], errors="coerce")
                temp = temp.dropna(subset=[value_col]).copy()

                aliases = build_region_aliases()

                def map_region(x):
                    if pd.isna(x):
                        return None
                    s = str(x).strip().lower()
                    s_norm = s.replace("_", "").replace("-", "").replace(" ", "")
                    for alias, display in aliases.items():
                        if s_norm == alias or alias in s_norm:
                            return display
                    return None

                temp["_region_std"] = temp[region_col].map(map_region)
                temp = temp.dropna(subset=["_region_std"]).copy()

                out = (
                    temp.pivot_table(
                        index=time_col,
                        columns="_region_std",
                        values=value_col,
                        aggfunc="mean"
                    )
                    .sort_index()
                    .resample("5min")
                    .mean()
                )

                region_order = [r for r in ["Panhandle", "Coastal", "South", "West", "North"] if r in out.columns]
                out["ERCOT Total"] = out[region_order].sum(axis=1, min_count=1)
                out = out[["ERCOT Total"] + region_order]
                return out, time_col

            raise ValueError(f"Actuals: Could not detect regional actuals format. Columns: {list(df.columns)}")


        # ---------------------------------------------------
        # FORECAST NORMALIZATION TO LONG FORMAT
        # posted_ts | target_ts | series | mw
        # ---------------------------------------------------
        def normalize_forecast_long(df: pd.DataFrame):
            df = convert_columns(df.copy())

            posted_col = detect_posted_col(df)
            target_col = detect_target_col(df, posted_col)

            if not posted_col:
                raise ValueError(f"Forecast: Could not detect posted timestamp column. Columns: {list(df.columns)}")
            if not target_col:
                raise ValueError(f"Forecast: Could not detect target timestamp column. Columns: {list(df.columns)}")

            df[posted_col] = pd.to_datetime(df[posted_col], errors="coerce")
            df[target_col] = pd.to_datetime(df[target_col], errors="coerce")
            df = df.dropna(subset=[posted_col, target_col]).copy()

            wide_cols = find_wide_region_columns(df)
            if wide_cols:
                keep_cols = [posted_col, target_col] + list(wide_cols.values())
                out = df[keep_cols].copy()
                out = out.rename(columns={v: k for k, v in wide_cols.items()})

                region_names = list(wide_cols.keys())
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

                aliases = build_region_aliases()

                def map_region(x):
                    if pd.isna(x):
                        return None
                    s = str(x).strip().lower()
                    s_norm = s.replace("_", "").replace("-", "").replace(" ", "")
                    for alias, display in aliases.items():
                        if s_norm == alias or alias in s_norm:
                            return display
                    return None

                temp["series"] = temp[region_col].map(map_region)
                temp = temp.dropna(subset=["series"]).copy()

                temp = temp.rename(columns={
                    posted_col: "posted_ts",
                    target_col: "target_ts",
                    value_col: "mw"
                })

                return temp[["posted_ts", "target_ts", "series", "mw"]], posted_col, target_col

            raise ValueError(f"Forecast: Could not detect regional forecast format. Columns: {list(df.columns)}")


        # ---------------------------------------------------
        # FORECAST CURVE CONSTRUCTION
        # ---------------------------------------------------
        def build_lead_curve(forecast_long: pd.DataFrame, target_lead_minutes: int, series_order):
            """
            For each target_ts and series, choose the forecast whose lead time
            (target_ts - posted_ts) is closest to target_lead_minutes, subject to posted_ts <= target_ts.
            """
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

            region_series = [s for s in ["Panhandle", "Coastal", "South", "West", "North"] if s in wide.columns]
            if region_series:
                wide["ERCOT Total"] = wide[region_series].sum(axis=1, min_count=1)

            ordered = [s for s in series_order if s in wide.columns]
            return wide[ordered] if ordered else wide


        def align_index(df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp):
            if df.empty:
                return df
            return df[(df.index >= start_ts) & (df.index <= end_ts)].copy()


        # ---------------------------------------------------
        # CONTROLS
        # ---------------------------------------------------
        try:
            st.subheader("Display Controls")

            view_mode = st.selectbox(
                "Mode",
                options=[
                    "Actual vs Forecast",
                    "Forecast Error",
                    "Forecast Revisions",
                ],
                index=0,
                key="wind_view_mode"
            )

            period = st.selectbox(
                "Selectable period",
                options=[
                    "Last 1 hour",
                    "Last 3 hours",
                    "Last 6 hours",
                    "Last 12 hours",
                    "Last 24 hours",
                ],
                index=2,
                key="wind_period"
            )

            now = pd.Timestamp.now().floor("5min")

            if period == "Last 1 hour":
                start_ts = now - pd.Timedelta(hours=1)
                end_ts = now
                hours_back = 1
            elif period == "Last 3 hours":
                start_ts = now - pd.Timedelta(hours=3)
                end_ts = now
                hours_back = 3
            elif period == "Last 6 hours":
                start_ts = now - pd.Timedelta(hours=6)
                end_ts = now
                hours_back = 6
            elif period == "Last 12 hours":
                start_ts = now - pd.Timedelta(hours=12)
                end_ts = now
                hours_back = 12
            else:
                start_ts = now - pd.Timedelta(hours=24)
                end_ts = now
                hours_back = 24

            available_series = ["ERCOT Total", "Panhandle", "Coastal", "South", "West", "North"]

            selected_series = st.multiselect(
                "Regions to graph",
                options=available_series,
                default=["ERCOT Total"],
                key="wind_selected_series"
            )

            if not selected_series:
                st.warning("Select at least one region or total.")
                st.stop()

            lead_options = {
                "Latest": 0,
                "1 hour ago": 60,
                "2 hours ago": 120,
            }

            selected_leads = st.multiselect(
                "Forecast lines",
                options=list(lead_options.keys()),
                default=["Latest", "1 hour ago"],
                key="wind_selected_leads"
            )

            if not selected_leads:
                st.warning("Select at least one forecast line.")
                st.stop()

            if hours_back <= 3:
                actual_size = 5000
                forecast_size = 15000
            elif hours_back <= 6:
                actual_size = 8000
                forecast_size = 25000
            elif hours_back <= 12:
                actual_size = 12000
                forecast_size = 40000
            else:
                actual_size = 18000
                forecast_size = 70000

            forecast_posted_start = start_ts - pd.Timedelta(hours=3)

            actual_posted_from = start_ts.strftime("%Y-%m-%dT%H:%M")
            actual_posted_to = end_ts.strftime("%Y-%m-%dT%H:%M")

            forecast_posted_from = forecast_posted_start.strftime("%Y-%m-%dT%H:%M")
            forecast_posted_to = end_ts.strftime("%Y-%m-%dT%H:%M")

            actual_endpoint, _ = get_artifact_endpoint(ACTUAL_PRODUCT_URL)
            forecast_endpoint, _ = get_artifact_endpoint(FORECAST_PRODUCT_URL)

            actual_raw = load_report(actual_endpoint, actual_posted_from, actual_posted_to, actual_size)
            forecast_raw = load_report(forecast_endpoint, forecast_posted_from, forecast_posted_to, forecast_size)

            actual_regional_df, actual_time_col = normalize_actual_regional_df(actual_raw)
            forecast_long, forecast_posted_col, forecast_target_col = normalize_forecast_long(forecast_raw)

            actual_regional_df = align_index(actual_regional_df, start_ts, end_ts)
            forecast_long = forecast_long[
                (forecast_long["target_ts"] >= start_ts) &
                (forecast_long["target_ts"] <= end_ts)
                ].copy()

            if actual_regional_df.empty:
                st.warning("No actual wind data returned for that period.")
                st.stop()

            if forecast_long.empty:
                st.warning("No forecast wind data returned for that period.")
                st.stop()

            selected_available_series = [
                s for s in selected_series
                if s in actual_regional_df.columns
                   or s in forecast_long["series"].unique().tolist()
                   or s == "ERCOT Total"
            ]

            if not selected_available_series:
                st.warning("Selected series were not found in the returned data.")
                st.stop()

            forecast_curves = {}
            for lead_label in selected_leads:
                lead_minutes = lead_options[lead_label]
                curve = build_lead_curve(forecast_long, lead_minutes, available_series)
                curve = align_index(curve, start_ts, end_ts)
                forecast_curves[lead_label] = curve

            st.subheader("Wind Trader Graph")
            st.caption("Use the multiselect to declutter. You can also click legend items to hide/show traces.")

            palette = px.colors.qualitative.Plotly
            color_map = {series: palette[i % len(palette)] for i, series in enumerate(available_series)}

            fig = go.Figure()

            if view_mode == "Actual vs Forecast":
                for series_name in selected_available_series:
                    if series_name in actual_regional_df.columns:
                        fig.add_trace(
                            go.Scatter(
                                x=actual_regional_df.index,
                                y=actual_regional_df[series_name],
                                mode="lines",
                                name=f"{series_name} Actual",
                                line=dict(color=color_map[series_name], dash="solid"),
                                hovertemplate=(
                                    f"Series: {series_name} Actual<br>"
                                    "Time: %{x}<br>"
                                    "MW: %{y:,.0f}<extra></extra>"
                                ),
                            )
                        )

                    for lead_label in selected_leads:
                        curve = forecast_curves.get(lead_label, pd.DataFrame())
                        if not curve.empty and series_name in curve.columns:
                            dash_style = {
                                "Latest": "dash",
                                "1 hour ago": "dot",
                                "2 hours ago": "dashdot",
                            }.get(lead_label, "dash")

                            fig.add_trace(
                                go.Scatter(
                                    x=curve.index,
                                    y=curve[series_name],
                                    mode="lines",
                                    name=f"{series_name} Forecast {lead_label}",
                                    line=dict(color=color_map[series_name], dash=dash_style),
                                    hovertemplate=(
                                        f"Series: {series_name} Forecast {lead_label}<br>"
                                        "Time: %{x}<br>"
                                        "MW: %{y:,.0f}<extra></extra>"
                                    ),
                                )
                            )

                fig.update_layout(
                    xaxis_title="Time",
                    yaxis_title="MW",
                    hovermode="x unified",
                    legend_title_text="Series",
                    height=700,
                    margin=dict(l=20, r=20, t=20, b=20),
                )

            elif view_mode == "Forecast Error":
                for series_name in selected_available_series:
                    if series_name not in actual_regional_df.columns:
                        continue

                    for lead_label in selected_leads:
                        curve = forecast_curves.get(lead_label, pd.DataFrame())
                        if curve.empty or series_name not in curve.columns:
                            continue

                        combined = pd.concat(
                            [
                                actual_regional_df[[series_name]].rename(columns={series_name: "actual"}),
                                curve[[series_name]].rename(columns={series_name: "forecast"})
                            ],
                            axis=1
                        )
                        combined["error"] = combined["forecast"] - combined["actual"]

                        dash_style = {
                            "Latest": "dash",
                            "1 hour ago": "dot",
                            "2 hours ago": "dashdot",
                        }.get(lead_label, "dash")

                        fig.add_trace(
                            go.Scatter(
                                x=combined.index,
                                y=combined["error"],
                                mode="lines",
                                name=f"{series_name} Error {lead_label}",
                                line=dict(color=color_map[series_name], dash=dash_style),
                                hovertemplate=(
                                    f"Series: {series_name} Error {lead_label}<br>"
                                    "Time: %{x}<br>"
                                    "Forecast - Actual: %{y:,.0f} MW<extra></extra>"
                                ),
                            )
                        )

                fig.update_layout(
                    xaxis_title="Time",
                    yaxis_title="Forecast - Actual (MW)",
                    hovermode="x unified",
                    legend_title_text="Series",
                    height=700,
                    margin=dict(l=20, r=20, t=20, b=20),
                )
                fig.add_hline(y=0, line_dash="solid", line_width=1)

            else:
                latest_curve = forecast_curves.get("Latest", pd.DataFrame())
                older_labels = [x for x in selected_leads if x != "Latest"]

                if latest_curve.empty:
                    st.warning("Forecast Revisions mode needs 'Latest' selected.")
                    st.stop()

                if not older_labels:
                    st.warning("Forecast Revisions mode needs 'Latest' plus at least one older forecast line.")
                    st.stop()

                for series_name in selected_available_series:
                    if series_name not in latest_curve.columns:
                        continue

                    for older_label in older_labels:
                        older_curve = forecast_curves.get(older_label, pd.DataFrame())
                        if older_curve.empty or series_name not in older_curve.columns:
                            continue

                        combined = pd.concat(
                            [
                                latest_curve[[series_name]].rename(columns={series_name: "latest"}),
                                older_curve[[series_name]].rename(columns={series_name: "older"})
                            ],
                            axis=1
                        )
                        combined["revision"] = combined["latest"] - combined["older"]

                        dash_style = {
                            "1 hour ago": "dot",
                            "2 hours ago": "dashdot",
                        }.get(older_label, "dot")

                        fig.add_trace(
                            go.Scatter(
                                x=combined.index,
                                y=combined["revision"],
                                mode="lines",
                                name=f"{series_name} Revision vs {older_label}",
                                line=dict(color=color_map[series_name], dash=dash_style),
                                hovertemplate=(
                                    f"Series: {series_name} Revision vs {older_label}<br>"
                                    "Time: %{x}<br>"
                                    "Latest - Older: %{y:,.0f} MW<extra></extra>"
                                ),
                            )
                        )

                fig.update_layout(
                    xaxis_title="Time",
                    yaxis_title="Latest Forecast - Older Forecast (MW)",
                    hovermode="x unified",
                    legend_title_text="Series",
                    height=700,
                    margin=dict(l=20, r=20, t=20, b=20),
                )
                fig.add_hline(y=0, line_dash="solid", line_width=1)

            st.plotly_chart(
                fig,
                use_container_width=True,
                key="wind_trader_chart"
            )

            export_df = pd.DataFrame(index=actual_regional_df.index)

            if view_mode == "Actual vs Forecast":
                for series_name in selected_available_series:
                    if series_name in actual_regional_df.columns:
                        export_df[f"{series_name} Actual"] = actual_regional_df[series_name]
                    for lead_label in selected_leads:
                        curve = forecast_curves.get(lead_label, pd.DataFrame())
                        if not curve.empty and series_name in curve.columns:
                            export_df[f"{series_name} Forecast {lead_label}"] = curve[series_name]

            elif view_mode == "Forecast Error":
                for series_name in selected_available_series:
                    if series_name not in actual_regional_df.columns:
                        continue
                    for lead_label in selected_leads:
                        curve = forecast_curves.get(lead_label, pd.DataFrame())
                        if curve.empty or series_name not in curve.columns:
                            continue
                        combined = pd.concat(
                            [
                                actual_regional_df[[series_name]].rename(columns={series_name: "actual"}),
                                curve[[series_name]].rename(columns={series_name: "forecast"})
                            ],
                            axis=1
                        )
                        export_df[f"{series_name} Error {lead_label}"] = combined["forecast"] - combined["actual"]

            else:
                latest_curve = forecast_curves.get("Latest", pd.DataFrame())
                for series_name in selected_available_series:
                    if latest_curve.empty or series_name not in latest_curve.columns:
                        continue
                    for older_label in [x for x in selected_leads if x != "Latest"]:
                        older_curve = forecast_curves.get(older_label, pd.DataFrame())
                        if older_curve.empty or series_name not in older_curve.columns:
                            continue
                        combined = pd.concat(
                            [
                                latest_curve[[series_name]].rename(columns={series_name: "latest"}),
                                older_curve[[series_name]].rename(columns={series_name: "older"})
                            ],
                            axis=1
                        )
                        export_df[f"{series_name} Revision vs {older_label}"] = combined["latest"] - combined["older"]

            csv = export_df.reset_index().to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download graphed data as CSV",
                data=csv,
                file_name="ercot_wind_trader_view.csv",
                mime="text/csv",
                key="wind_trader_download"
            )

            with st.expander("Show graphed data"):
                st.dataframe(
                    export_df.reset_index(),
                    use_container_width=True,
                    hide_index=True
                )

            with st.expander("Debug info"):
                st.write(f"Actual endpoint: {actual_endpoint}")
                st.write(f"Forecast endpoint: {forecast_endpoint}")
                st.write(f"Actual rows returned: {len(actual_raw):,}")
                st.write(f"Forecast rows returned: {len(forecast_raw):,}")
                st.write(f"Actual timestamp column: {actual_time_col}")
                st.write(f"Forecast posted column: {forecast_posted_col}")
                st.write(f"Forecast target column: {forecast_target_col}")
                st.write("Forecast columns:")
                st.write(list(forecast_raw.columns))

        except Exception as e:
            st.error(str(e))
        # -----------------------------
        # TAB 3 - SOLAR DATA
        # -----------------------------
elif page == "Solar Trader View":
    st.caption("ERCOT Solar Trader View - Actuals, Forecast Errors, Revisions, and 168-Hour Outlook")

    ACTUAL_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-746-cd"
    INTRAHOUR_FORECAST_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-752-cd"
    HOURLY_FORECAST_PRODUCT_URL = "https://api.ercot.com/api/public-reports/np4-443-cd"

    # ---------------------------------------------------
    # PRODUCT / ARTIFACT HELPERS
    # ---------------------------------------------------
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
                score += 5
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
        product = get_product_metadata(product_url)
        artifacts = product.get("artifacts", [])
        if not artifacts:
            raise ValueError(f"No artifacts found for product: {product_url}")

        best = choose_best_artifact(artifacts)
        endpoint = best.get("_links", {}).get("endpoint", {}).get("href")
        if not endpoint:
            raise ValueError(f"Could not find artifact endpoint for product: {product_url}")

        return endpoint, artifacts

    @st.cache_data(ttl=300)
    def load_report(endpoint: str, posted_from=None, posted_to=None, size=10000):
        params = {"size": size}
        if posted_from:
            params["postedDatetimeFrom"] = posted_from
        if posted_to:
            params["postedDatetimeTo"] = posted_to

        r = requests.get(endpoint, headers=get_headers(), params=params, timeout=120)
        r.raise_for_status()
        payload = r.json()

        fields = payload.get("fields", [])
        rows = payload.get("data", [])

        if not fields:
            raise ValueError(f"No fields returned from endpoint: {endpoint}")

        cols = [f["name"] for f in fields]
        return pd.DataFrame(rows, columns=cols)

    # ---------------------------------------------------
    # DETECTION HELPERS
    # ---------------------------------------------------
    def detect_time_col(df: pd.DataFrame) -> str | None:
        preferred = ["intervalEnding", "timestamp", "datetime", "postedDatetime"]
        for c in preferred:
            if c in df.columns:
                return c

        for c in df.columns:
            cl = c.lower()
            if "interval" in cl and "ending" in cl:
                return c
        for c in df.columns:
            cl = c.lower()
            if "timestamp" in cl or "datetime" in cl or ("time" in cl and "posted" not in cl):
                return c
        return None

    def detect_posted_col(df: pd.DataFrame) -> str | None:
        preferred = ["postedDatetime", "postedTime", "publishTime", "issueTime", "createdDatetime"]
        for c in preferred:
            if c in df.columns:
                return c

        for c in df.columns:
            cl = c.lower()
            if "posted" in cl or "publish" in cl or "issue" in cl or "created" in cl:
                return c
        return None

    def detect_target_col(df: pd.DataFrame, posted_col: str | None) -> str | None:
        preferred = ["intervalEnding", "forecastTime", "deliveryTime", "deliveryDatetime", "timestamp", "datetime"]
        for c in preferred:
            if c in df.columns and c != posted_col:
                return c

        for c in df.columns:
            if c == posted_col:
                continue
            cl = c.lower()
            if "interval" in cl and "ending" in cl:
                return c
        for c in df.columns:
            if c == posted_col:
                continue
            cl = c.lower()
            if "forecast" in cl and "time" in cl:
                return c
        for c in df.columns:
            if c == posted_col:
                continue
            cl = c.lower()
            if "delivery" in cl and ("time" in cl or "date" in cl):
                return c
        for c in df.columns:
            if c == posted_col:
                continue
            cl = c.lower()
            if "time" in cl or "date" in cl or "timestamp" in cl or "datetime" in cl:
                return c
        return None

    # ---------------------------------------------------
    # SOLAR REGION HELPERS
    # ---------------------------------------------------
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

    def build_solar_region_aliases():
        return {
            "centerwest": "Center West",
            "northwest": "North West",
            "farwest": "Far West",
            "fareast": "Far East",
            "southeast": "South East",
            "centereast": "Center East",
            "systemwide": "ERCOT Total",
            "systemtotal": "ERCOT Total",
            "system_total": "ERCOT Total",
        }

    def normalize_region_name(x):
        if pd.isna(x):
            return None
        s = str(x).strip().lower()
        s_norm = s.replace("_", "").replace("-", "").replace(" ", "")
        aliases = build_solar_region_aliases()
        for alias, display in aliases.items():
            if s_norm == alias or alias in s_norm:
                return display
        return None

    def find_solar_wide_region_columns(df: pd.DataFrame):
        aliases = build_solar_region_aliases()
        found = {}

        for col in df.columns:
            lc = col.lower()
            norm = lc.replace("_", "").replace("-", "").replace(" ", "")
            for alias, display in aliases.items():
                if alias in norm:
                    found[display] = col

        return found

    def find_long_region_and_value_columns(df: pd.DataFrame):
        region_col = None
        value_col = None

        region_candidates = []
        for c in df.columns:
            cl = c.lower()
            if "region" in cl or "zone" in cl or "geograph" in cl:
                region_candidates.append(c)

        value_candidates = []
        for c in df.columns:
            cl = c.lower()
            if any(x in cl for x in ["mw", "gen", "actual", "forecast", "output", "production", "value", "hsl"]):
                numeric_test = pd.to_numeric(df[c], errors="coerce")
                if numeric_test.notna().sum() > 0:
                    value_candidates.append(c)

        if region_candidates:
            region_col = region_candidates[0]
        if value_candidates:
            value_col = value_candidates[0]

        return region_col, value_col

    # ---------------------------------------------------
    # ACTUAL NORMALIZATION
    # ---------------------------------------------------
    def normalize_actual_regional_df(df: pd.DataFrame):
        df = convert_columns(df.copy())

        time_col = detect_time_col(df)
        if not time_col:
            raise ValueError(f"Actuals: Could not detect timestamp column. Columns: {list(df.columns)}")

        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        df = df.dropna(subset=[time_col]).copy()

        wide_cols = find_solar_wide_region_columns(df)
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

            regional_components = [
                c for c in ["Center West", "North West", "Far West", "Far East", "South East", "Center East"]
                if c in out.columns
            ]

            if "ERCOT Total" not in out.columns and regional_components:
                out["ERCOT Total"] = out[regional_components].sum(axis=1, min_count=1)

            ordered = [c for c in solar_region_order() if c in out.columns]
            return out[ordered], time_col

        region_col, value_col = find_long_region_and_value_columns(df)
        if region_col and value_col:
            temp = df[[time_col, region_col, value_col]].copy()
            temp[value_col] = pd.to_numeric(temp[value_col], errors="coerce")
            temp = temp.dropna(subset=[value_col]).copy()
            temp["series"] = temp[region_col].map(normalize_region_name)
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

            regional_components = [
                c for c in ["Center West", "North West", "Far West", "Far East", "South East", "Center East"]
                if c in out.columns
            ]

            if "ERCOT Total" not in out.columns and regional_components:
                out["ERCOT Total"] = out[regional_components].sum(axis=1, min_count=1)

            ordered = [c for c in solar_region_order() if c in out.columns]
            return out[ordered], time_col

        raise ValueError(f"Actuals: Could not detect regional actuals format. Columns: {list(df.columns)}")

    # ---------------------------------------------------
    # INTRAHOUR FORECAST NORMALIZATION
    # ---------------------------------------------------
    def normalize_intrahour_forecast_long(df: pd.DataFrame):
        df = convert_columns(df.copy())

        posted_col = detect_posted_col(df)
        target_col = detect_target_col(df, posted_col)

        if not posted_col:
            raise ValueError(f"Intra-hour forecast: Could not detect posted timestamp column. Columns: {list(df.columns)}")
        if not target_col:
            raise ValueError(f"Intra-hour forecast: Could not detect target timestamp column. Columns: {list(df.columns)}")

        df[posted_col] = pd.to_datetime(df[posted_col], errors="coerce")
        df[target_col] = pd.to_datetime(df[target_col], errors="coerce")
        df = df.dropna(subset=[posted_col, target_col]).copy()

        wide_cols = find_solar_wide_region_columns(df)
        if wide_cols:
            keep_cols = [posted_col, target_col] + list(wide_cols.values())
            out = df[keep_cols].copy()
            out = out.rename(columns={v: k for k, v in wide_cols.items()})

            region_names = [c for c in solar_region_order() if c in out.columns]
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

            long_df["source"] = "Intra-hour"
            long_df["model"] = "Latest"
            return long_df, posted_col, target_col

        region_col, value_col = find_long_region_and_value_columns(df)
        if region_col and value_col:
            temp = df[[posted_col, target_col, region_col, value_col]].copy()
            temp[value_col] = pd.to_numeric(temp[value_col], errors="coerce")
            temp = temp.dropna(subset=[value_col]).copy()
            temp["series"] = temp[region_col].map(normalize_region_name)
            temp = temp.dropna(subset=["series"]).copy()

            temp = temp.rename(columns={
                posted_col: "posted_ts",
                target_col: "target_ts",
                value_col: "mw"
            })

            temp["source"] = "Intra-hour"
            temp["model"] = "Latest"
            return temp[["posted_ts", "target_ts", "series", "mw", "source", "model"]], posted_col, target_col

        raise ValueError(f"Intra-hour forecast: Could not detect regional forecast format. Columns: {list(df.columns)}")

    # ---------------------------------------------------
    # NP4-443 HOURLY FORECAST NORMALIZATION
    # ---------------------------------------------------
    def normalize_np4443_hourly(df: pd.DataFrame):
        df = df.copy()

        original_cols = list(df.columns)
        norm_map = {c: c.strip() for c in df.columns}
        df = df.rename(columns=norm_map)

        lower_to_orig = {c.lower().replace("_", "").replace(" ", ""): c for c in df.columns}

        def pick_col(candidates):
            for cand in candidates:
                key = cand.lower().replace("_", "").replace(" ", "")
                if key in lower_to_orig:
                    return lower_to_orig[key]
            return None

        delivery_col = pick_col(["DeliveryDate", "deliveryDate", "OperatingDate", "date"])
        he_col = pick_col(["HourEnding", "hourEnding", "HE", "hour"])
        region_col = pick_col(["Region", "region", "WeatherZone", "zone"])
        value_col = pick_col(["Value", "value", "MW", "mw", "Forecast", "forecast"])
        model_col = pick_col(["Model", "model"])
        inuse_col = pick_col(["InUseFlag", "inUseFlag", "activeFlag"])

        missing = []
        if not delivery_col:
            missing.append("DeliveryDate/date")
        if not he_col:
            missing.append("HourEnding/HE")
        if not region_col:
            missing.append("Region")
        if not value_col:
            missing.append("Value/MW")

        if missing:
            raise ValueError(
                f"NP4-443 columns not detected. Missing: {missing}. "
                f"Actual columns returned: {original_cols}"
            )

        if inuse_col:
            active = df[df[inuse_col].astype(str).str.upper() == "Y"].copy()
            if not active.empty:
                df = active

        df[delivery_col] = pd.to_datetime(df[delivery_col], errors="coerce")
        df[he_col] = pd.to_numeric(df[he_col], errors="coerce")
        df[value_col] = pd.to_numeric(df[value_col], errors="coerce")

        df = df.dropna(subset=[delivery_col, he_col, region_col, value_col]).copy()

        df["target_ts"] = df[delivery_col] + pd.to_timedelta(df[he_col], unit="h")
        df["series"] = df[region_col].map(normalize_region_name)
        df = df.dropna(subset=["series"]).copy()

        if model_col:
            df["model"] = df[model_col].astype(str)
        else:
            df["model"] = "NP4-443"

        out = df.rename(columns={value_col: "mw"})
        out["source"] = "Hourly NP4-443"

        return out[["target_ts", "series", "mw", "model", "source"]].copy()

    def build_intrahour_lead_curve(forecast_long: pd.DataFrame, target_lead_minutes: int, series_order):
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

        regional_components = [
            c for c in ["Center West", "North West", "Far West", "Far East", "South East", "Center East"]
            if c in wide.columns
        ]

        if "ERCOT Total" not in wide.columns and regional_components:
            wide["ERCOT Total"] = wide[regional_components].sum(axis=1, min_count=1)

        ordered = [s for s in series_order if s in wide.columns]
        return wide[ordered] if ordered else wide

    def build_np4443_curve(hourly_long: pd.DataFrame, model_name: str, series_order):
        df = hourly_long.copy()
        if model_name != "All In-Use Models":
            df = df[df["model"] == model_name].copy()

        if df.empty:
            return pd.DataFrame()

        wide = (
            df.pivot_table(
                index="target_ts",
                columns="series",
                values="mw",
                aggfunc="mean"
            )
            .sort_index()
        )

        regional_components = [
            c for c in ["Center West", "North West", "Far West", "Far East", "South East", "Center East"]
            if c in wide.columns
        ]

        if "ERCOT Total" not in wide.columns and regional_components:
            wide["ERCOT Total"] = wide[regional_components].sum(axis=1, min_count=1)

        ordered = [s for s in series_order if s in wide.columns]
        return wide[ordered] if ordered else wide

    def align_index(df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp):
        if df.empty:
            return df
        return df[(df.index >= start_ts) & (df.index <= end_ts)].copy()

    def make_error_frame(actual_df: pd.DataFrame, forecast_df: pd.DataFrame, series_name: str):
        combined = pd.concat(
            [
                actual_df[[series_name]].rename(columns={series_name: "actual"}),
                forecast_df[[series_name]].rename(columns={series_name: "forecast"}),
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

    def get_active_np4443_model_info(df: pd.DataFrame):
            if df.empty:
                return None

            work = df.copy()
            lower_map = {c.lower().replace("_", "").replace(" ", ""): c for c in work.columns}

            model_col = None
            inuse_col = None
            delivery_col = None
            he_col = None

            for key, col in lower_map.items():
                if key == "model":
                    model_col = col
                elif key == "inuseflag":
                    inuse_col = col
                elif key == "deliverydate":
                    delivery_col = col
                elif key == "hourending":
                    he_col = col

            if not model_col:
                return {
                    "status": "no_model_column",
                    "message": "NP4-443 did not include a detectable Model column."
         }

            # Build target timestamp when possible
            if delivery_col and he_col:
                work[delivery_col] = pd.to_datetime(work[delivery_col], errors="coerce")
                work[he_col] = pd.to_numeric(work[he_col], errors="coerce")
                work = work.dropna(subset=[delivery_col, he_col]).copy()
                if not work.empty:
                    work["target_ts"] = work[delivery_col] + pd.to_timedelta(work[he_col], unit="h")

            # Preferred path: explicit active flag
            if inuse_col:
                work["_inuse_norm"] = work[inuse_col].astype(str).str.strip().str.upper()

                active = work[work["_inuse_norm"].isin(["Y", "YES", "TRUE", "1"])].copy()
                if not active.empty:
                    model_name = active[model_col].astype(str).mode().iloc[0]
                    next_target = active["target_ts"].min() if "target_ts" in active.columns else None
                    return {
                        "status": "active_flag_found",
                        "model": model_name,
                        "next_target_ts": next_target,
                        "row_count": len(active),
                        "flag_values": sorted(work["_inuse_norm"].dropna().unique().tolist()),
                    }

            # Fallback: most recent target/model available
            if "target_ts" in work.columns and not work.empty:
                latest_target = work["target_ts"].max()
                latest_rows = work[work["target_ts"] == latest_target].copy()
                models = sorted(latest_rows[model_col].astype(str).dropna().unique().tolist())

                return {
                    "status": "fallback_latest_target",
                    "models": models,
                    "latest_target_ts": latest_target,
                    "row_count": len(latest_rows),
                    "flag_values": (
                        sorted(work[inuse_col].astype(str).dropna().unique().tolist())
                        if inuse_col and inuse_col in work.columns else []
                    ),
                }

            # Last fallback: just list available models
            models = sorted(work[model_col].astype(str).dropna().unique().tolist())
            return {
                "status": "fallback_models_only",
                "models": models,
                "row_count": len(work),
                "flag_values": (
                    sorted(work[inuse_col].astype(str).dropna().unique().tolist())
                    if inuse_col and inuse_col in work.columns else []
                ),
            }

            return None

    def get_intrahour_status(forecast_long: pd.DataFrame, selected_leads, lead_options, now_ts):
        if forecast_long.empty:
            return None

        out = []

        for lead_label in selected_leads:
            target_lead_minutes = lead_options[lead_label]
            df = forecast_long.copy()
            df["lead_minutes"] = (df["target_ts"] - df["posted_ts"]).dt.total_seconds() / 60.0
            df = df[df["lead_minutes"] >= 0].copy()
            df["score"] = (df["lead_minutes"] - target_lead_minutes).abs()

            future_df = df[df["target_ts"] >= now_ts].copy()
            if future_df.empty:
                continue

            future_df = future_df.sort_values(["target_ts", "score", "posted_ts"], ascending=[True, True, False])
            first_target = future_df["target_ts"].min()
            picked = future_df[future_df["target_ts"] == first_target].sort_values(
                ["score", "posted_ts"], ascending=[True, False]
            )

            if picked.empty:
                continue

            best = picked.iloc[0]
            out.append({
                "lead_label": lead_label,
                "target_ts": best["target_ts"],
                "posted_ts": best["posted_ts"],
                "lead_minutes": best["lead_minutes"],
            })

        return out

    # ---------------------------------------------------
    # CONTROLS
    # ---------------------------------------------------
    try:
        st.subheader("Display Controls")

        view_mode = st.selectbox(
            "Mode",
            options=[
                "Actual vs Forecast",
                "Forecast Error",
                "Forecast Revisions",
                "Error Summary",
                "Table View",
            ],
            index=0,
            key="solar_view_mode_v2"
        )

        uses_error_display = view_mode == "Forecast Error"
        uses_revisions = view_mode == "Forecast Revisions"
        uses_table_toggles = view_mode == "Table View"
        uses_np4443_controls = view_mode in [
            "Actual vs Forecast",
            "Forecast Error",
            "Error Summary",
            "Table View",
        ]
        uses_intrahour_lines = view_mode in [
            "Actual vs Forecast",
            "Forecast Error",
            "Forecast Revisions",
            "Error Summary",
            "Table View",
        ]

        period = st.selectbox(
            "Selectable period",
            options=[
                "Last 6 hours",
                "Last 12 hours",
                "Last 24 hours",
                "Last 48 hours",
                "Last 72 hours",
                "Last 168 hours",
            ],
            index=2,
            key="solar_period_v2"
        )

        hours_back_map = {
            "Last 6 hours": 6,
            "Last 12 hours": 12,
            "Last 24 hours": 24,
            "Last 48 hours": 48,
            "Last 72 hours": 72,
            "Last 168 hours": 168,
        }

        now = pd.Timestamp.now(tz="America/Chicago").tz_localize(None).floor("5min")
        hours_back = hours_back_map[period]
        start_ts = now - pd.Timedelta(hours=hours_back)
        end_ts = now

        available_series = [
            "ERCOT Total",
            "Center West",
            "North West",
            "Far West",
            "Far East",
            "South East",
            "Center East",
        ]

        selected_series = st.multiselect(
            "Regions to graph",
            options=available_series,
            default=["ERCOT Total"],
            key="solar_selected_series_v2"
        )

        if not selected_series:
            st.warning("Select at least one region or total.")
            st.stop()

        intrahour_lead_options = {
            "Latest": 0,
            "1 hour ago": 60,
            "2 hours ago": 120,
        }

        default_intrahour_leads = ["Latest", "1 hour ago"]
        selected_intrahour_leads = st.multiselect(
            "Intra-hour forecast lines",
            options=list(intrahour_lead_options.keys()),
            default=default_intrahour_leads,
            key="solar_selected_intrahour_leads_v2",
            disabled=not uses_intrahour_lines,
            help=None if uses_intrahour_lines else "This setting does not affect the current mode."
        )

        if uses_intrahour_lines and not selected_intrahour_leads:
            st.warning("Select at least one intra-hour forecast line.")
            st.stop()

        if uses_revisions and "Latest" not in selected_intrahour_leads:
            selected_intrahour_leads = ["Latest"] + selected_intrahour_leads
            selected_intrahour_leads = list(dict.fromkeys(selected_intrahour_leads))
            st.info("Forecast Revisions mode requires 'Latest', so it was added automatically.")

        show_np4443 = st.checkbox(
            "Include NP4-443 hourly forecast",
            value=True,
            key="solar_show_np4443_v2",
            disabled=not uses_np4443_controls,
            help=None if uses_np4443_controls else "This setting does not affect the current mode."
        )

        error_style = st.selectbox(
            "Error display",
            options=["MW Error", "Absolute Error", "Percent Error"],
            index=0,
            key="solar_error_style_v2",
            disabled=not uses_error_display,
            help=None if uses_error_display else "Only used in Forecast Error mode."
        )

        st.markdown("**Table View Toggles**")
        table_col1, table_col2, table_col3, table_col4 = st.columns(4)

        with table_col1:
            show_actual_cols = st.checkbox(
                "Table: Actual",
                value=True,
                key="solar_table_actual_v2",
                disabled=not uses_table_toggles
            )

        with table_col2:
            show_intrahour_cols = st.checkbox(
                "Table: Intra-hour forecast",
                value=True,
                key="solar_table_intrahour_v2",
                disabled=not uses_table_toggles
            )

        with table_col3:
            show_np4443_cols = st.checkbox(
                "Table: NP4-443 forecast",
                value=True,
                key="solar_table_np4443_v2",
                disabled=not uses_table_toggles
            )

        with table_col4:
            show_error_cols = st.checkbox(
                "Table: Error columns",
                value=True,
                key="solar_table_error_v2",
                disabled=not uses_table_toggles
            )

        if not uses_table_toggles:
            show_actual_cols = True
            show_intrahour_cols = True
            show_np4443_cols = True
            show_error_cols = False

        if hours_back <= 24:
            actual_size = 18000
            intrahour_size = 70000
        elif hours_back <= 72:
            actual_size = 40000
            intrahour_size = 120000
        else:
            actual_size = 80000
            intrahour_size = 250000

        np4443_size = 250000

        intrahour_posted_start = start_ts - pd.Timedelta(hours=3)

        actual_posted_from = start_ts.strftime("%Y-%m-%dT%H:%M")
        actual_posted_to = end_ts.strftime("%Y-%m-%dT%H:%M")

        intrahour_posted_from = intrahour_posted_start.strftime("%Y-%m-%dT%H:%M")
        intrahour_posted_to = end_ts.strftime("%Y-%m-%dT%H:%M")

        # ---------------------------------------------------
        # LOAD DATA
        # ---------------------------------------------------
        actual_endpoint, _ = get_artifact_endpoint(ACTUAL_PRODUCT_URL)
        intrahour_endpoint, _ = get_artifact_endpoint(INTRAHOUR_FORECAST_PRODUCT_URL)
        np4443_endpoint, _ = get_artifact_endpoint(HOURLY_FORECAST_PRODUCT_URL)

        actual_raw = load_report(actual_endpoint, actual_posted_from, actual_posted_to, actual_size)
        intrahour_raw = load_report(intrahour_endpoint, intrahour_posted_from, intrahour_posted_to, intrahour_size)
        np4443_raw = load_report(np4443_endpoint, None, None, np4443_size)

        actual_regional_df, actual_time_col = normalize_actual_regional_df(actual_raw)
        intrahour_long, intrahour_posted_col, intrahour_target_col = normalize_intrahour_forecast_long(intrahour_raw)
        np4443_long = normalize_np4443_hourly(np4443_raw)

        actual_regional_df = align_index(actual_regional_df, start_ts, end_ts)

        intrahour_long = intrahour_long[
            (intrahour_long["target_ts"] >= start_ts) &
            (intrahour_long["target_ts"] <= end_ts)
        ].copy()

        np4443_long = np4443_long[
            (np4443_long["target_ts"] >= start_ts) &
            (np4443_long["target_ts"] <= end_ts)
        ].copy()

        if actual_regional_df.empty:
            st.warning("No actual solar data returned for that period.")
            st.stop()

        if intrahour_long.empty and np4443_long.empty:
            st.warning("No forecast solar data returned for that period.")
            st.stop()

        selected_available_series = [
            s for s in selected_series
            if s in actual_regional_df.columns
            or (not intrahour_long.empty and s in intrahour_long["series"].unique().tolist())
            or (not np4443_long.empty and s in np4443_long["series"].unique().tolist())
            or s == "ERCOT Total"
        ]

        if not selected_available_series:
            st.warning("Selected series were not found in the returned data.")
            st.stop()

        intrahour_curves = {}
        for lead_label in selected_intrahour_leads:
            lead_minutes = intrahour_lead_options[lead_label]
            curve = build_intrahour_lead_curve(intrahour_long, lead_minutes, available_series)
            curve = align_index(curve, start_ts, end_ts)
            intrahour_curves[lead_label] = curve

        np4443_models = []
        np4443_curve = pd.DataFrame()
        np4443_model_selected = "All In-Use Models"

        if show_np4443 and not np4443_long.empty:
            model_values = sorted(np4443_long["model"].dropna().astype(str).unique().tolist())
            np4443_models = ["All In-Use Models"] + model_values

            np4443_model_selected = st.selectbox(
                "NP4-443 model",
                options=np4443_models,
                index=0,
                key="solar_np4443_model_v2",
                disabled=not uses_np4443_controls
            )

            np4443_curve = build_np4443_curve(np4443_long, np4443_model_selected, available_series)
            np4443_curve = align_index(np4443_curve, start_ts, end_ts)

        # ---------------------------------------------------
        # STATUS PANEL
        # ---------------------------------------------------
        st.subheader("Current Forecast Status")

        status_col1, status_col2 = st.columns(2)

        with status_col1:
            active_np4443 = get_active_np4443_model_info(np4443_raw)

            if not active_np4443:
                st.warning("Could not determine NP4-443 model status.")
            elif active_np4443["status"] == "active_flag_found":
                st.info(
                    f"Hourly active model: {active_np4443['model']}"
                    + (
                        f" | Next target: {active_np4443['next_target_ts']:%Y-%m-%d %H:%M}"
                        if active_np4443.get("next_target_ts") is not None else ""
                    )
                )
            elif active_np4443["status"] == "fallback_latest_target":
                st.warning(
                    "No explicit active InUseFlag row detected. "
                    f"Most recent target has model(s): {', '.join(active_np4443['models'])}"
                    + (
                        f" | Latest target: {active_np4443['latest_target_ts']:%Y-%m-%d %H:%M}"
                        if active_np4443.get("latest_target_ts") is not None else ""
                    )
                )
            elif active_np4443["status"] == "fallback_models_only":
                st.warning(
                    "No explicit active InUseFlag row detected. "
                    f"Available NP4-443 model(s): {', '.join(active_np4443['models'][:10])}"
                )
            else:
                st.warning(active_np4443.get("message", "Could not determine NP4-443 model status."))

        with status_col2:
            intrahour_status = get_intrahour_status(
                intrahour_long,
                selected_intrahour_leads,
                intrahour_lead_options,
                now
            )

            if intrahour_status:
                lines = []
                for item in intrahour_status:
                    lines.append(
                        f"{item['lead_label']}: posted {item['posted_ts']:%Y-%m-%d %H:%M}, "
                        f"target {item['target_ts']:%Y-%m-%d %H:%M}, "
                        f"lead {item['lead_minutes']:.0f} min"
                    )
                st.success("Intra-hour forecast currently being plotted:\n\n" + "\n\n".join(lines))
            else:
                st.warning("Could not determine current intra-hour posted forecast status.")

        st.subheader("Solar Trader Graph")
        st.caption("Long-horizon chart includes hourly NP4-443 and intra-hour forecasts when available.")

        palette = px.colors.qualitative.Plotly
        color_map = {series: palette[i % len(palette)] for i, series in enumerate(available_series)}

        fig = go.Figure()

        if view_mode == "Actual vs Forecast":
            for series_name in selected_available_series:
                if series_name in actual_regional_df.columns:
                    fig.add_trace(
                        go.Scatter(
                            x=actual_regional_df.index,
                            y=actual_regional_df[series_name],
                            mode="lines",
                            name=f"{series_name} Actual",
                            line=dict(color=color_map[series_name], dash="solid"),
                            hovertemplate=(
                                f"Series: {series_name} Actual<br>"
                                "Time: %{x}<br>"
                                "MW: %{y:,.0f}<extra></extra>"
                            ),
                        )
                    )

                for lead_label in selected_intrahour_leads:
                    curve = intrahour_curves.get(lead_label, pd.DataFrame())
                    if not curve.empty and series_name in curve.columns:
                        dash_style = {
                            "Latest": "dash",
                            "1 hour ago": "dot",
                            "2 hours ago": "dashdot",
                        }.get(lead_label, "dash")

                        fig.add_trace(
                            go.Scatter(
                                x=curve.index,
                                y=curve[series_name],
                                mode="lines",
                                name=f"{series_name} Intra-hour {lead_label}",
                                line=dict(color=color_map[series_name], dash=dash_style),
                                hovertemplate=(
                                    f"Series: {series_name} Intra-hour {lead_label}<br>"
                                    "Time: %{x}<br>"
                                    "MW: %{y:,.0f}<extra></extra>"
                                ),
                            )
                        )

                if show_np4443 and not np4443_curve.empty and series_name in np4443_curve.columns:
                    fig.add_trace(
                        go.Scatter(
                            x=np4443_curve.index,
                            y=np4443_curve[series_name],
                            mode="lines",
                            name=f"{series_name} NP4-443",
                            line=dict(color=color_map[series_name], dash="longdash"),
                            hovertemplate=(
                                f"Series: {series_name} NP4-443"
                                + (f" ({np4443_model_selected})" if np4443_model_selected else "")
                                + "<br>Time: %{x}<br>MW: %{y:,.0f}<extra></extra>"
                            ),
                        )
                    )

            fig.update_layout(
                xaxis_title="Time",
                yaxis_title="MW",
                hovermode="x unified",
                legend_title_text="Series",
                height=760,
                margin=dict(l=20, r=20, t=20, b=20),
            )

        elif view_mode == "Forecast Error":
            metric_map = {
                "MW Error": "error_mw",
                "Absolute Error": "abs_error_mw",
                "Percent Error": "pct_error",
            }
            y_col = metric_map[error_style]
            y_title = {
                "MW Error": "Forecast - Actual (MW)",
                "Absolute Error": "Absolute Error (MW)",
                "Percent Error": "Forecast Error (%)",
            }[error_style]

            for series_name in selected_available_series:
                if series_name not in actual_regional_df.columns:
                    continue

                for lead_label in selected_intrahour_leads:
                    curve = intrahour_curves.get(lead_label, pd.DataFrame())
                    if curve.empty or series_name not in curve.columns:
                        continue

                    err = make_error_frame(actual_regional_df, curve, series_name)
                    if err.empty:
                        continue

                    fig.add_trace(
                        go.Scatter(
                            x=err.index,
                            y=err[y_col],
                            mode="lines",
                            name=f"{series_name} Intra-hour {lead_label}",
                            line=dict(color=color_map[series_name], dash={
                                "Latest": "dash",
                                "1 hour ago": "dot",
                                "2 hours ago": "dashdot",
                            }.get(lead_label, "dash")),
                            hovertemplate=(
                                f"Series: {series_name} Intra-hour {lead_label}<br>"
                                "Time: %{x}<br>"
                                f"{y_title}: "
                                + ("%{y:,.1f}%" if y_col == "pct_error" else "%{y:,.0f}")
                                + "<extra></extra>"
                            ),
                        )
                    )

                if show_np4443 and not np4443_curve.empty and series_name in np4443_curve.columns:
                    err = make_error_frame(actual_regional_df, np4443_curve, series_name)
                    if not err.empty:
                        fig.add_trace(
                            go.Scatter(
                                x=err.index,
                                y=err[y_col],
                                mode="lines",
                                name=f"{series_name} NP4-443",
                                line=dict(color=color_map[series_name], dash="longdash"),
                                hovertemplate=(
                                    f"Series: {series_name} NP4-443<br>"
                                    "Time: %{x}<br>"
                                    f"{y_title}: "
                                    + ("%{y:,.1f}%" if y_col == "pct_error" else "%{y:,.0f}")
                                    + "<extra></extra>"
                                ),
                            )
                        )

            fig.update_layout(
                xaxis_title="Time",
                yaxis_title=y_title,
                hovermode="x unified",
                legend_title_text="Series",
                height=760,
                margin=dict(l=20, r=20, t=20, b=20),
            )
            if error_style != "Absolute Error":
                fig.add_hline(y=0, line_dash="solid", line_width=1)

        elif view_mode == "Forecast Revisions":
            latest_curve = intrahour_curves.get("Latest", pd.DataFrame())
            older_labels = [x for x in selected_intrahour_leads if x != "Latest"]

            if latest_curve.empty:
                st.warning("Forecast Revisions mode needs 'Latest' selected and available.")
                st.stop()
            if not older_labels:
                st.warning("Forecast Revisions mode needs 'Latest' plus at least one older intra-hour line.")
                st.stop()

            for series_name in selected_available_series:
                if series_name not in latest_curve.columns:
                    continue

                for older_label in older_labels:
                    older_curve = intrahour_curves.get(older_label, pd.DataFrame())
                    if older_curve.empty or series_name not in older_curve.columns:
                        continue

                    combined = pd.concat(
                        [
                            latest_curve[[series_name]].rename(columns={series_name: "latest"}),
                            older_curve[[series_name]].rename(columns={series_name: "older"})
                        ],
                        axis=1
                    ).dropna()

                    if combined.empty:
                        continue

                    combined["revision"] = combined["latest"] - combined["older"]

                    fig.add_trace(
                        go.Scatter(
                            x=combined.index,
                            y=combined["revision"],
                            mode="lines",
                            name=f"{series_name} Revision vs {older_label}",
                            line=dict(color=color_map[series_name], dash={
                                "1 hour ago": "dot",
                                "2 hours ago": "dashdot",
                            }.get(older_label, "dot")),
                            hovertemplate=(
                                f"Series: {series_name} Revision vs {older_label}<br>"
                                "Time: %{x}<br>"
                                "Latest - Older: %{y:,.0f} MW<extra></extra>"
                            ),
                        )
                    )

            fig.update_layout(
                xaxis_title="Time",
                yaxis_title="Latest Forecast - Older Forecast (MW)",
                hovermode="x unified",
                legend_title_text="Series",
                height=760,
                margin=dict(l=20, r=20, t=20, b=20),
            )
            fig.add_hline(y=0, line_dash="solid", line_width=1)

        elif view_mode == "Error Summary":
            summary_rows = []

            for series_name in selected_available_series:
                if series_name in actual_regional_df.columns:
                    for lead_label in selected_intrahour_leads:
                        curve = intrahour_curves.get(lead_label, pd.DataFrame())
                        if curve.empty or series_name not in curve.columns:
                            continue
                        err = make_error_frame(actual_regional_df, curve, series_name)
                        row = summarize_error_metrics(err, f"Intra-hour {lead_label}", series_name)
                        if row:
                            summary_rows.append(row)

                    if show_np4443 and not np4443_curve.empty and series_name in np4443_curve.columns:
                        err = make_error_frame(actual_regional_df, np4443_curve, series_name)
                        row = summarize_error_metrics(err, "NP4-443", series_name)
                        if row:
                            summary_rows.append(row)

            summary_df = pd.DataFrame(summary_rows)
            if summary_df.empty:
                st.warning("No overlapping forecast vs actual data available for summary metrics.")
            else:
                st.dataframe(summary_df, use_container_width=True, hide_index=True)

        if view_mode in ["Actual vs Forecast", "Forecast Error", "Forecast Revisions"]:
            st.plotly_chart(fig, use_container_width=True, key="solar_trader_chart_v2")

        # ---------------------------------------------------
        # TABLE VIEW / EXPORT
        # ---------------------------------------------------
        table_df = pd.DataFrame(index=actual_regional_df.index)

        if show_actual_cols:
            for series_name in selected_available_series:
                if series_name in actual_regional_df.columns:
                    table_df[f"{series_name} Actual"] = actual_regional_df[series_name]

        if show_intrahour_cols:
            for series_name in selected_available_series:
                for lead_label in selected_intrahour_leads:
                    curve = intrahour_curves.get(lead_label, pd.DataFrame())
                    if not curve.empty and series_name in curve.columns:
                        table_df[f"{series_name} Intra-hour {lead_label}"] = curve[series_name]

        if show_np4443_cols and show_np4443 and not np4443_curve.empty:
            for series_name in selected_available_series:
                if series_name in np4443_curve.columns:
                    table_df[f"{series_name} NP4-443"] = np4443_curve[series_name]

        if show_error_cols:
            for series_name in selected_available_series:
                if series_name in actual_regional_df.columns:
                    latest_curve = intrahour_curves.get("Latest", pd.DataFrame())
                    if not latest_curve.empty and series_name in latest_curve.columns:
                        err = make_error_frame(actual_regional_df, latest_curve, series_name)
                        if not err.empty:
                            table_df[f"{series_name} Latest Error MW"] = err["error_mw"]
                            table_df[f"{series_name} Latest Abs Error MW"] = err["abs_error_mw"]
                            table_df[f"{series_name} Latest Error %"] = err["pct_error"]

                    if show_np4443 and not np4443_curve.empty and series_name in np4443_curve.columns:
                        err = make_error_frame(actual_regional_df, np4443_curve, series_name)
                        if not err.empty:
                            table_df[f"{series_name} NP4-443 Error MW"] = err["error_mw"]
                            table_df[f"{series_name} NP4-443 Abs Error MW"] = err["abs_error_mw"]
                            table_df[f"{series_name} NP4-443 Error %"] = err["pct_error"]

        if view_mode == "Table View":
            st.subheader("Solar Table View")
            st.dataframe(table_df.reset_index(), use_container_width=True, hide_index=True)

        csv = table_df.reset_index().to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download graphed/table data as CSV",
            data=csv,
            file_name="ercot_solar_trader_view_v2.csv",
            mime="text/csv",
            key="solar_trader_download_v2"
        )

        with st.expander("Show graphed/table data"):
            st.dataframe(table_df.reset_index(), use_container_width=True, hide_index=True)

        with st.expander("Debug info"):
            st.write(f"Actual endpoint: {actual_endpoint}")
            st.write(f"Intra-hour endpoint: {intrahour_endpoint}")
            st.write(f"NP4-443 endpoint: {np4443_endpoint}")
            st.write(f"Actual rows returned: {len(actual_raw):,}")
            st.write(f"Intra-hour rows returned: {len(intrahour_raw):,}")
            st.write(f"NP4-443 rows returned: {len(np4443_raw):,}")
            st.write(f"Actual timestamp column: {actual_time_col}")
            st.write(f"Intra-hour posted column: {intrahour_posted_col}")
            st.write(f"Intra-hour target column: {intrahour_target_col}")
            st.write("NP4-443 raw columns:")
            st.write(list(np4443_raw.columns))
            if not np4443_long.empty:
                st.write("NP4-443 models:")
                st.write(sorted(np4443_long["model"].dropna().astype(str).unique().tolist()))

    except Exception as e:
        st.error(str(e))
# -----------------------------
# TAB 4 - NET LOAD DATA
# -----------------------------
elif page == "Load Forecast View":
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