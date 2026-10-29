import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from io import BytesIO
import hashlib
import hmac
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader
import os
import copy

# Register a Unicode font when available so Vietnamese text renders correctly in PDF.
PDF_REGULAR = "Helvetica"
PDF_BOLD = "Helvetica-Bold"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
for regular_path, bold_path in [
    (os.path.join(BASE_DIR, "DejaVuSans.ttf"), os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")),
    (os.path.join(BASE_DIR, "fonts", "DejaVuSans.ttf"), os.path.join(BASE_DIR, "fonts", "DejaVuSans-Bold.ttf")),
    ("fonts/NotoSans-Regular.ttf", "fonts/NotoSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"),
    (r"C:\\Windows\\Fonts\\arial.ttf", r"C:\\Windows\\Fonts\\arialbd.ttf"),
]:
    if os.path.exists(regular_path) and os.path.exists(bold_path):
        try:
            pdfmetrics.registerFont(TTFont("DashboardUnicode", regular_path))
            pdfmetrics.registerFont(TTFont("DashboardUnicode-Bold", bold_path))
            PDF_REGULAR = "DashboardUnicode"
            PDF_BOLD = "DashboardUnicode-Bold"
            break
        except Exception:
            pass

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
    .login-wrap {max-width:520px; margin:5vh auto; padding:2rem 2.2rem;
        background:#ffffff; border:1px solid #dce5ef; border-radius:18px;
        box-shadow:0 12px 34px rgba(20,48,80,.10);}
    .login-title {font-size:1.8rem; font-weight:800; color:#16324f;}
    .login-sub {color:#64748b; margin-bottom:1.2rem;}
    </style>
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
    st.caption("Tài khoản demo theo yêu cầu: admin / admin123")
    st.warning("Trước khi đưa vào sử dụng thực tế, hãy thay thông tin đăng nhập mẫu bằng cơ chế xác thực an toàn qua st.secrets hoặc hệ thống tài khoản riêng.")

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
    fig = px.bar(system_totals, x="Hệ thống", y="Dung lượng đang khai thác (GB)",
        color="Hệ thống", text_auto=".2f", title="Dung lượng đang khai thác theo hệ thống (GB)",
        hover_data={"Hệ thống": True, "Dung lượng đang khai thác (GB)": ":,.2f"},
        color_discrete_sequence=px.colors.qualitative.Plotly,
        category_orders={"Hệ thống": SYSTEMS})
    fig.update_layout(template="plotly_white", showlegend=False, height=390,
        margin=dict(l=20,r=20,t=60,b=20), font=dict(family="Arial, sans-serif",size=11),
        title=dict(x=.5,xanchor="center",font=dict(size=15)))
    fig.update_traces(textposition="outside",cliponaxis=False)
    fig.update_yaxes(gridcolor="#E5E7EB",zerolinecolor="#CBD5E1")
    return fig


def build_direction_chart(df_effective):
    pivot=df_effective.pivot_table(index="Hướng",columns="Hệ thống",values="Dung lượng đang khai thác (GB)",aggfunc="sum",fill_value=0).reindex(DIRECTIONS,fill_value=0)
    totals=pivot.sum(axis=1)
    share=pivot.div(totals.replace(0,np.nan),axis=0).fillna(0)*100
    long=share.reindex(columns=SYSTEMS,fill_value=0).reset_index().melt(id_vars="Hướng",var_name="Hệ thống",value_name="Tỷ trọng (%)")
    long["Nhãn"]=long["Tỷ trọng (%)"].map(lambda x:f"{x:.1f}%" if x>=4 else "")
    fig=px.bar(long,x="Hướng",y="Tỷ trọng (%)",color="Hệ thống",barmode="stack",text="Nhãn",
        title="Tỷ trọng từng hệ thống trong tổng dung lượng của mỗi hướng kết nối",
        hover_data={"Tỷ trọng (%)":":.2f","Nhãn":False},category_orders={"Hướng":DIRECTIONS,"Hệ thống":SYSTEMS},
        color_discrete_sequence=px.colors.qualitative.Plotly)
    fig.update_layout(template="plotly_white",height=410,yaxis_range=[0,100],margin=dict(l=20,r=20,t=60,b=20),
        font=dict(family="Arial, sans-serif",size=11),title=dict(x=.5,xanchor="center",font=dict(size=15)),
        legend=dict(orientation="h",yanchor="top",y=-.18,xanchor="center",x=.5))
    fig.update_traces(textposition="inside")
    fig.update_yaxes(gridcolor="#E5E7EB",ticksuffix="%",zerolinecolor="#CBD5E1")
    return fig,share,totals


def build_network_pie(system_totals,total_effective):
    overall=system_totals.copy()
    overall["Tỷ trọng toàn mạng (%)"]=np.where(total_effective>0,overall["Dung lượng đang khai thác (GB)"]/total_effective*100,0)
    if total_effective>0:
        fig=px.pie(overall,names="Hệ thống",values="Dung lượng đang khai thác (GB)",hole=.52,
            title="Cơ cấu dung lượng đang khai thác toàn mạng",color="Hệ thống",
            category_orders={"Hệ thống":SYSTEMS},color_discrete_sequence=px.colors.qualitative.Plotly)
        fig.update_traces(textposition="inside",textinfo="label+percent",sort=False)
    else:
        fig=go.Figure(); fig.add_annotation(text="Chưa có dung lượng lớn hơn 0 GB",x=.5,y=.5,showarrow=False,font=dict(size=14))
        fig.update_layout(title="Cơ cấu dung lượng đang khai thác toàn mạng")
    fig.update_layout(template="plotly_white",height=390,margin=dict(l=10,r=10,t=60,b=10),
        font=dict(family="Arial, sans-serif",size=11),title=dict(x=.5,xanchor="center",font=dict(size=15)),
        legend=dict(orientation="h",yanchor="top",y=-.02,xanchor="center",x=.5))
    return fig,overall


def build_pdf(df_effective, df_raw):
    """Xuất báo cáo A4 ngang một trang, dùng chung hàm và dữ liệu biểu đồ với dashboard."""
    buffer = BytesIO()
    page_w, page_h = landscape(A4)
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h), pageCompression=1)

    navy = colors.HexColor("#16324F")
    pale = colors.HexColor("#EFF6FF")
    green = colors.HexColor("#16A34A")
    red = colors.HexColor("#DC2626")
    gray = colors.HexColor("#64748B")
    ink = colors.HexColor("#1F2937")
    line = colors.HexColor("#D7E0EA")
    margin = 24
    content_w = page_w - 2 * margin

    total_raw = float(df_raw["Dung lượng nhập (GB)"].sum())
    total_effective = float(df_effective["Dung lượng đang khai thác (GB)"].sum())
    bad_count = sum(st.session_state.system_status[s] == "Nghiêm trọng" for s in SYSTEMS)

    # 1. Header
    header_y = page_h - 58
    c.setFillColor(navy)
    c.roundRect(margin, header_y, content_w, 40, 8, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(PDF_BOLD, 12)
    c.drawCentredString(page_w / 2, header_y + 23, "BÁO CÁO HIỆN TRẠNG HOẠT ĐỘNG MẠNG CÁP QUỐC TẾ")
    c.setFont(PDF_REGULAR, 7)
    c.drawCentredString(page_w / 2, header_y + 9, "Ngày báo cáo: " + datetime.now().strftime("%d/%m/%Y %H:%M"))

    # 2. Six status cards, evenly spaced
    status_top = header_y - 10
    gap = 7
    card_w = (content_w - gap * 5) / 6
    card_y = status_top - 34
    for i, system in enumerate(SYSTEMS):
        x = margin + i * (card_w + gap)
        critical = st.session_state.system_status[system] == "Nghiêm trọng"
        c.setFillColor(red if critical else green)
        c.roundRect(x, card_y, card_w, 29, 5, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(PDF_BOLD, 8.5)
        c.drawCentredString(x + card_w / 2, card_y + 17, system)
        c.setFont(PDF_REGULAR, 6.2)
        c.drawCentredString(x + card_w / 2, card_y + 7, "NGHIÊM TRỌNG" if critical else "BÌNH THƯỜNG")

    # 3. KPI row
    kpi_top = card_y - 9
    kpi_gap = 9
    kpi_w = (content_w - kpi_gap * 2) / 3
    metrics = [
        ("TỔNG DUNG LƯỢNG NHẬP", f"{total_raw:,.2f} GB"),
        ("DUNG LƯỢNG ĐANG KHAI THÁC", f"{total_effective:,.2f} GB"),
        ("HỆ THỐNG NGHIÊM TRỌNG", f"{bad_count}/6"),
    ]
    kpi_y = kpi_top - 35
    for i, (label, value) in enumerate(metrics):
        x = margin + i * (kpi_w + kpi_gap)
        c.setFillColor(pale)
        c.roundRect(x, kpi_y, kpi_w, 29, 5, fill=1, stroke=0)
        c.setFillColor(gray)
        c.setFont(PDF_BOLD, 6.5)
        c.drawString(x + 8, kpi_y + 17, label)
        c.setFillColor(navy)
        c.setFont(PDF_BOLD, 9)
        c.drawRightString(x + kpi_w - 8, kpi_y + 5, value)

    # 4. Charts: use the same builders as the dashboard, changing only export dimensions.
    system_totals = (
        df_effective.groupby("Hệ thống")["Dung lượng đang khai thác (GB)"]
        .sum().reindex(SYSTEMS, fill_value=0).reset_index()
    )
    system_totals.columns = ["Hệ thống", "Dung lượng đang khai thác (GB)"]
    fig_system = build_system_chart(system_totals)
    fig_direction = build_direction_chart(df_effective)[0]
    fig_pie = build_network_pie(system_totals, total_effective)[0]

    # PDF canvas has a fixed, compact landscape aspect ratio. Cloning prevents
    # export-specific styling from changing the figures shown in Streamlit.
    chart_gap = 10
    chart_w = (content_w - chart_gap * 2) / 3
    chart_y = 120
    chart_h = 255
    chart_specs = [
        (fig_system, margin),
        (fig_direction, margin + chart_w + chart_gap),
        (fig_pie, margin + 2 * (chart_w + chart_gap)),
    ]
    for source_fig, x in chart_specs:
        fig_pdf = copy.deepcopy(source_fig)
        fig_pdf.update_layout(
            width=760,
            height=760,
            margin=dict(l=28, r=22, t=72, b=65),
            font=dict(family="Arial, sans-serif", size=10),
            title=dict(font=dict(size=13), x=0.5, xanchor="center"),
        )
        # Keep the stacked-chart legend readable in the narrow PDF panel.
        if source_fig is fig_direction:
            fig_pdf.update_layout(
                legend=dict(orientation="h", yanchor="top", y=-0.2,
                            xanchor="center", x=0.5, font=dict(size=8)),
                margin=dict(l=34, r=18, t=76, b=100),
            )
        image_bytes = BytesIO(fig_pdf.to_image(format="png", width=760, height=760, scale=1))
        c.drawImage(
            ImageReader(image_bytes), x, chart_y, width=chart_w, height=chart_h,
            preserveAspectRatio=True, anchor="c", mask="auto"
        )

    # 5. Balanced footer: totals by direction and note
    c.setStrokeColor(line)
    c.line(margin, 105, page_w - margin, 105)
    c.setFillColor(ink)
    c.setFont(PDF_BOLD, 7.5)
    c.drawString(margin, 92, "TỔNG DUNG LƯỢNG THEO HƯỚNG")
    direction_totals = (
        df_effective.groupby("Hướng")["Dung lượng đang khai thác (GB)"]
        .sum().reindex(DIRECTIONS, fill_value=0)
    )
    col_w = content_w / len(DIRECTIONS)
    for i, direction in enumerate(DIRECTIONS):
        value = float(direction_totals[direction])
        pct = value / total_effective * 100 if total_effective else 0
        c.setFillColor(navy)
        c.setFont(PDF_REGULAR, 7)
        c.drawString(margin + i * col_w, 77, f"{direction}: {value:,.2f} GB ({pct:.1f}%)")
    c.setFillColor(gray)
    c.setFont(PDF_REGULAR, 6.2)
    c.drawString(
        margin, 58,
        "Ghi chú: dung lượng đang khai thác đã loại trừ các hướng được chọn ảnh hưởng bởi sự cố."
    )

    c.save()
    buffer.seek(0)
    return buffer.getvalue()

# ---------------- Sidebar: data entry ----------------
with st.sidebar:
    st.markdown("## ⚙️ Quản trị dữ liệu")
    st.caption("Cập nhật số liệu dung lượng sử dụng thực tế (GB).")
    st.markdown(f"**Cập nhật gần nhất:**  \n{st.session_state.last_updated.strftime('%d/%m/%Y %H:%M:%S')}")
    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()
    st.divider()
    with st.expander("Nhập dung lượng sử dụng (GB)", expanded=True):
        st.caption("Chỉ nhập tại các tuyến có kết nối (ô x trong bảng gốc). Ô không có kết nối được cố định bằng 0.")
        with st.form("capacity_form"):
            edited = {}
            for s, d in VALID_ROUTES:
                key = f"{s}|{d}"
                edited[key] = st.number_input(
                    f"{s} → {d} (GB)",
                    min_value=0.0,
                    value=float(st.session_state.capacities.get(key, 0.0)),
                    step=1.0,
                    format="%.2f",
                    key=f"input_{s}_{d}",
                )
            save_capacity = st.form_submit_button("Lưu số liệu dung lượng", type="primary", use_container_width=True)
            if save_capacity:
                st.session_state.capacities.update(edited)
                mark_updated()
                st.success("Đã cập nhật số liệu dung lượng.")
                st.rerun()

    with st.expander("Cập nhật trạng thái hệ thống", expanded=True):
        with st.form("status_form"):
            new_status = {}
            new_affected = {}
            for s in SYSTEMS:
                st.markdown(f"**{s} — {SYSTEM_TYPE[s]}**")
                new_status[s] = st.radio(
                    f"Trạng thái {s}",
                    ["Bình thường", "Nghiêm trọng"],
                    index=0 if st.session_state.system_status[s] == "Bình thường" else 1,
                    horizontal=True,
                    key=f"status_{s}",
                    label_visibility="collapsed",
                )
                if new_status[s] == "Nghiêm trọng":
                    available = [d for d in DIRECTIONS if ROUTE_EXISTS[s][d]]
                    prior = [d for d in st.session_state.affected_directions.get(s, []) if d in available]
                    new_affected[s] = st.multiselect(
                        f"Hướng bị ảnh hưởng — {s}",
                        options=available,
                        default=prior,
                        key=f"affected_{s}",
                        help="Chỉ chọn hướng có kết nối theo bảng gốc. Có thể chọn nhiều hướng.",
                    )
                else:
                    new_affected[s] = []
                st.divider()
            save_status = st.form_submit_button("Lưu trạng thái sự cố", type="primary", use_container_width=True)
            if save_status:
                st.session_state.system_status.update(new_status)
                st.session_state.affected_directions.update(new_affected)
                mark_updated()
                st.success("Đã cập nhật trạng thái hệ thống.")
                st.rerun()

# ---------------- Main dashboard ----------------
st.markdown('<div class="main-title">🌐 BÁO CÁO HIỆN TRẠNG HOẠT ĐỘNG MẠNG CÁP QUỐC TẾ</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Dashboard giám sát trạng thái và phân tích dung lượng khai thác</div>', unsafe_allow_html=True)

top1, top2, top3, top4 = st.columns([1.1, 1, 1, 1])
df_raw = get_raw_df()
df_effective = get_effective_df()
total_raw = df_raw["Dung lượng nhập (GB)"].sum()
total_effective = df_effective["Dung lượng đang khai thác (GB)"].sum()
critical_count = sum(st.session_state.system_status[s] == "Nghiêm trọng" for s in SYSTEMS)
top1.metric("Dung lượng nhập", f"{total_raw:,.2f} GB")
top2.metric("Đang khai thác", f"{total_effective:,.2f} GB")
top3.metric("Hệ thống nghiêm trọng", f"{critical_count}/6")
top4.metric("Tuyến kết nối hợp lệ", f"{len(VALID_ROUTES)}/30")

st.caption(f"Ngày cập nhật số liệu gần nhất: **{st.session_state.last_updated.strftime('%d/%m/%Y %H:%M:%S')}**")
st.info("Lưu ý: dung lượng ban đầu bằng 0 GB cho đến khi bạn nhập số liệu thực tế. Ký hiệu x/0 trong bảng chỉ xác định tuyến có/không có kết nối.")

st.markdown("## Chức năng 1 — Dashboard trạng thái hoạt động")
status_cols = st.columns(3)
for i, s in enumerate(SYSTEMS):
    is_bad = st.session_state.system_status[s] == "Nghiêm trọng"
    css_class = "status-bad" if is_bad else "status-ok"
    status_text = "🔴 NGHIÊM TRỌNG" if is_bad else "🟢 BÌNH THƯỜNG"
    affected = st.session_state.affected_directions.get(s, []) if is_bad else []
    detail = ("Hướng ảnh hưởng: " + (", ".join(affected) if affected else "Chưa chọn hướng"))
    with status_cols[i % 3]:
        st.markdown(
            f'<div class="status-card {css_class}">'
            f'<div class="status-name">{s}</div>'
            f'<div class="status-type">{SYSTEM_TYPE[s]}</div>'
            f'<div class="status-state">{status_text}</div>'
            f'<div style="font-size:.78rem;margin-top:5px;">{detail if is_bad else "Tất cả hướng hợp lệ đang được xem là bình thường"}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

with st.expander("Ma trận kết nối gốc theo bảng cung cấp", expanded=False):
    matrix = pd.DataFrame(
        {d: ["x" if ROUTE_EXISTS[s][d] else "0" for s in SYSTEMS] for d in DIRECTIONS},
        index=SYSTEMS
    )
    matrix.insert(0, "Loại cáp", [SYSTEM_TYPE[s] for s in SYSTEMS])
    st.dataframe(matrix, use_container_width=True)

st.markdown("## Chức năng 2 — Dashboard phân tích dung lượng")
tab1, tab2, tab3 = st.tabs([
    "So sánh 6 hệ thống",
    "Tỷ trọng theo 5 hướng",
    "Cơ cấu toàn mạng",
])

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
        st.dataframe(overall[["Hệ thống", "Dung lượng đang khai thác (GB)", "Tỷ trọng toàn mạng (%)"]].round(2), use_container_width=True, hide_index=True)
        st.markdown("### Tổng theo hướng")
        by_direction = df_effective.groupby("Hướng")["Dung lượng đang khai thác (GB)"].sum().reindex(DIRECTIONS, fill_value=0).reset_index()
        by_direction["Tỷ trọng toàn mạng (%)"] = np.where(total_effective > 0, by_direction["Dung lượng đang khai thác (GB)"] / total_effective * 100, 0)
        st.dataframe(by_direction.round(2), use_container_width=True, hide_index=True)

st.markdown("## Xuất báo cáo")
st.write("Báo cáo PDF một trang A4 ngang; biểu đồ trong PDF sử dụng cùng cấu hình với dashboard.")
pdf_bytes = build_pdf(get_effective_df(), get_raw_df())
st.download_button(
    "Tạo báo cáo PDF",
    data=pdf_bytes,
    file_name=f"Bao_cao_mang_cap_quoc_te_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
    mime="application/pdf",
    type="primary",
)

st.divider()
st.caption("International Cable Network Dashboard • Đơn vị dung lượng: GB • Dữ liệu chỉ được cập nhật khi người vận hành lưu thay đổi.")
