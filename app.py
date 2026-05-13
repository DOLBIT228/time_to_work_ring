import requests
import pandas as pd
import time
import streamlit as st
import plotly.express as px

# =========================================================
# AUTH
# =========================================================

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:

    st.title("🔐 Авторизація")

    login = st.text_input("Логін")

    password = st.text_input(
        "Пароль",
        type="password"
    )

    if st.button("Увійти"):

        if (
            login == st.secrets["APP_LOGIN"]
            and
            password == st.secrets["APP_PASSWORD"]
        ):

            st.session_state.authenticated = True

            st.rerun()

        else:

            st.error(
                "Невірний логін або пароль"
            )

    st.stop()

from datetime import (
    datetime,
    timedelta,
    time as dt_time
)

# =========================================================
# CONFIG
# =========================================================

WEBHOOK_URL = st.secrets["WEBHOOK_URL"]

FUNNEL_ID = st.secrets["FUNNEL_ID"]

TARGET_STAGE = st.secrets["TARGET_STAGE"]

MANAGERS = dict(
    st.secrets["MANAGERS"]
)

WORK_START = dt_time(10, 15)
WORK_END = dt_time(19, 0)

API_DELAY = 0.35

TAKEN_BY_FIELD = "UF_CRM_1778665985"

# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="Bitrix SLA Dashboard",
    layout="wide"
)

st.title("📊 Bitrix24 SLA Dashboard")

st.caption(
    "Аналіз часу взяття угод у роботу "
    "в рамках робочих годин"
)

# =========================================================
# SLA LOGIC
# =========================================================

def calculate_working_minutes(start_dt, end_dt):

    if start_dt >= end_dt:
        return 1

    total_minutes = 0

    current_day = start_dt.date()

    while current_day <= end_dt.date():

        day_start = datetime.combine(
            current_day,
            WORK_START
        )

        day_end = datetime.combine(
            current_day,
            WORK_END
        )

        actual_start = max(
            start_dt.replace(tzinfo=None),
            day_start
        )

        actual_end = min(
            end_dt.replace(tzinfo=None),
            day_end
        )

        if actual_start < actual_end:

            delta = actual_end - actual_start

            total_minutes += (
                delta.total_seconds() / 60
            )

        current_day += timedelta(days=1)

    return max(1, round(total_minutes))


# =========================================================
# HELPERS
# =========================================================

def minutes_to_human(minutes):

    if pd.isna(minutes):
        return "NOT TAKEN"

    minutes = int(minutes)

    hours = minutes // 60
    mins = minutes % 60

    if hours == 0:
        return f"{mins} хв"

    return f"{hours}г {mins}хв"


def parse_bitrix_datetime(date_string):

    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S"
    ]

    for fmt in formats:

        try:
            return datetime.strptime(
                date_string,
                fmt
            )

        except:
            pass

    raise Exception(
        f"Не вдалося розпарсити дату: "
        f"{date_string}"
    )

# =========================================================
# API
# =========================================================

def bitrix_request(method, data=None):

    url = f"{WEBHOOK_URL}{method}.json"

    response = requests.post(
        url,
        json=data,
        timeout=30
    )

    result = response.json()

    # =========================================
    # API LIMIT RETRY
    # =========================================

    if result.get("error") == "QUERY_LIMIT_EXCEEDED":

        time.sleep(2)

        response = requests.post(
            url,
            json=data,
            timeout=30
        )

        result = response.json()

    if "error" in result:

        raise Exception(
            f"BITRIX ERROR: {result}"
        )

    return result

# =========================================================
# DEALS
# =========================================================

def get_all_deals(date_from, date_to):

    all_deals = []

    start = 0

    progress = st.progress(0)

    info = st.empty()

    while True:

        payload = {

            "filter": {

                "CATEGORY_ID": FUNNEL_ID,

                ">=DATE_CREATE":
                    f"{date_from}T00:00:00",

                "<=DATE_CREATE":
                    f"{date_to}T23:59:59"
            },

            "select": [
                "ID",
                "TITLE",
                "DATE_CREATE",
                "ASSIGNED_BY_ID",
                "CATEGORY_ID",
                TAKEN_BY_FIELD
            ],

            "start": start
        }

        result = bitrix_request(
            "crm.deal.list",
            payload
        )

        deals = result.get(
            "result",
            []
        )

        if not deals:
            break

        all_deals.extend(deals)

        info.info(
            f"Завантажено угод: "
            f"{len(all_deals)}"
        )

        if "next" not in result:
            break

        start = result["next"]

        time.sleep(API_DELAY)

    progress.progress(100)

    return all_deals

