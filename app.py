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

# Register a Unicode font when available so Vietnamese text renders correctly in PDF.
PDF_REGULAR = "Helvetica"
PDF_BOLD = "Helvetica-Bold"
for regular_path, bold_path in [
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
    return hmac.compare_digest(username, "admin") and hmac.compare_digest(password, "admin123")

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
.block-container {padding-top:1.2rem; padding-bottom:2rem; max-width:1500px;}
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

def build_pdf(df_effective, df_raw):
    """Create a balanced single-page A4 landscape PDF with all three dashboard charts."""
    buffer = BytesIO()
    page_w, page_h = landscape(A4)
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    navy = colors.HexColor("#16324F")
    blue = colors.HexColor("#2563EB")
    pale = colors.HexColor("#EFF6FF")
    green = colors.HexColor("#16A34A")
    red = colors.HexColor("#DC2626")
    gray = colors.HexColor("#64748B")
    ink = colors.HexColor("#1F2937")
    margin = 25

    # Header
    c.setFillColor(navy)
    c.roundRect(margin, page_h-65, page_w-2*margin, 43, 8, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont(PDF_BOLD, 14)
    c.drawCentredString(page_w/2, page_h-39, "BÁO CÁO HIỆN TRẠNG HOẠT ĐỘNG MẠNG CÁP QUỐC TẾ")
    c.setFont(PDF_REGULAR, 7.5)
    c.drawCentredString(page_w/2, page_h-54, "Ngày báo cáo: " + datetime.now().strftime("%d/%m/%Y %H:%M"))

    # System status cards
    c.setFillColor(ink); c.setFont(PDF_BOLD, 8.5)
    c.drawString(margin, page_h-81, "CHỨC NĂNG 1  |  TRẠNG THÁI HỆ THỐNG")
    gap=6; card_w=(page_w-2*margin-gap*5)/6; card_y=page_h-128
    for i, system in enumerate(SYSTEMS):
        x=margin+i*(card_w+gap); bad=st.session_state.system_status[system]=="Nghiêm trọng"
        c.setFillColor(red if bad else green); c.roundRect(x,card_y,card_w,35,5,fill=1,stroke=0)
        c.setFillColor(colors.white); c.setFont(PDF_BOLD,8)
        c.drawCentredString(x+card_w/2,card_y+22,system)
        c.setFont(PDF_REGULAR,6.4)
        c.drawCentredString(x+card_w/2,card_y+10,"NGHIÊM TRỌNG" if bad else "BÌNH THƯỜNG")
        if bad:
            affected=st.session_state.affected_directions.get(system,[])
            label="Ảnh hưởng: "+(", ".join(affected) if affected else "Chưa chọn")
            c.setFillColor(red); c.setFont(PDF_REGULAR,5.4)
            c.drawCentredString(x+card_w/2,card_y-8,label[:34])

    # Only the two requested summary metrics
    total_effective=float(df_effective["Dung lượng đang khai thác (GB)"].sum())
    bad_count=sum(st.session_state.system_status[x]=="Nghiêm trọng" for x in SYSTEMS)
    metric_y=page_h-157; metric_gap=10; metric_w=(page_w-2*margin-metric_gap)/2
    for i,(label,value) in enumerate([
        ("ĐANG KHAI THÁC",f"{total_effective:,.2f} GB"),
        ("HỆ THỐNG NGHIÊM TRỌNG",f"{bad_count}/6"),
    ]):
        x=margin+i*(metric_w+metric_gap)
        c.setFillColor(pale); c.roundRect(x,metric_y-23,metric_w,26,4,fill=1,stroke=0)
        c.setFillColor(gray); c.setFont(PDF_BOLD,7); c.drawString(x+8,metric_y-7,label)
        c.setFillColor(navy); c.setFont(PDF_BOLD,10); c.drawRightString(x+metric_w-8,metric_y-17,value)

    # Recreate the three application chart types using the same source data.
    # One row of three equally sized panels keeps the report to a single page.
    fig, axes = plt.subplots(1,3,figsize=(13.0,3.0),dpi=170)
    fig.patch.set_facecolor("white")
    totals=df_effective.groupby("Hệ thống")["Dung lượng đang khai thác (GB)"].sum().reindex(SYSTEMS,fill_value=0)
    ax=axes[0]
    bars=ax.bar(SYSTEMS,totals.values)
    ax.set_title("Dung lượng đang khai thác theo hệ thống (GB)",fontsize=8.5,fontweight="bold",pad=9)
    ax.tick_params(axis="x",labelsize=7); ax.tick_params(axis="y",labelsize=6.5)
    ax.grid(axis="y",alpha=.22); ax.set_axisbelow(True)
    ax.bar_label(bars,fmt="%.1f",fontsize=6,padding=2)

    pivot=df_effective.pivot_table(index="Hướng",columns="Hệ thống",values="Dung lượng đang khai thác (GB)",aggfunc="sum",fill_value=0).reindex(DIRECTIONS,fill_value=0)
    direction_totals=pivot.sum(axis=1)
    shares=pivot.div(direction_totals.replace(0,np.nan),axis=0).fillna(0)*100
    ax=axes[1]; bottom=np.zeros(len(DIRECTIONS))
    for system in SYSTEMS:
        vals=shares[system].values if system in shares.columns else np.zeros(len(DIRECTIONS))
        ax.bar(DIRECTIONS,vals,bottom=bottom,label=system)
        bottom+=vals
    ax.set_title("Tỷ trọng theo 5 hướng kết nối (%)",fontsize=8.5,fontweight="bold",pad=9)
    ax.set_ylim(0,100); ax.set_ylabel("%",fontsize=6.5)
    ax.tick_params(axis="x",labelsize=6.5); ax.tick_params(axis="y",labelsize=6.5)
    ax.grid(axis="y",alpha=.2); ax.set_axisbelow(True)
    ax.legend(fontsize=5.5,ncol=3,loc="upper center",bbox_to_anchor=(.5,-.17),frameon=False)

    ax=axes[2]
    if total_effective>0:
        nonzero=totals[totals>0]
        wedges, texts, autotexts=ax.pie(nonzero.values,labels=nonzero.index,autopct="%1.1f%%",
            startangle=90,counterclock=False,pctdistance=.72,labeldistance=1.08,
            textprops={"fontsize":6.5},wedgeprops={"width":.48,"edgecolor":"white"})
        for t in autotexts: t.set_fontsize(6)
    else:
        ax.text(.5,.5,"Chưa có dung lượng\nđang khai thác",ha="center",va="center",fontsize=8,transform=ax.transAxes)
        ax.set_axis_off()
    ax.set_title("Cơ cấu dung lượng đang khai thác toàn mạng",fontsize=8.5,fontweight="bold",pad=9)
    fig.subplots_adjust(left=.04,right=.98,top=.84,bottom=.22,wspace=.35)
    chart_buf=BytesIO(); fig.savefig(chart_buf,format="png",bbox_inches="tight",facecolor="white")
    plt.close(fig); chart_buf.seek(0)
    chart_x=margin; chart_y=49; chart_w=page_w-2*margin; chart_h=page_h-205
    c.drawImage(ImageReader(chart_buf),chart_x,chart_y,width=chart_w,height=chart_h,preserveAspectRatio=True,anchor="c",mask="auto")

    # Footer summary of direction totals
    c.setFillColor(ink); c.setFont(PDF_BOLD,7)
    c.drawString(margin,35,"TỔNG DUNG LƯỢNG THEO HƯỚNG")
    c.setFont(PDF_REGULAR,6.5); col_w=(page_w-2*margin)/5
    for i,direction in enumerate(DIRECTIONS):
        value=float(direction_totals[direction]); pct=value/total_effective*100 if total_effective else 0
        c.drawString(margin+i*col_w,22,f"{direction}: {value:,.2f} GB ({pct:.1f}%)")
    c.setFillColor(gray); c.setFont(PDF_REGULAR,5.8)
    c.drawRightString(page_w-margin,10,"Dung lượng đang khai thác đã loại trừ các tuyến được chọn ảnh hưởng bởi sự cố.")
    c.save(); buffer.seek(0)
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
    fig = px.bar(
        system_totals,
        x="Hệ thống",
        y="Dung lượng đang khai thác (GB)",
        color="Hệ thống",
        text_auto=".2f",
        title="Dung lượng đang khai thác theo hệ thống (GB)",
        hover_data={"Hệ thống": True, "Dung lượng đang khai thác (GB)": ":,.2f"},
    )
    fig.update_layout(showlegend=False, height=390, margin=dict(l=20, r=20, t=60, b=20))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(system_totals, use_container_width=True, hide_index=True)

with tab2:
    pivot = df_effective.pivot_table(
        index="Hướng",
        columns="Hệ thống",
        values="Dung lượng đang khai thác (GB)",
        aggfunc="sum",
        fill_value=0,
    ).reindex(DIRECTIONS, fill_value=0)
    direction_total = pivot.sum(axis=1)
    share = pivot.div(direction_total.replace(0, np.nan), axis=0).fillna(0) * 100
    share_long = share.reset_index().melt(id_vars="Hướng", var_name="Hệ thống", value_name="Tỷ trọng (%)")
    fig = px.bar(
        share_long,
        x="Hướng",
        y="Tỷ trọng (%)",
        color="Hệ thống",
        barmode="stack",
        text=share_long["Tỷ trọng (%)"].map(lambda x: f"{x:.1f}%" if x >= 4 else ""),
        title="Tỷ trọng từng hệ thống trong tổng dung lượng của mỗi hướng kết nối",
        hover_data={"Tỷ trọng (%)": ":.2f"},
    )
    fig.update_layout(height=410, yaxis_range=[0, 100], margin=dict(l=20, r=20, t=60, b=20))
    st.plotly_chart(fig, use_container_width=True)
    show_share = share.copy().round(2)
    show_share["Tổng hướng (GB)"] = direction_total
    st.dataframe(show_share, use_container_width=True)

with tab3:
    overall = system_totals.copy()
    overall["Tỷ trọng toàn mạng (%)"] = np.where(
        total_effective > 0,
        overall["Dung lượng đang khai thác (GB)"] / total_effective * 100,
        0,
    )
    col_a, col_b = st.columns([1.2, 1])
    with col_a:
        if total_effective > 0:
            fig = px.pie(
                overall,
                names="Hệ thống",
                values="Dung lượng đang khai thác (GB)",
                hole=0.52,
                title="Cơ cấu dung lượng đang khai thác toàn mạng",
            )
            fig.update_traces(textposition="inside", textinfo="label+percent")
        else:
            fig = go.Figure()
            fig.add_annotation(text="Chưa có dung lượng lớn hơn 0 GB", x=.5, y=.5, showarrow=False, font=dict(size=16))
            fig.update_layout(title="Cơ cấu dung lượng đang khai thác toàn mạng")
        fig.update_layout(height=390, margin=dict(l=10, r=10, t=60, b=10))
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
st.write("Báo cáo PDF một trang A4 ngang gồm trạng thái hệ thống và ba biểu đồ đồng bộ theo dữ liệu trên ứng dụng.")
if st.button("Tạo báo cáo PDF", type="primary", use_container_width=False):
    pdf_bytes = build_pdf(get_effective_df(), get_raw_df())
    st.download_button(
        "⬇️ Tải báo cáo PDF",
        data=pdf_bytes,
        file_name=f"Bao_cao_mang_cap_quoc_te_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
        mime="application/pdf",
        use_container_width=False,
    )

st.divider()
st.caption("International Cable Network Dashboard • Đơn vị dung lượng: GB • Dữ liệu chỉ được cập nhật khi người vận hành lưu thay đổi.")
