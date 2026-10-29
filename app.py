import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import sqlite3
import secrets
import calendar
from contextlib import contextmanager
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

        # Use the same Unicode font for Matplotlib charts embedded in the PDF.
        from matplotlib import font_manager
        font_manager.fontManager.addfont(REGULAR_FONT_PATH)
        font_manager.fontManager.addfont(BOLD_FONT_PATH)
        plt.rcParams["font.family"] = font_manager.FontProperties(
            fname=REGULAR_FONT_PATH
        ).get_name()
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

# ---------------- Authentication and user management ----------------
DB_PATH = os.path.join(BASE_DIR, "user_accounts.db")
VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DURATION_OPTIONS = {
    "1 ngày": "day",
    "1 tuần": "week",
    "1 tháng": "month",
    "1 năm": "year",
}


@contextmanager
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_user_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'Viewer',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Hoạt động'
            )
        """)


def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000).hex()
    return digest, salt


def add_duration(start: datetime, duration_label: str) -> datetime:
    unit = DURATION_OPTIONS[duration_label]
    if unit == "day":
        return start + timedelta(days=1)
    if unit == "week":
        return start + timedelta(weeks=1)
    if unit == "month":
        month_index = start.month - 1 + 1
        year = start.year + month_index // 12
        month = month_index % 12 + 1
        return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))
    return start.replace(year=start.year + 1, day=min(start.day, calendar.monthrange(start.year + 1, start.month)[1]))


def utc_now():
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_vietnam_time(value: str) -> str:
    return parse_timestamp(value).astimezone(VIETNAM_TZ).strftime("%d/%m/%Y %H:%M:%S (GMT+7)")


def get_admin_credentials():
    try:
        auth = st.secrets["auth"]
        return str(auth["username"]).strip(), str(auth["password"])
    except Exception:
        return None, None


def check_login(username: str, password: str):
    username = username.strip()
    admin_user, admin_password = get_admin_credentials()
    if admin_user and admin_password and hmac.compare_digest(username, admin_user) and hmac.compare_digest(password, admin_password):
        return {"ok": True, "role": "admin", "username": admin_user, "message": ""}

    with db_connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    if not user:
        return {"ok": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}
    expected_hash, _ = hash_password(password, user["password_salt"])
    if not hmac.compare_digest(expected_hash, user["password_hash"]):
        return {"ok": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}
    if user["status"] == "Đã xóa":
        return {"ok": False, "message": "Tài khoản đã bị xóa. Vui lòng liên hệ quản trị viên."}
    if user["status"] == "Tạm ngưng":
        return {"ok": False, "message": "Tài khoản đang tạm ngưng. Vui lòng liên hệ quản trị viên."}
    if utc_now() >= parse_timestamp(user["expires_at"]):
        return {"ok": False, "message": "Tài khoản đã hết hạn sử dụng. Vui lòng liên hệ quản trị viên."}
    return {"ok": True, "role": "Viewer", "username": user["username"], "message": ""}


def viewer_session_is_valid(username: str) -> tuple[bool, str]:
    with db_connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    if not user or user["status"] == "Đã xóa":
        return False, "Tài khoản đã bị xóa hoặc không còn tồn tại."
    if user["status"] == "Tạm ngưng":
        return False, "Tài khoản của bạn đang bị quản trị viên tạm ngưng."
    if utc_now() >= parse_timestamp(user["expires_at"]):
        return False, "Tài khoản của bạn đã hết hạn sử dụng."
    return True, ""


def list_managed_users():
    admin_user, _ = get_admin_credentials()
    rows = []
    if admin_user:
        rows.append({
            "Tên đăng nhập": admin_user,
            "Vai trò": "admin",
            "Hạn sử dụng (GMT+7)": "Vĩnh viễn",
            "Trạng thái": "Hoạt động",
        })
    with db_connect() as conn:
        users = conn.execute("SELECT username, role, expires_at, status FROM users ORDER BY username COLLATE NOCASE").fetchall()
    now = utc_now()
    for user in users:
        status = user["status"]
        if status == "Hoạt động" and now >= parse_timestamp(user["expires_at"]):
            status = "Hết hạn"
        rows.append({
            "Tên đăng nhập": user["username"],
            "Vai trò": user["role"],
            "Hạn sử dụng (GMT+7)": format_vietnam_time(user["expires_at"]),
            "Trạng thái": status,
        })
    return pd.DataFrame(rows, columns=["Tên đăng nhập", "Vai trò", "Hạn sử dụng (GMT+7)", "Trạng thái"])


def create_viewer_account(username: str, password: str, duration_label: str):
    username = username.strip()
    admin_user, _ = get_admin_credentials()
    if not username or len(username) < 3:
        return False, "Tên đăng nhập phải có ít nhất 3 ký tự."
    if len(username) > 64 or any(ch.isspace() for ch in username):
        return False, "Tên đăng nhập tối đa 64 ký tự và không chứa khoảng trắng."
    if admin_user and username.casefold() == admin_user.casefold():
        return False, "Tên đăng nhập này đang được dùng cho tài khoản admin."
    if len(password) < 8:
        return False, "Mật khẩu phải có ít nhất 8 ký tự."
    if duration_label not in DURATION_OPTIONS:
        return False, "Vui lòng chọn thời hạn hợp lệ."
    now = utc_now()
    expires = add_duration(now.astimezone(VIETNAM_TZ), duration_label).astimezone(timezone.utc)
    password_hash, salt = hash_password(password)
    try:
        with db_connect() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, password_salt, role, created_at, expires_at, status) VALUES (?, ?, ?, 'Viewer', ?, ?, 'Hoạt động')",
                (username, password_hash, salt, now.isoformat(), expires.isoformat()),
            )
    except sqlite3.IntegrityError:
        return False, "Tên đăng nhập đã tồn tại (kể cả tài khoản đã xóa)."
    return True, f"Đã tạo tài khoản Viewer '{username}', hạn đến {format_vietnam_time(expires.isoformat())}."


def manage_viewer(username: str, action: str, duration_label: str | None = None):
    admin_user, _ = get_admin_credentials()
    if not username or (admin_user and username.casefold() == admin_user.casefold()):
        return False, "Không thể thao tác với tài khoản admin."
    with db_connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        if not user:
            return False, "Không tìm thấy tài khoản."
        if user["status"] == "Đã xóa" and action != "delete":
            return False, "Tài khoản đã xóa không thể thay đổi."
        if action == "suspend":
            conn.execute("UPDATE users SET status = 'Tạm ngưng' WHERE username = ? COLLATE NOCASE", (username,))
            return True, f"Đã tạm ngưng tài khoản '{username}'."
        if action == "activate":
            if utc_now() >= parse_timestamp(user["expires_at"]):
                return False, "Tài khoản đã hết hạn; hãy gia hạn trước khi mở khóa."
            conn.execute("UPDATE users SET status = 'Hoạt động' WHERE username = ? COLLATE NOCASE", (username,))
            return True, f"Đã mở khóa tài khoản '{username}'."
        if action == "delete":
            conn.execute("UPDATE users SET status = 'Đã xóa' WHERE username = ? COLLATE NOCASE", (username,))
            return True, f"Đã xóa mềm tài khoản '{username}'. Tài khoản vẫn lưu trong lịch sử quản lý."
        if action == "extend":
            if duration_label not in DURATION_OPTIONS:
                return False, "Vui lòng chọn thời hạn gia hạn."
            current_expiry = parse_timestamp(user["expires_at"])
            base = max(current_expiry, utc_now()).astimezone(VIETNAM_TZ)
            new_expiry = add_duration(base, duration_label).astimezone(timezone.utc)
            conn.execute("UPDATE users SET expires_at = ? WHERE username = ? COLLATE NOCASE", (new_expiry.isoformat(), username))
            return True, f"Đã gia hạn tài khoản '{username}' đến {format_vietnam_time(new_expiry.isoformat())}."
    return False, "Thao tác không hợp lệ."


init_user_db()
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_role" not in st.session_state:
    st.session_state.user_role = None
if "current_username" not in st.session_state:
    st.session_state.current_username = None
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
                result = check_login(username, password)
                if result["ok"]:
                    st.session_state.authenticated = True
                    st.session_state.user_role = result["role"]
                    st.session_state.current_username = result["username"]
                    st.rerun()
                else:
                    st.error(result["message"])

if not st.session_state.authenticated:
    login_page()
    st.stop()

# Re-check viewer permissions on every rerun so a suspended, deleted, or expired
# account cannot continue using an already-open session.
if st.session_state.get("user_role") == "Viewer":
    valid, reason = viewer_session_is_valid(st.session_state.get("current_username", ""))
    if not valid:
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.current_username = None
        st.error(reason + " Vui lòng đăng nhập lại sau khi được quản trị viên xử lý.")
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
            # Sanitize values before aggregation and plotting.
            raw_value = st.session_state.capacities.get(f"{system}|{direction}", 0.0)
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                value = 0.0
            if not np.isfinite(value) or value < 0:
                value = 0.0
            if not exists:
                value = 0.0
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


# Fixed palette: the same system always has the same clearly distinguishable color.
SYSTEM_COLORS = {
    "SJC2": "#0072B2",
    "APG": "#E69F00",
    "AAG": "#009E73",
    "AAE-1": "#CC79A7",
    "VSTN": "#D55E00",
    "CSC": "#56B4E9",
}


def create_analysis_figures(df_effective):
    """Build the three shared Matplotlib charts used in both Streamlit and PDF."""
    plt.rcParams.update({
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.labelsize": 10,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 8,
    })

    totals = (
        df_effective.groupby("Hệ thống")["Dung lượng đang khai thác (GB)"]
        .sum().reindex(SYSTEMS, fill_value=0)
    )
    pivot = (
        df_effective.pivot_table(
            index="Hướng", columns="Hệ thống",
            values="Dung lượng đang khai thác (GB)",
            aggfunc="sum", fill_value=0,
        ).reindex(DIRECTIONS, fill_value=0)
    )
    direction_totals = pivot.sum(axis=1)
    shares = pivot.div(direction_totals.replace(0, np.nan), axis=0).fillna(0) * 100
    total_effective = float(totals.sum())

    # Chart 1: capacity by system.
    fig1, ax1 = plt.subplots(figsize=(10, 4.3), dpi=130)
    fig1.patch.set_facecolor("white")
    bars = ax1.bar(
        SYSTEMS, totals.values,
        color=[SYSTEM_COLORS[s] for s in SYSTEMS],
        edgecolor="white", linewidth=0.8, width=0.68,
    )
    ax1.set_title("Dung lượng đang khai thác theo hệ thống (GB)", pad=14)
    ax1.set_ylabel("GB")
    ax1.set_ylim(bottom=0)
    ax1.grid(axis="y", alpha=0.25)
    ax1.set_axisbelow(True)
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    if total_effective > 0:
        ax1.bar_label(bars, labels=[f"{v:,.2f}" for v in totals.values], padding=3, fontsize=8)
        max_val = max(float(totals.max()), 1.0)
        ax1.set_ylim(0, max_val * 1.18)
    else:
        ax1.text(0.5, 0.55, "Chưa có dữ liệu dung lượng > 0 GB",
                 transform=ax1.transAxes, ha="center", va="center", color="#64748B")
    fig1.tight_layout()

    # Chart 2: per-direction percentage mix, with consistent system colors.
    fig2, ax2 = plt.subplots(figsize=(10, 4.5), dpi=130)
    fig2.patch.set_facecolor("white")
    bottom = np.zeros(len(DIRECTIONS))
    for system in SYSTEMS:
        vals = shares[system].to_numpy() if system in shares.columns else np.zeros(len(DIRECTIONS))
        ax2.bar(
            DIRECTIONS, vals, bottom=bottom, label=system,
            color=SYSTEM_COLORS[system], edgecolor="white", linewidth=0.6, width=0.68,
        )
        for idx, (val, base) in enumerate(zip(vals, bottom)):
            if val >= 7:
                ax2.text(idx, base + val / 2, f"{val:.0f}%",
                         ha="center", va="center", fontsize=8, color="white",
                         fontweight="bold")
        bottom += vals
    ax2.set_title("Tỷ trọng từng hệ thống trong tổng dung lượng mỗi hướng kết nối (%)", pad=14)
    ax2.set_ylabel("%")
    ax2.set_ylim(0, 108)
    ax2.grid(axis="y", alpha=0.22)
    ax2.set_axisbelow(True)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.legend(ncol=6, loc="upper center", bbox_to_anchor=(0.5, -0.13), frameon=False)
    if total_effective <= 0:
        ax2.text(0.5, 0.5, "Chưa có dữ liệu để tính tỷ trọng",
                 transform=ax2.transAxes, ha="center", va="center", color="#64748B")
    fig2.tight_layout(rect=[0, 0.08, 1, 1])

    # Chart 3: overall capacity distribution as a donut.
    fig3, ax3 = plt.subplots(figsize=(8.2, 4.5), dpi=130)
    fig3.patch.set_facecolor("white")
    ax3.set_title("Cơ cấu dung lượng đang khai thác toàn mạng", pad=14)
    if total_effective > 0:
        nonzero = totals[totals > 0]
        wedges, _, autotexts = ax3.pie(
            nonzero.values,
            labels=None,
            colors=[SYSTEM_COLORS[s] for s in nonzero.index],
            startangle=90,
            counterclock=False,
            autopct=lambda pct: f"{pct:.1f}%" if pct >= 4 else "",
            pctdistance=0.78,
            wedgeprops=dict(width=0.42, edgecolor="white", linewidth=1.2),
            textprops=dict(color="white", fontsize=8, fontweight="bold"),
        )
        ax3.text(0, 0, f"{total_effective:,.1f}\\nGB", ha="center", va="center",
                 fontsize=12, fontweight="bold", color="#16324F")
        ax3.legend(
            wedges,
            [f"{s}: {totals[s]:,.2f} GB" for s in nonzero.index],
            loc="center left", bbox_to_anchor=(0.98, 0.5), frameon=False,
            fontsize=8,
        )
    else:
        ax3.text(0.5, 0.5, "Chưa có dung lượng lớn hơn 0 GB",
                 transform=ax3.transAxes, ha="center", va="center", color="#64748B")
        ax3.axis("off")
    ax3.set_aspect("equal")
    fig3.tight_layout()

    # Chart 4: compare entered (normal/no-incident) capacity against current capacity
    # for each of the five directions. Both bars show capacity at the top and
    # percentage of baseline inside the bar; missing/zero directions are 0.
    raw_direction_totals = (
        df_effective.groupby("Hướng")["Dung lượng nhập (GB)"]
        .sum().reindex(DIRECTIONS, fill_value=0).astype(float)
    )
    effective_direction_totals = (
        df_effective.groupby("Hướng")["Dung lượng đang khai thác (GB)"]
        .sum().reindex(DIRECTIONS, fill_value=0).astype(float)
    )
    remaining_pct = np.divide(
        effective_direction_totals.to_numpy() * 100.0,
        raw_direction_totals.to_numpy(),
        out=np.zeros(len(DIRECTIONS), dtype=float),
        where=raw_direction_totals.to_numpy() > 0,
    )
    fig4, ax4 = plt.subplots(figsize=(10, 4.5), dpi=130)
    fig4.patch.set_facecolor("white")
    x = np.arange(len(DIRECTIONS))
    width = 0.34
    baseline_bars = ax4.bar(
        x - width / 2, raw_direction_totals.to_numpy(), width,
        label="Thời điểm an toàn", color="#2563EB", edgecolor="white", linewidth=1.0,
    )
    current_bars = ax4.bar(
        x + width / 2, effective_direction_totals.to_numpy(), width,
        label="Dung lượng hiện tại", color="#16A34A", edgecolor="white", linewidth=1.0,
    )
    max_capacity = max(float(raw_direction_totals.max()), float(effective_direction_totals.max()), 0.0)
    ax4.set_title("Tỷ trọng dung lượng hiện tại so với thời điểm an toàn theo hướng kết nối", pad=14, fontsize=12, fontweight="bold")
    ax4.set_ylabel("Dung lượng (GB)", fontsize=10, fontweight="bold")
    ax4.set_xticks(x, DIRECTIONS)
    ax4.tick_params(axis="x", labelsize=9, pad=5)
    ax4.tick_params(axis="y", labelsize=8)
    ax4.set_ylim(0, max(max_capacity * 1.35, 1.0))
    ax4.grid(axis="y", alpha=0.20, linestyle="--")
    ax4.set_axisbelow(True)
    ax4.spines["top"].set_visible(False)
    ax4.spines["right"].set_visible(False)
    ax4.legend(ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.15), frameon=False, fontsize=9)
    label_offset = max(max_capacity * 0.025, 0.12)
    # Percentage labels use opaque white badges with dark text for legibility
    # against either bar color; capacity labels are larger and bold above bars.
    for bar, capacity in zip(baseline_bars, raw_direction_totals.to_numpy()):
        pct = 100 if capacity > 0 else 0
        if capacity > 0:
            ax4.text(bar.get_x() + bar.get_width()/2, capacity * 0.52, f"{pct}%",
                     ha="center", va="center", fontsize=9, color="#16324F", fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.22", facecolor="white", edgecolor="none", alpha=0.94))
        else:
            ax4.text(bar.get_x() + bar.get_width()/2, label_offset * 0.35, "0%",
                     ha="center", va="bottom", fontsize=9, color="#16324F", fontweight="bold")
        ax4.text(bar.get_x() + bar.get_width()/2, capacity + label_offset,
                 f"{capacity:,.2f}", ha="center", va="bottom", fontsize=8, fontweight="bold", color="#1F2937",
                 bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.90))
    for bar, capacity, pct in zip(current_bars, effective_direction_totals.to_numpy(), remaining_pct):
        if capacity > 0:
            ax4.text(bar.get_x() + bar.get_width()/2, capacity * 0.52, f"{pct:.0f}%",
                     ha="center", va="center", fontsize=9, color="#14532D", fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.22", facecolor="white", edgecolor="none", alpha=0.94))
        else:
            ax4.text(bar.get_x() + bar.get_width()/2, label_offset * 0.35, "0%",
                     ha="center", va="bottom", fontsize=9, color="#14532D", fontweight="bold")
        ax4.text(bar.get_x() + bar.get_width()/2, capacity + label_offset,
                 f"{capacity:,.2f}", ha="center", va="bottom", fontsize=8, fontweight="bold", color="#1F2937",
                 bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.90))
    fig4.tight_layout(rect=[0, 0.11, 1, 0.96])
    return [fig1, fig2, fig3, fig4]


def build_pdf(df_effective, df_raw):
    """Create a balanced, single-page landscape A4 PDF report."""
    buffer = BytesIO()
    page_w, page_h = landscape(A4)
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    navy = colors.HexColor("#16324F")
    pale = colors.HexColor("#EFF6FF")
    green = colors.HexColor("#16A34A")
    red = colors.HexColor("#DC2626")
    gray = colors.HexColor("#64748B")
    ink = colors.HexColor("#1F2937")
    margin = 24

    total_raw = float(df_raw["Dung lượng nhập (GB)"].sum())
    total_effective = float(df_effective["Dung lượng đang khai thác (GB)"].sum())
    bad_count = sum(st.session_state.system_status[s] == "Nghiêm trọng" for s in SYSTEMS)

    # Header.
    c.setFillColor(navy)
    c.roundRect(margin, page_h - 55, page_w - 2 * margin, 38, 8, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(PDF_BOLD, 13)
    c.drawCentredString(page_w / 2, page_h - 33, "BÁO CÁO HIỆN TRẠNG HOẠT ĐỘNG MẠNG CÁP QUỐC TẾ")
    c.setFont(PDF_REGULAR, 7.5)
    c.drawCentredString(page_w / 2, page_h - 46, "Ngày báo cáo: " + datetime.now().strftime("%d/%m/%Y"))

    # Compact status cards.
    section_y = page_h - 72
    c.setFillColor(ink)
    c.setFont(PDF_BOLD, 8)
    c.drawString(margin, section_y, "TRẠNG THÁI HỆ THỐNG")
    card_gap = 6
    card_w = (page_w - 2 * margin - card_gap * 5) / 6
    card_y = page_h - 116
    for i, system in enumerate(SYSTEMS):
        x = margin + i * (card_w + card_gap)
        is_bad = st.session_state.system_status[system] == "Nghiêm trọng"
        c.setFillColor(red if is_bad else green)
        c.roundRect(x, card_y, card_w, 28, 4, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(PDF_BOLD, 8)
        c.drawCentredString(x + card_w / 2, card_y + 19, system)
        c.setFont(PDF_REGULAR, 6)
        c.drawCentredString(x + card_w / 2, card_y + 8,
                            "NGHIÊM TRỌNG" if is_bad else "BÌNH THƯỜNG")

    # Show the affected directions directly beneath each red system card.
    # Keep the wording compact as requested: "Hướng HKG, SNG".
    for i, system in enumerate(SYSTEMS):
        if st.session_state.system_status[system] != "Nghiêm trọng":
            continue
        affected = st.session_state.affected_directions.get(system, [])
        direction_label = "Hướng " + (", ".join(affected) if affected else "chưa chọn")
        x = margin + i * (card_w + card_gap)
        c.setFillColor(red)
        c.setFont(PDF_BOLD, 5.8)
        c.drawString(x, card_y - 9, direction_label)

    # Three summary metrics, including the share of capacity still in service.
    utilization_pct = (total_effective / total_raw * 100.0) if total_raw > 0 else 0.0
    metrics = [
        ("TỔNG DUNG LƯỢNG", f"{total_raw:,.2f} GB"),
        ("DUNG LƯỢNG ĐANG KHAI THÁC", f"{total_effective:,.2f} GB ({utilization_pct:.0f}%)"),
        ("HỆ THỐNG BỊ ẢNH HƯỞNG", f"{bad_count}/6"),
    ]
    metric_y = page_h - 144
    metric_gap = 8
    metric_w = (page_w - 2 * margin - 2 * metric_gap) / 3
    for i, (label, value) in enumerate(metrics):
        x = margin + i * (metric_w + metric_gap)
        c.setFillColor(pale)
        c.roundRect(x, metric_y - 23, metric_w, 24, 4, fill=1, stroke=0)
        c.setFillColor(gray)
        c.setFont(PDF_BOLD, 6.5)
        c.drawString(x + 7, metric_y - 8, label)
        c.setFillColor(navy)
        c.setFont(PDF_BOLD, 9)
        c.drawRightString(x + metric_w - 7, metric_y - 18, value)

    # Four charts in a balanced 2x2 grid on the same single-page A4 report.
    figures = create_analysis_figures(df_effective)
    try:
        chart_y = 24
        chart_gap_x = 10
        chart_gap_y = 6
        chart_top = metric_y - 34
        chart_area_h = chart_top - chart_y
        chart_w = (page_w - 2 * margin - chart_gap_x) / 2
        chart_h = (chart_area_h - chart_gap_y) / 2
        chart_positions = [
            (margin, chart_y + chart_h + chart_gap_y, chart_w, chart_h),
            (margin + chart_w + chart_gap_x, chart_y + chart_h + chart_gap_y, chart_w, chart_h),
            (margin, chart_y, chart_w, chart_h),
            (margin + chart_w + chart_gap_x, chart_y, chart_w, chart_h),
        ]
        for fig, (x, y, w, h) in zip(figures, chart_positions):
            image_buf = BytesIO()
            fig.savefig(image_buf, format="png", dpi=150, bbox_inches="tight",
                        facecolor="white", pad_inches=0.08)
            image_buf.seek(0)
            c.drawImage(
                ImageReader(image_buf), x, y, width=w, height=h,
                preserveAspectRatio=True, anchor="c", mask="auto",
            )
            image_buf.close()
    finally:
        for fig in figures:
            plt.close(fig)

    # Short note, no second/third pages or detail tables.
    c.setFillColor(gray)
    c.setFont(PDF_REGULAR, 5.8)
    c.drawString(
        margin, 10,
        "Đơn vị: GB | Dung lượng đang khai thác đã loại trừ các hướng được chọn ảnh hưởng bởi sự cố.",
    )
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


# ---------------- Sidebar: administration and data entry ----------------
with st.sidebar:
    st.markdown("## ⚙️ Quản trị hệ thống")
    st.caption(f"Đăng nhập: **{st.session_state.current_username}** · Vai trò: **{st.session_state.user_role}**")
    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.current_username = None
        st.rerun()
    st.divider()

    if st.session_state.user_role == "admin":
        st.markdown("### 👥 Quản lý người dùng")
        with st.expander("Tạo tài khoản mới", expanded=False):
            with st.form("create_viewer_account_form", clear_on_submit=True):
                new_username = st.text_input("Tên đăng nhập", key="new_viewer_username")
                new_password = st.text_input("Mật khẩu (tối thiểu 8 ký tự)", type="password", key="new_viewer_password")
                new_duration = st.selectbox("Thời hạn sử dụng", list(DURATION_OPTIONS.keys()), key="new_viewer_duration")
                create_submitted = st.form_submit_button("Tạo tài khoản Viewer", type="primary", use_container_width=True)
                if create_submitted:
                    ok, message = create_viewer_account(new_username, new_password, new_duration)
                    (st.success if ok else st.error)(message)
                    if ok:
                        st.session_state.show_user_list = True
                        st.rerun()

        with st.expander("Danh sách người dùng", expanded=st.session_state.get("show_user_list", False)):
            st.caption("Thời hạn hiển thị theo múi giờ Việt Nam (GMT+7). Tài khoản hết hạn sẽ không thể đăng nhập.")
            st.dataframe(list_managed_users(), use_container_width=True, hide_index=True)

        with st.expander("Khóa / Xóa / Gia hạn tài khoản", expanded=False):
            managed_df = list_managed_users()
            admin_user, _ = get_admin_credentials()
            selectable = [u for u in managed_df["Tên đăng nhập"].tolist()
                          if not admin_user or u.casefold() != admin_user.casefold()]
            selectable = [u for u in selectable if u]
            if not selectable:
                st.info("Chưa có tài khoản Viewer để quản lý.")
            else:
                selected_user = st.selectbox("Chọn tài khoản Viewer", selectable, key="manage_selected_user")
                extend_duration = st.selectbox("Thời gian gia hạn", list(DURATION_OPTIONS.keys()), key="manage_extend_duration")
                action_cols = st.columns(2)
                with action_cols[0]:
                    if st.button("Gia hạn tài khoản", use_container_width=True):
                        ok, message = manage_viewer(selected_user, "extend", extend_duration)
                        (st.success if ok else st.error)(message)
                        st.session_state.show_user_list = True
                        st.rerun()
                    if st.button("Khóa tài khoản", use_container_width=True):
                        ok, message = manage_viewer(selected_user, "suspend")
                        (st.success if ok else st.error)(message)
                        st.session_state.show_user_list = True
                        st.rerun()
                with action_cols[1]:
                    if st.button("Mở khóa tài khoản", use_container_width=True):
                        ok, message = manage_viewer(selected_user, "activate")
                        (st.success if ok else st.error)(message)
                        st.session_state.show_user_list = True
                        st.rerun()
                    if st.button("Xóa tài khoản", type="secondary", use_container_width=True):
                        ok, message = manage_viewer(selected_user, "delete")
                        (st.success if ok else st.error)(message)
                        st.session_state.show_user_list = True
                        st.rerun()

        st.divider()
        st.caption("Cập nhật số liệu dung lượng sử dụng thực tế (GB).")
        st.markdown(f"**Cập nhật gần nhất:**  \n{st.session_state.last_updated.strftime('%d/%m/%Y %H:%M:%S')}")
        with st.expander("Nhập dung lượng sử dụng (GB)", expanded=True):
            st.caption("Chỉ nhập tại các tuyến có kết nối (ô x trong bảng gốc). Ô không có kết nối được cố định bằng 0.")
            with st.form("capacity_form"):
                edited = {}
                for s, d in VALID_ROUTES:
                    key = f"{s}|{d}"
                    edited[key] = st.number_input(
                        f"{s} → {d} (GB)", min_value=0.0,
                        value=float(st.session_state.capacities.get(key, 0.0)),
                        step=1.0, format="%.2f", key=f"input_{s}_{d}",
                    )
                save_capacity = st.form_submit_button("Lưu số liệu dung lượng", type="primary", use_container_width=True)
                if save_capacity:
                    st.session_state.capacities.update(edited)
                    mark_updated()
                    st.success("Đã cập nhật số liệu dung lượng.")
                    st.rerun()

        with st.expander("Cập nhật trạng thái hệ thống", expanded=True):
            new_status = {}
            new_affected = {}
            for s in SYSTEMS:
                st.markdown(f"**{s} — {SYSTEM_TYPE[s]}**")
                new_status[s] = st.radio(
                    f"Trạng thái {s}", ["Bình thường", "Nghiêm trọng"],
                    index=0 if st.session_state.system_status[s] == "Bình thường" else 1,
                    horizontal=True, key=f"status_{s}", label_visibility="collapsed",
                )
                if new_status[s] == "Nghiêm trọng":
                    available = [d for d in DIRECTIONS if ROUTE_EXISTS[s][d]]
                    prior = [d for d in st.session_state.affected_directions.get(s, []) if d in available]
                    new_affected[s] = st.multiselect(
                        f"Hướng bị ảnh hưởng — {s}", options=available, default=prior,
                        key=f"affected_{s}", help="Chỉ chọn hướng có kết nối theo bảng gốc. Có thể chọn nhiều hướng.",
                    )
                else:
                    new_affected[s] = []
                    st.session_state.affected_directions[s] = []
                    st.session_state[f"affected_{s}"] = []
                st.divider()
            if st.button("Lưu trạng thái sự cố", type="primary", use_container_width=True):
                st.session_state.system_status.update(new_status)
                st.session_state.affected_directions.update(new_affected)
                mark_updated()
                st.success("Đã cập nhật trạng thái hệ thống.")
                st.rerun()
    else:
        st.info("Tài khoản Viewer chỉ có quyền xem dashboard và xuất báo cáo. Các chức năng nhập liệu và quản lý người dùng chỉ dành cho admin.")

# ---------------- Main dashboard ----------------
st.markdown('<div class="main-title">🌐 BÁO CÁO HIỆN TRẠNG HOẠT ĐỘNG MẠNG CÁP QUỐC TẾ</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Dashboard giám sát trạng thái và phân tích dung lượng khai thác</div>', unsafe_allow_html=True)

df_raw = get_raw_df()
df_effective = get_effective_df()
total_raw = df_raw["Dung lượng nhập (GB)"].sum()
total_effective = df_effective["Dung lượng đang khai thác (GB)"].sum()
critical_count = sum(st.session_state.system_status[s] == "Nghiêm trọng" for s in SYSTEMS)
top1, top2 = st.columns(2)
utilization_ratio = float(total_effective / total_raw) if total_raw > 0 else 0.0
utilization_ratio = min(max(utilization_ratio, 0.0), 1.0)
with top1:
    st.metric("Đang khai thác", f"{total_effective:,.2f} GB")
    progress_html = (
        '<div style="height:12px;width:100%;background:#e5e7eb;border-radius:999px;overflow:hidden;margin-top:-2px;">'
        f'<div style="height:100%;width:{utilization_ratio * 100:.2f}%;background:#16a34a;border-radius:999px;"></div>'
        '</div>'
        f'<div style="font-size:.8rem;color:#64748b;margin-top:5px;">Dung lượng đang khai thác / tổng dung lượng: {utilization_ratio * 100:.0f}%</div>'
    )
    st.markdown(progress_html, unsafe_allow_html=True)
top2.metric("Hệ thống bị ảnh hưởng", f"{critical_count}/6")

st.caption(f"Ngày cập nhật số liệu gần nhất: **{st.session_state.last_updated.strftime('%d/%m/%Y %H:%M:%S')}**")

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
    # Show entered capacities on valid routes, independently of incident exclusions.
    matrix = pd.DataFrame(
        {
            d: [
                float(st.session_state.capacities.get(f"{system}|{d}", 0.0))
                if ROUTE_EXISTS[system][d] else 0.0
                for system in SYSTEMS
            ]
            for d in DIRECTIONS
        },
        index=SYSTEMS,
    )
    matrix.insert(0, "Loại cáp", [SYSTEM_TYPE[system] for system in SYSTEMS])
    matrix.index.name = "Hệ thống"
    st.dataframe(matrix.style.format({d: "{:,.2f} GB" for d in DIRECTIONS}), use_container_width=True)

st.markdown("## Chức năng 2 — Dashboard phân tích dung lượng")
tab1, tab2, tab3 = st.tabs([
    "So sánh 6 hệ thống",
    "Tỷ trọng theo 5 hướng",
    "Cơ cấu toàn mạng",
])

system_totals = df_effective.groupby("Hệ thống")["Dung lượng đang khai thác (GB)"].sum().reindex(SYSTEMS, fill_value=0).reset_index()
system_totals.columns = ["Hệ thống", "Dung lượng đang khai thác (GB)"]
with tab1:
    figures = create_analysis_figures(df_effective)
    try:
        st.pyplot(figures[0], use_container_width=True)
    finally:
        plt.close(figures[0])
        plt.close(figures[1])
        plt.close(figures[2])

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
    figures = create_analysis_figures(df_effective)
    try:
        st.pyplot(figures[1], use_container_width=True)
        st.markdown("### Tỷ trọng dung lượng hiện tại so với thời điểm an toàn theo hướng kết nối")
        st.pyplot(figures[3], use_container_width=True)
    finally:
        for chart in figures:
            plt.close(chart)
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
        figures = create_analysis_figures(df_effective)
        try:
            st.pyplot(figures[2], use_container_width=True)
        finally:
            for chart in figures:
                plt.close(chart)
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