# =========================================================
# HISTORY
# =========================================================

def get_stage_history(deal_id):

    payload = {

        "entityTypeId": 2,

        "filter": {
            "OWNER_ID": deal_id
        }
    }

    result = bitrix_request(
        "crm.stagehistory.list",
        payload
    )

    items = (
        result.get("result", {})
        .get("items", [])
    )

    time.sleep(API_DELAY)

    return items


def find_taken_in_work_datetime(stage_history):

    for item in stage_history:

        stage_id = (
            item.get("STAGE_ID")
            or item.get("STAGE_ID_TO")
        )

        if stage_id == TARGET_STAGE:

            created_time = (
                item.get("CREATED_TIME")
                or item.get("MODIFIED_TIME")
                or item.get("DATE_CREATE")
            )

            if created_time:
                return created_time

    return None

# =========================================================
# ANALYSIS
# =========================================================

@st.cache_data(show_spinner=False)

def run_analysis(date_from, date_to):

    deals = get_all_deals(
        date_from,
        date_to
    )

    rows = []

    for index, deal in enumerate(deals):

        try:

            deal_id = deal["ID"]

            # =================================
            # REAL SLA OWNER
            # =================================

            taken_by_id = str(
                deal.get(
                    TAKEN_BY_FIELD,
                    ""
                )
            ).strip()

            # Якщо automation field валідне
            if (
                taken_by_id
                and
                taken_by_id in MANAGERS
            ):

                manager_id = taken_by_id

            # fallback на відповідального
            else:

                manager_id = str(
                    deal.get(
                        "ASSIGNED_BY_ID",
                        ""
                    )
                ).strip()

            manager = MANAGERS.get(
                manager_id,
                f"USER ID {manager_id}"
            )

            created_dt = parse_bitrix_datetime(
                deal["DATE_CREATE"]
            )

            history = get_stage_history(
                deal_id
            )

            taken_raw = (
                find_taken_in_work_datetime(
                    history
                )
            )

            # =================================
            # NOT TAKEN
            # =================================

            if not taken_raw:

                rows.append({

                    "Deal ID":
                        deal_id,

                    "Title":
                        deal.get(
                            "TITLE",
                            ""
                        ),

                    "Manager":
                        manager,

                    "Created":
                        created_dt,

                    "Taken In Work":
                        None,

                    "SLA Minutes":
                        None,

                    "SLA Human":
                        "NOT TAKEN"
                })

                continue

            # =================================
            # SLA
            # =================================

            taken_dt = parse_bitrix_datetime(
                taken_raw
            )

            sla_minutes = (
                calculate_working_minutes(
                    created_dt,
                    taken_dt
                )
            )

            rows.append({

                "Deal ID":
                    deal_id,

                "Title":
                    deal.get(
                        "TITLE",
                        ""
                    ),

                "Manager":
                    manager,

                "Created":
                    created_dt,

                "Taken In Work":
                    taken_dt,

                "SLA Minutes":
                    sla_minutes,

                "SLA Human":
                    minutes_to_human(
                        sla_minutes
                    )
            })

        except:
            pass

    return pd.DataFrame(rows)

# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.header("Фільтри")

col1, col2 = st.sidebar.columns(2)

with col1:

    date_from = st.date_input(
        "Від",
        value=datetime.now().date()
    )

with col2:

    date_to = st.date_input(
        "До",
        value=datetime.now().date()
    )

run = st.sidebar.button(
    "Запустити аналіз",
    use_container_width=True
)

# =========================================================
# RUN
# =========================================================

if run:

    with st.spinner(
        "Аналізуємо SLA..."
    ):

        df = run_analysis(
            str(date_from),
            str(date_to)
        )

        st.session_state["df"] = df

