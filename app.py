import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from io import BytesIO
import hashlib
import hmac
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader
import os

# Load Unicode fonts stored alongside app.py (same folder in GitHub).
# BASE_DIR avoids depending on Streamlit Cloud's current working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_REGULAR = "Helvetica"
PDF_BOLD = "Helvetica-Bold"
REGULAR_FONT_PATH = os.path.join(BASE_DIR, "DejaVuSans.ttf")
BOLD_FONT_PATH = os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")

if os.path.isfile(REGULAR_FONT_PATH) and os.path.isfile(BOLD_FONT_PATH):
    try:
        pdfmetrics.registerFont(TTFont("DashboardUnicode", REGULAR_FONT_PATH))
        pdfmetrics.registerFont(TTFont("DashboardUnicode-Bold", BOLD_FONT_PATH))
        PDF_REGULAR = "DashboardUnicode"
        PDF_BOLD = "DashboardUnicode-Bold"

    except Exception as exc:
        # Keep the app running, but make a missing/invalid font visible in logs.
        print(f"Không thể nạp font Unicode cho PDF: {exc}")
else:
    print(
        "CẢNH BÁO: Không tìm thấy DejaVuSans.ttf và/hoặc "
        "DejaVuSans-Bold.ttf cùng thư mục app.py. PDF có thể lỗi font tiếng Việt."
    )

# ============================================================
# INTERNATIONAL CABLE NETWORK DASHBOARD
# Deploy: GitHub + Streamlit Community Cloud
# ============================================================

st.set_page_config(
    page_title="International Cable Network Dashboard",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)

SYSTEMS = ["SJC2", "APG", "AAG", "AAE-1", "VSTN", "CSC"]
SYSTEM_TYPE = {
    "SJC2": "Cáp biển",
    "APG": "Cáp biển",
    "AAG": "Cáp biển",
    "AAE-1": "Cáp biển",
    "VSTN": "Cáp đất",
    "CSC": "Cáp đất",
}
DIRECTIONS = ["HKG", "SNG", "JPN", "US", "EU"]

# True means the route exists (shown as x in the supplied table).
# False means there is no connection (shown as 0 in the supplied table).
ROUTE_EXISTS = {
    "SJC2":  {"HKG": True,  "SNG": True,  "JPN": False, "US": False, "EU": False},
    "APG":   {"HKG": True,  "SNG": True,  "JPN": True,  "US": True,  "EU": False},
    "AAG":   {"HKG": True,  "SNG": False, "JPN": False, "US": False, "EU": False},
    "AAE-1": {"HKG": True,  "SNG": True,  "JPN": False, "US": False, "EU": True},
    "VSTN":  {"HKG": False, "SNG": True,  "JPN": False, "US": False, "EU": False},
    "CSC":   {"HKG": True,  "SNG": False, "JPN": False, "US": False, "EU": False},
}
VALID_ROUTES = [(s, d) for s in SYSTEMS for d in DIRECTIONS if ROUTE_EXISTS[s][d]]

# ---------------- Login ----------------
def check_login(username: str, password: str) -> bool:
    # Demo credentials requested by the user.
    try:
        auth = st.secrets["auth"]
        expected_user = str(auth["username"])
        expected_password = str(auth["password"])
    except Exception:
        return False
    return hmac.compare_digest(username.strip(), expected_user) and hmac.compare_digest(password, expected_password)

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "last_updated" not in st.session_state:
    st.session_state.last_updated = datetime.now()
if "capacities" not in st.session_state:
    st.session_state.capacities = {f"{s}|{d}": 0.0 for s, d in VALID_ROUTES}
if "system_status" not in st.session_state:
    st.session_state.system_status = {s: "Bình thường" for s in SYSTEMS}
if "affected_directions" not in st.session_state:
    st.session_state.affected_directions = {s: [] for s in SYSTEMS}

def login_page():
    st.markdown("""
    <style>
    .login-wrap {max-width:390px; margin:2vh auto .8rem; padding:1.1rem 1.3rem;
        background:#ffffff; border:1px solid #dce5ef; border-radius:16px;
        box-shadow:0 8px 24px rgba(20,48,80,.09); text-align:center;}
    .login-title {font-size:1.45rem; font-weight:800; color:#16324f;}
    .login-sub {color:#64748b; margin-top:.25rem; margin-bottom:.2rem; font-size:.9rem;}
    </style>
    """, unsafe_allow_html=True)
    left, center, right = st.columns([1, 1.15, 1])
    with center:
        st.markdown("""
        <div class="login-wrap">
          <div class="login-title">🌐 International Cable Network</div>
          <div class="login-sub">Dashboard giám sát mạng cáp quốc tế</div>
        </div>
        """, unsafe_allow_html=True)
        with st.form("login_form"):
            st.subheader("Đăng nhập hệ thống")
            username = st.text_input("Tên đăng nhập", placeholder="Nhập username")
            password = st.text_input("Mật khẩu", type="password", placeholder="Nhập password")
            submitted = st.form_submit_button("Đăng nhập", type="primary", use_container_width=True)
            if submitted:
                if check_login(username.strip(), password):
                    st.session_state.authenticated = True
                    st.session_state.login_error = False
                    st.rerun()
                else:
                    st.error("Tên đăng nhập hoặc mật khẩu không đúng.")