if "df" in st.session_state:

    df = st.session_state["df"]

    valid_df = df[
        df["SLA Minutes"].notna()
    ]

    # =====================================================
    # TABS
    # =====================================================

    tab1, tab2, tab3 = st.tabs([

        "📈 Загальна ситуація",

        "👨‍💼 Менеджери",

        "📋 Угоди"
    ])

    # =====================================================
    # TAB 1
    # =====================================================

    with tab1:

        st.subheader(
            "Загальна ситуація"
        )

        total_deals = len(df)

        processed = len(valid_df)

        not_taken = len(
            df[
                df["SLA Minutes"].isna()
            ]
        )

        avg_sla = round(
            valid_df["SLA Minutes"].mean(),
            2
        )

        median_sla = round(
            valid_df["SLA Minutes"].median(),
            2
        )

        fast_15 = round(
            (
                len(
                    valid_df[
                        valid_df[
                            "SLA Minutes"
                        ] <= 15
                    ]
                )
                / len(valid_df)
            ) * 100,
            2
        )

        fast_30 = round(
            (
                len(
                    valid_df[
                        valid_df[
                            "SLA Minutes"
                        ] <= 30
                    ]
                )
                / len(valid_df)
            ) * 100,
            2
        )

        col1, col2, col3, col4 = st.columns(4)

        col1.metric(
            "Угод",
            total_deals
        )

        col2.metric(
            "В роботі",
            processed
        )

        col3.metric(
            "NOT TAKEN",
            not_taken
        )

        col4.metric(
            "Average SLA",
            minutes_to_human(avg_sla)
        )

        st.divider()

        col5, col6, col7, col8 = st.columns(4)

        col5.metric(
            "Median SLA",
            minutes_to_human(median_sla)
        )

        col6.metric(
            "До 15 хв",
            f"{fast_15}%"
        )

        col7.metric(
            "До 30 хв",
            f"{fast_30}%"
        )

        col8.metric(
            "Max SLA",
            minutes_to_human(
                valid_df[
                    "SLA Minutes"
                ].max()
            )
        )

        st.divider()

        fig = px.histogram(
            valid_df,
            x="SLA Minutes",
            nbins=20,
            title="Розподіл SLA"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    # =====================================================
    # TAB 2
    # =====================================================

    with tab2:

        st.subheader(
            "Деталізація по менеджерах"
        )

        manager_df = (

            valid_df

            .groupby("Manager")

            .agg({

                "Deal ID":
                    "count",

                "SLA Minutes": [
                    "mean",
                    "median",
                    "max"
                ]
            })

            .reset_index()
        )

        manager_df.columns = [

            "Manager",

            "Deals",

            "Average SLA",

            "Median SLA",

            "Max SLA"
        ]

        manager_df[
            "Average SLA Human"
        ] = (

            manager_df[
                "Average SLA"
            ]

            .apply(
                minutes_to_human
            )
        )

        manager_df[
            "Median SLA Human"
        ] = (

            manager_df[
                "Median SLA"
            ]

            .apply(
                minutes_to_human
            )
        )

        manager_df[
            "Max SLA Human"
        ] = (

            manager_df[
                "Max SLA"
            ]

            .apply(
                minutes_to_human
            )
        )

        st.dataframe(
            manager_df,
            use_container_width=True
        )

        st.divider()

        fig = px.bar(

            manager_df,

            x="Manager",

            y="Average SLA",

            title="Average SLA по менеджерах"
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    # =====================================================
    # TAB 3
    # =====================================================

    with tab3:

        st.subheader(
            "Деталізація по угодах"
        )

        sort_option = st.selectbox(

            "Сортування",

            [
                "Найдовші SLA",
                "Найшвидші SLA"
            ]
        )

        deals_df = df.copy()

        if sort_option == "Найдовші SLA":

            deals_df = deals_df.sort_values(

                by="SLA Minutes",

                ascending=False,

                na_position="last"
            )

        else:

            deals_df = deals_df.sort_values(

                by="SLA Minutes",

                ascending=True,

                na_position="last"
            )

        st.dataframe(

            deals_df,

            use_container_width=True,

            height=700
        )

        st.divider()

        long_sla = deals_df[
            deals_df["SLA Minutes"] >= 60
        ]

        st.subheader(
            "🔥 Довгі SLA (1+ година)"
        )

        st.dataframe(
            long_sla,
            use_container_width=True
        )

        csv = deals_df.to_csv(
            index=False
        )

        st.download_button(

            label="📥 Завантажити CSV",

            data=csv,

            file_name="sla_report.csv",

            mime="text/csv"
        )