if not st.session_state.authenticated:
    login_page()
    st.stop()

# ---------------- Styling ----------------
st.markdown("""
<style>
.block-container {padding-top:3.2rem; padding-bottom:2rem; max-width:1500px;}
.main-title {font-size:1.85rem; font-weight:850; color:#16324f; margin-bottom:0;}
.sub-title {color:#64748b; margin-top:.2rem; margin-bottom:1rem;}
.status-card {padding:14px 16px; border-radius:14px; color:white; min-height:112px;
    box-shadow:0 5px 16px rgba(15,23,42,.10); margin-bottom:8px;}
.status-ok {background:linear-gradient(135deg,#16803c,#22a653);}
.status-bad {background:linear-gradient(135deg,#b91c1c,#ef4444);}
.status-name {font-size:1.15rem; font-weight:800;}
.status-type {font-size:.82rem; opacity:.92;}
.status-state {font-size:1rem; font-weight:700; margin-top:10px;}
.small-muted {color:#64748b; font-size:.85rem;}
.metric-box {background:#f3f7fb; border:1px solid #e0eaf4; border-radius:12px; padding:12px 15px;}
</style>
""", unsafe_allow_html=True)

# ---------------- Data helpers ----------------
def get_raw_df():
    rows = []
    for system in SYSTEMS:
        for direction in DIRECTIONS:
            exists = ROUTE_EXISTS[system][direction]
            value = float(st.session_state.capacities.get(f"{system}|{direction}", 0.0)) if exists else 0.0
            rows.append({
                "Hệ thống": system,
                "Loại cáp": SYSTEM_TYPE[system],
                "Hướng": direction,
                "Có kết nối": exists,
                "Dung lượng nhập (GB)": value,
            })
    return pd.DataFrame(rows)

def get_effective_df():
    df = get_raw_df()
    effective = []
    for _, row in df.iterrows():
        s, d = row["Hệ thống"], row["Hướng"]
        failed = st.session_state.system_status[s] == "Nghiêm trọng"
        affected = d in st.session_state.affected_directions.get(s, [])
        excluded = bool(failed and affected and row["Có kết nối"])
        effective.append(0.0 if excluded else float(row["Dung lượng nhập (GB)"]))
    df["Dung lượng đang khai thác (GB)"] = effective
    df["Bị loại do sự cố"] = [
        bool(st.session_state.system_status[r["Hệ thống"]] == "Nghiêm trọng"
             and r["Hướng"] in st.session_state.affected_directions.get(r["Hệ thống"], [])
             and r["Có kết nối"])
        for _, r in df.iterrows()
    ]
    return df

def mark_updated():
    st.session_state.last_updated = datetime.now()

def build_system_chart(system_totals):
    """Shared Plotly chart used by both dashboard and PDF."""
    fig = px.bar(
        system_totals,
        x="Hệ thống",
        y="Dung lượng đang khai thác (GB)",
        color="Hệ thống",
        text_auto=".2f",
        title="Dung lượng đang khai thác theo hệ thống (GB)",
        hover_data={"Hệ thống": True, "Dung lượng đang khai thác (GB)": ":,.2f"},
        color_discrete_sequence=px.colors.qualitative.Plotly,
    )
    fig.update_layout(
        template="plotly_white", showlegend=False, height=390,
        margin=dict(l=20, r=20, t=60, b=20),
        font=dict(family="Arial, sans-serif", size=11),
        title=dict(x=0.5, xanchor="center", font=dict(size=15)),
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_yaxes(gridcolor="#E5E7EB", zerolinecolor="#CBD5E1")
    return fig


def build_direction_chart(df_effective):
    """Shared 100% stacked bar chart used by both dashboard and PDF."""
    pivot = df_effective.pivot_table(
        index="Hướng", columns="Hệ thống", values="Dung lượng đang khai thác (GB)",
        aggfunc="sum", fill_value=0,
    ).reindex(DIRECTIONS, fill_value=0)
    direction_total = pivot.sum(axis=1)
    share = pivot.div(direction_total.replace(0, np.nan), axis=0).fillna(0) * 100
    share_long = share.reindex(columns=SYSTEMS, fill_value=0).reset_index().melt(
        id_vars="Hướng", var_name="Hệ thống", value_name="Tỷ trọng (%)"
    )
    share_long["Nhãn"] = share_long["Tỷ trọng (%)"].map(lambda x: f"{x:.1f}%" if x >= 4 else "")
    fig = px.bar(
        share_long, x="Hướng", y="Tỷ trọng (%)", color="Hệ thống", barmode="stack",
        text="Nhãn",
        title="Tỷ trọng từng hệ thống trong tổng dung lượng của mỗi hướng kết nối",
        hover_data={"Tỷ trọng (%)": ":.2f", "Nhãn": False},
        category_orders={"Hướng": DIRECTIONS, "Hệ thống": SYSTEMS},
        color_discrete_sequence=px.colors.qualitative.Plotly,
    )
    fig.update_layout(
        template="plotly_white", height=410, yaxis_range=[0, 100],
        margin=dict(l=20, r=20, t=60, b=20),
        font=dict(family="Arial, sans-serif", size=11),
        title=dict(x=0.5, xanchor="center", font=dict(size=15)),
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="center", x=0.5),
    )
    fig.update_traces(textposition="inside")
    fig.update_yaxes(gridcolor="#E5E7EB", ticksuffix="%", zerolinecolor="#CBD5E1")
    return fig, share, direction_total


def build_network_pie(system_totals, total_effective):
    """Shared whole-network doughnut chart used by both dashboard and PDF."""
    overall = system_totals.copy()
    overall["Tỷ trọng toàn mạng (%)"] = np.where(
        total_effective > 0,
        overall["Dung lượng đang khai thác (GB)"] / total_effective * 100,
        0,
    )
    if total_effective > 0:
        fig = px.pie(
            overall, names="Hệ thống", values="Dung lượng đang khai thác (GB)",
            hole=0.52, title="Cơ cấu dung lượng đang khai thác toàn mạng",
            color="Hệ thống", category_orders={"Hệ thống": SYSTEMS},
            color_discrete_sequence=px.colors.qualitative.Plotly,
        )
        fig.update_traces(textposition="inside", textinfo="label+percent", sort=False)
    else:
        fig = go.Figure()
        fig.add_annotation(text="Chưa có dung lượng lớn hơn 0 GB", x=.5, y=.5,
                           showarrow=False, font=dict(size=14))
        fig.update_layout(title="Cơ cấu dung lượng đang khai thác toàn mạng")
    fig.update_layout(
        template="plotly_white", height=390, margin=dict(l=10, r=10, t=60, b=10),
        font=dict(family="Arial, sans-serif", size=11),
        title=dict(x=0.5, xanchor="center", font=dict(size=15)),
        legend=dict(orientation="h", yanchor="top", y=-0.02, xanchor="center", x=0.5),
    )
    return fig, overall


def build_pdf(df_effective, df_raw):
    """Export a balanced, single-page A4 landscape PDF using the same Plotly charts as the app."""
    buffer = BytesIO()
    page_w, page_h = landscape(A4)
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h), pageCompression=1)
    navy = colors.HexColor("#16324F")
    pale = colors.HexColor("#EFF6FF")
    green = colors.HexColor("#16A34A")
    red = colors.HexColor("#DC2626")
    gray = colors.HexColor("#64748B")
    ink = colors.HexColor("#1F2937")
    margin = 25
    content_w = page_w - 2 * margin

    total_effective = float(df_effective["Dung lượng đang khai thác (GB)"].sum())
    bad_count = sum(st.session_state.system_status[s] == "Nghiêm trọng" for s in SYSTEMS)
    system_totals = df_effective.groupby("Hệ thống")["Dung lượng đang khai thác (GB)"].sum().reindex(SYSTEMS, fill_value=0).reset_index()
system_totals.columns = ["Hệ thống", "Dung lượng đang khai thác (GB)"]
with tab1:
    fig = build_system_chart(system_totals)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(system_totals, use_container_width=True, hide_index=True)

with tab2:
    fig, share, direction_total = build_direction_chart(df_effective)
    st.plotly_chart(fig, use_container_width=True)
    show_share = share.copy().round(2)
    show_share["Tổng hướng (GB)"] = direction_total
    st.dataframe(show_share, use_container_width=True)

with tab3:
    fig, overall = build_network_pie(system_totals, total_effective)
    col_a, col_b = st.columns([1.2, 1])
    with col_a:
        st.plotly_chart(fig, use_container_width=True)
    with col_b:
        st.markdown("### Tỷ trọng toàn mạng")
        st.dataframe(overall[["Hệ thống", "Dung lượng đang khai thác (GB)", "Tỷ trọng toàn mạng (%)"]].round(2),
                     use_container_width=True, hide_index=True)
        st.markdown("### Tổng theo hướng")
        by_direction = df_effective.groupby("Hướng")["Dung lượng đang khai thác (GB)"].sum().reindex(DIRECTIONS, fill_value=0).reset_index()
        by_direction["Tỷ trọng toàn mạng (%)"] = np.where(
            total_effective > 0,
            by_direction["Dung lượng đang khai thác (GB)"] / total_effective * 100,
            0,
        )
        st.dataframe(by_direction.round(2), use_container_width=True, hide_index=True)

st.markdown("## Xuất báo cáo")
st.write("Báo cáo PDF một trang A4 ngang; biểu đồ dùng chung cấu hình với dashboard để bảo đảm cách hiển thị nhất quán.")
pdf_bytes = build_pdf(get_effective_df(), get_raw_df())
st.download_button(
    "Tạo báo cáo PDF",
    data=pdf_bytes,
    file_name=f"Bao_cao_mang_cap_quoc_te_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
    mime="application/pdf",
    type="primary",
    use_container_width=False,
)

st.divider()
st.caption("International Cable Network Dashboard • Đơn vị dung lượng: GB • Dữ liệu chỉ được cập nhật khi người vận hành lưu thay đổi.")
