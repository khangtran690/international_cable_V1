import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import sqlite3
import secrets
import time

# psycopg is required for PostgreSQL. Keep the app error actionable if the
# deployment environment has not installed requirements.txt yet.
try:
    import psycopg
    from psycopg.types.json import Jsonb
    PSYCOPG_IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    psycopg = None
    Jsonb = None
    PSYCOPG_IMPORT_ERROR = exc
import calendar
from contextlib import contextmanager
from io import BytesIO
import hashlib
import hmac
import base64
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak
import os
import json
import zipfile
import tempfile
from pathlib import Path
from html import escape as html_escape

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

if PSYCOPG_IMPORT_ERROR is not None:
    st.error("Ứng dụng chưa cài thư viện PostgreSQL `psycopg`. Hãy thêm/cập nhật file `requirements.txt` ở thư mục gốc repository (hoặc cùng thư mục với app.py), bảo đảm có dòng `psycopg[binary]>=3.2,<4`, sau đó khởi động lại/reboot ứng dụng trên Streamlit Cloud.")
    st.stop()

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

# PostgreSQL stores the latest committed application-input snapshot.
# Configure these values in .streamlit/secrets.toml locally and in Streamlit Cloud
# -> App settings -> Secrets. See setup instructions delivered with this code.
def postgres_connect():
    try:
        cfg = st.secrets["postgres"]
        return psycopg.connect(
            host=str(cfg["host"]),
            port=int(cfg.get("port", 5432)),
            dbname=str(cfg["dbname"]),
            user=str(cfg["user"]),
            password=str(cfg["password"]),
            sslmode=str(cfg.get("sslmode", "require")),
            connect_timeout=10,
        )
    except Exception as exc:
        raise RuntimeError(
            "Chưa cấu hình PostgreSQL. Hãy khai báo mục [postgres] trong "
            ".streamlit/secrets.toml hoặc Streamlit Cloud > Settings > Secrets. "
            "Kiểm tra thêm dependency psycopg[binary]."
        ) from exc


@st.cache_resource(show_spinner=False)
def init_postgres_state_db():
    with postgres_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                id SMALLINT PRIMARY KEY CHECK (id = 1),
                capacities JSONB NOT NULL,
                system_status JSONB NOT NULL,
                affected_directions JSONB NOT NULL,
                incident_info JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_updated TIMESTAMPTZ NOT NULL,
                updated_by TEXT NOT NULL,
                saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        # Backward-compatible migration for databases created by earlier app versions.
        conn.execute("""
            ALTER TABLE app_state
            ADD COLUMN IF NOT EXISTS incident_info JSONB NOT NULL DEFAULT '{}'::jsonb
        """)
        # Append-only audit trail. Keep the migration idempotent so an older or
        # partially-created table is repaired instead of breaking retrieval.
        _ensure_history_log_events_schema(conn)


def _ensure_history_log_events_schema(conn):
    """Create/migrate the audit table safely for both new and existing databases."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history_log_events (
            id BIGSERIAL PRIMARY KEY,
            activities TEXT,
            old_value TEXT,
            new_value TEXT,
            occurred_at TIMESTAMPTZ DEFAULT NOW(),
            changed_by TEXT DEFAULT 'system'
        )
    """)
    # CREATE TABLE IF NOT EXISTS does not repair an existing table. Add each
    # expected column independently to support deployments with partial schemas.
    conn.execute("ALTER TABLE history_log_events ADD COLUMN IF NOT EXISTS id BIGSERIAL")
    conn.execute("ALTER TABLE history_log_events ADD COLUMN IF NOT EXISTS activities TEXT")
    conn.execute("ALTER TABLE history_log_events ADD COLUMN IF NOT EXISTS old_value TEXT")
    conn.execute("ALTER TABLE history_log_events ADD COLUMN IF NOT EXISTS new_value TEXT")
    conn.execute("ALTER TABLE history_log_events ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ DEFAULT NOW()")
    conn.execute("ALTER TABLE history_log_events ADD COLUMN IF NOT EXISTS changed_by TEXT DEFAULT 'system'")
    conn.execute("UPDATE history_log_events SET occurred_at = NOW() WHERE occurred_at IS NULL")
    conn.execute("UPDATE history_log_events SET activities = '(Không xác định)' WHERE activities IS NULL")
    conn.execute("UPDATE history_log_events SET changed_by = 'system' WHERE changed_by IS NULL")
    conn.execute("ALTER TABLE history_log_events ALTER COLUMN activities SET NOT NULL")
    conn.execute("ALTER TABLE history_log_events ALTER COLUMN occurred_at SET DEFAULT NOW()")
    conn.execute("ALTER TABLE history_log_events ALTER COLUMN occurred_at SET NOT NULL")
    conn.execute("ALTER TABLE history_log_events ALTER COLUMN changed_by SET DEFAULT 'system'")
    conn.execute("ALTER TABLE history_log_events ALTER COLUMN changed_by SET NOT NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_history_log_events_id ON history_log_events (id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_log_events_occurred_at ON history_log_events (occurred_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_log_events_activities ON history_log_events (activities)")


def write_history_log_event(activities, old_value=None, new_value=None, changed_by=None, occurred_at=None):
    """Append one audit event to PostgreSQL using an explicit UTC timestamp."""
    event_time = occurred_at or datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    event_time = event_time.astimezone(timezone.utc)
    actor = changed_by or st.session_state.get("current_username") or "system"
    with postgres_connect() as conn:
        conn.execute(
            """
            INSERT INTO history_log_events
                (activities, old_value, new_value, occurred_at, changed_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (str(activities), old_value, new_value, event_time, str(actor)),
        )


def normalize_incident_store(saved_incident_info, saved_affected):
    """Migrate legacy {system: {fields}} records to {system: [{id, fields}]}."""
    normalized = {}
    for system in SYSTEMS:
        raw = (saved_incident_info or {}).get(system, {})
        if isinstance(raw, dict):
            if raw:
                legacy = dict(raw)
                legacy.setdefault("id", secrets.token_hex(6))
                legacy.setdefault(
                    "affected_directions",
                    list((saved_affected or {}).get(system, [])),
                )
                normalized[system] = [legacy]
            else:
                normalized[system] = []
        elif isinstance(raw, list):
            records = []
            for item in raw:
                if isinstance(item, dict):
                    record = dict(item)
                    record.setdefault("id", secrets.token_hex(6))
                    record.setdefault(
                        "affected_directions",
                        list((saved_affected or {}).get(system, [])),
                    )
                    records.append(record)
            normalized[system] = records
        else:
            normalized[system] = []
    return normalized


def load_app_state_from_postgres():
    with postgres_connect() as conn:
        row = conn.execute(
            "SELECT capacities, system_status, affected_directions, last_updated, incident_info "
            "FROM app_state WHERE id = 1"
        ).fetchone()
    if not row:
        return False

    saved_capacities, saved_status, saved_affected, saved_updated, saved_incident_info = row
    # Only load keys known by this app version; this also protects against stale data.
    defaults = {f"{system}|{direction}": 0.0 for system, direction in VALID_ROUTES}
    defaults.update({k: float(v) for k, v in (saved_capacities or {}).items() if k in defaults})
    st.session_state.capacities = defaults
    st.session_state.system_status = {
        system: (saved_status or {}).get(system, "Bình thường")
        for system in SYSTEMS
    }
    st.session_state.affected_directions = {
        system: [d for d in (saved_affected or {}).get(system, []) if d in DIRECTIONS and ROUTE_EXISTS[system][d]]
        for system in SYSTEMS
    }
    st.session_state.last_updated = saved_updated.astimezone(VIETNAM_TZ)
    st.session_state.incident_info = normalize_incident_store(
        saved_incident_info, saved_affected
    )
    return True


def _audit_value(value):
    """Return a stable, readable representation for an audit cell."""
    if value is None or value == "":
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, bool):
        return "Có" if value else "Không"
    return str(value)


def _flatten_audit_snapshot(snapshot):
    """Flatten the persisted application state into human-readable audit fields."""
    result = {}
    for key, value in (snapshot.get("capacities") or {}).items():
        system, _, direction = str(key).partition("|")
        result[f"Dung lượng / {system} → {direction}"] = value
    for system, value in (snapshot.get("system_status") or {}).items():
        result[f"Trạng thái hệ thống / {system}"] = value
    for system, value in (snapshot.get("affected_directions") or {}).items():
        result[f"Hướng kết nối bị ảnh hưởng / {system}"] = value
    for system, records in (snapshot.get("incident_info") or {}).items():
        if isinstance(records, dict):
            records = [records]
        for record in (records or []):
            if not isinstance(record, dict):
                continue
            incident_id = str(record.get("id", ""))
            for field, value in record.items():
                if field == "id":
                    continue
                result[f"Thông tin sự cố / {system} / {incident_id} / {field}"] = value
    return result


def save_app_state_to_postgres():
    last_updated = st.session_state.last_updated
    if last_updated.tzinfo is None:
        last_updated = last_updated.replace(tzinfo=VIETNAM_TZ)
    last_updated = last_updated.astimezone(timezone.utc)
    current_snapshot = {
        "capacities": dict(st.session_state.capacities),
        "system_status": dict(st.session_state.system_status),
        "affected_directions": dict(st.session_state.affected_directions),
        "incident_info": st.session_state.get("incident_info", {}),
    }
    username = str(st.session_state.current_username or "unknown")
    with postgres_connect() as conn:
        previous = conn.execute("""
            SELECT capacities, system_status, affected_directions, incident_info
            FROM app_state WHERE id = 1
        """).fetchone()

        # Record only actual field-level changes, in the same transaction as the save.
        if previous:
            old_snapshot = {
                "capacities": previous[0] or {},
                "system_status": previous[1] or {},
                "affected_directions": previous[2] or {},
                "incident_info": previous[3] or {},
            }
            old_flat = _flatten_audit_snapshot(old_snapshot)
            new_flat = _flatten_audit_snapshot(current_snapshot)
            for activity in sorted(set(old_flat) | set(new_flat)):
                old = old_flat.get(activity)
                new_value = new_flat.get(activity)
                if _audit_value(old) != _audit_value(new_value):
                    conn.execute("""
                        INSERT INTO history_log_events
                            (activities, old_value, new_value, occurred_at, changed_by)
                        VALUES (%s, %s, %s, NOW(), %s)
                    """, (activity, _audit_value(old), _audit_value(new_value), username))

        conn.execute("""
            INSERT INTO app_state
                (id, capacities, system_status, affected_directions, incident_info, last_updated, updated_by, saved_at)
            VALUES (1, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                capacities = EXCLUDED.capacities,
                system_status = EXCLUDED.system_status,
                affected_directions = EXCLUDED.affected_directions,
                incident_info = EXCLUDED.incident_info,
                last_updated = EXCLUDED.last_updated,
                updated_by = EXCLUDED.updated_by,
                saved_at = NOW()
        """, (
            Jsonb(current_snapshot["capacities"]),
            Jsonb(current_snapshot["system_status"]),
            Jsonb(current_snapshot["affected_directions"]),
            Jsonb(current_snapshot["incident_info"]),
            last_updated,
            username,
        ))


def query_history_log_events(start_date, end_date, activity_keyword=""):
    """Query events by GMT+7 calendar dates and an optional Activities keyword."""
    if not start_date or not end_date:
        raise ValueError("Vui lòng chọn đầy đủ ngày bắt đầu và ngày kết thúc.")
    if start_date > end_date:
        raise ValueError("Ngày bắt đầu không được lớn hơn ngày kết thúc.")

    start_local = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=VIETNAM_TZ)
    end_exclusive = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=VIETNAM_TZ)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_exclusive.astimezone(timezone.utc)
    keyword = (activity_keyword or "").strip()

    with postgres_connect() as conn:
        if keyword:
            rows = conn.execute("""
                SELECT activities, old_value, new_value, occurred_at
                FROM history_log_events
                WHERE occurred_at >= %s AND occurred_at < %s
                  AND COALESCE(activities, '') ILIKE %s
                ORDER BY occurred_at DESC, id DESC
            """, (start_utc, end_utc, f"%{keyword}%")).fetchall()
        else:
            rows = conn.execute("""
                SELECT activities, old_value, new_value, occurred_at
                FROM history_log_events
                WHERE occurred_at >= %s AND occurred_at < %s
                ORDER BY occurred_at DESC, id DESC
            """, (start_utc, end_utc)).fetchall()

    records = []
    for activities, old_value, new_value, occurred_at in rows:
        if occurred_at is None:
            continue
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        local_time = occurred_at.astimezone(VIETNAM_TZ)
        records.append({
            "Activities": activities or "",
            "Old value": old_value if old_value is not None else "",
            "New value": new_value if new_value is not None else "",
            "Time of Occurrence": local_time.strftime("%b %d, %Y %H:%M:%S (GMT+7)"),
            "_sort_time": local_time,
        })
    return pd.DataFrame(
        records,
        columns=["Activities", "Old value", "New value", "Time of Occurrence", "_sort_time"],
    )


# ---------------- Database backup / restore ----------------
BACKUP_FORMAT = "international-cable-dashboard-backup"
BACKUP_FORMAT_VERSION = 1
APP_DATA_SCHEMA_VERSION = 1


def _read_postgres_snapshot():
    """Read the complete persisted application snapshot from PostgreSQL."""
    with postgres_connect() as conn:
        row = conn.execute("""
            SELECT capacities, system_status, affected_directions, incident_info,
                   last_updated, updated_by, saved_at
            FROM app_state WHERE id = 1
        """).fetchone()
    if not row:
        raise RuntimeError(
            "PostgreSQL chưa có bản ghi dữ liệu (app_state). "
            "Hãy lưu số liệu/trạng thái ít nhất một lần trước khi backup."
        )
    return {
        "capacities": row[0] or {},
        "system_status": row[1] or {},
        "affected_directions": row[2] or {},
        "incident_info": row[3] or {},
        "last_updated": row[4].isoformat() if row[4] else None,
        "updated_by": row[5] or "unknown",
        "saved_at": row[6].isoformat() if row[6] else None,
    }


def _sqlite_backup_bytes():
    """Use SQLite's online backup API so the backup is transaction-consistent."""
    fd, temp_path = tempfile.mkstemp(prefix="user_accounts_backup_", suffix=".db")
    os.close(fd)
    try:
        with sqlite3.connect(DB_PATH, timeout=30) as source:
            with sqlite3.connect(temp_path) as target:
                source.backup(target)
        return Path(temp_path).read_bytes()
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def _current_app_config_snapshot():
    """Record code-defined settings for audit/compatibility checking."""
    return {
        "systems": list(SYSTEMS),
        "directions": list(DIRECTIONS),
        "system_type": dict(SYSTEM_TYPE),
        "route_exists": ROUTE_EXISTS,
        "duration_options": dict(DURATION_OPTIONS),
        "timezone": "Asia/Ho_Chi_Minh",
    }


def create_database_backup(created_by=None):
    """Create one ZIP containing PostgreSQL state and the complete SQLite user DB."""
    postgres_snapshot = _read_postgres_snapshot()
    sqlite_bytes = _sqlite_backup_bytes()
    manifest = {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "data_schema_version": APP_DATA_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": str(created_by or st.session_state.get("current_username") or "unknown"),
        "application": "International Cable Network Dashboard",
        "contents": ["postgres_app_state.json", "user_accounts.db", "app_config.json"],
        "note": (
            "Secrets/credentials are intentionally excluded. Configure them "
            "separately in Streamlit secrets."
        ),
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(
            "postgres_app_state.json",
            json.dumps(postgres_snapshot, ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "app_config.json",
            json.dumps(_current_app_config_snapshot(), ensure_ascii=False, indent=2),
        )
        archive.writestr("user_accounts.db", sqlite_bytes)
    output.seek(0)
    return output.getvalue()


def _validate_backup_archive(archive_bytes):
    """Validate archive structure, format, JSON and SQLite integrity before restore."""
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes), "r") as archive:
            names = set(archive.namelist())
            required = {
                "manifest.json", "postgres_app_state.json",
                "app_config.json", "user_accounts.db",
            }
            if not required.issubset(names):
                raise ValueError("File backup thiếu một hoặc nhiều thành phần bắt buộc.")
            if any(name.startswith(("/", "\\")) or ".." in Path(name).parts for name in names):
                raise ValueError("Cấu trúc đường dẫn trong file backup không hợp lệ.")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != BACKUP_FORMAT:
                raise ValueError("Đây không phải file backup của ứng dụng này.")
            if int(manifest.get("format_version", -1)) != BACKUP_FORMAT_VERSION:
                raise ValueError("Phiên bản định dạng backup không được hỗ trợ.")
            pg = json.loads(archive.read("postgres_app_state.json").decode("utf-8"))
            config = json.loads(archive.read("app_config.json").decode("utf-8"))
            sqlite_bytes = archive.read("user_accounts.db")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"File backup bị lỗi hoặc không đầy đủ: {exc}") from exc

    required_pg = {
        "capacities", "system_status", "affected_directions",
        "incident_info", "last_updated", "updated_by",
    }
    if not required_pg.issubset(pg):
        raise ValueError("Dữ liệu PostgreSQL trong file backup không đầy đủ.")
    if not isinstance(pg["capacities"], dict) or not isinstance(pg["system_status"], dict):
        raise ValueError("Cấu trúc dữ liệu dung lượng/trạng thái không hợp lệ.")
    if not isinstance(pg["affected_directions"], dict) or not isinstance(pg["incident_info"], dict):
        raise ValueError("Cấu trúc dữ liệu hướng ảnh hưởng/thông tin sự cố không hợp lệ.")
    if not pg.get("last_updated"):
        raise ValueError("File backup thiếu thời điểm cập nhật dữ liệu.")

    # Check the embedded SQLite database without touching the live user database.
    fd, temp_path = tempfile.mkstemp(prefix="user_accounts_validate_", suffix=".db")
    os.close(fd)
    try:
        Path(temp_path).write_bytes(sqlite_bytes)
        with sqlite3.connect(temp_path) as test_conn:
            integrity = test_conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError("Cơ sở dữ liệu tài khoản trong backup bị lỗi.")
            tables = {
                row[0] for row in test_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "users" not in tables:
                raise ValueError("Backup không có bảng users; từ chối khôi phục.")
            columns = {
                row[1] for row in test_conn.execute("PRAGMA table_info(users)").fetchall()
            }
            expected_columns = {
                "username", "password_hash", "password_salt", "role",
                "created_at", "expires_at", "status",
            }
            if not expected_columns.issubset(columns):
                raise ValueError("Cấu trúc bảng users trong backup không tương thích.")
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    return manifest, pg, config, sqlite_bytes


def _write_postgres_snapshot(snapshot):
    """Replace the singleton PostgreSQL application state in one transaction."""
    parsed_updated = datetime.fromisoformat(snapshot["last_updated"])
    if parsed_updated.tzinfo is None:
        parsed_updated = parsed_updated.replace(tzinfo=timezone.utc)
    saved_at = snapshot.get("saved_at")
    parsed_saved = datetime.fromisoformat(saved_at) if saved_at else datetime.now(timezone.utc)
    if parsed_saved.tzinfo is None:
        parsed_saved = parsed_saved.replace(tzinfo=timezone.utc)
    with postgres_connect() as conn:
        conn.execute("""
            INSERT INTO app_state
                (id, capacities, system_status, affected_directions, incident_info,
                 last_updated, updated_by, saved_at)
            VALUES (1, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                capacities = EXCLUDED.capacities,
                system_status = EXCLUDED.system_status,
                affected_directions = EXCLUDED.affected_directions,
                incident_info = EXCLUDED.incident_info,
                last_updated = EXCLUDED.last_updated,
                updated_by = EXCLUDED.updated_by,
                saved_at = EXCLUDED.saved_at
        """, (
            Jsonb(snapshot["capacities"]),
            Jsonb(snapshot["system_status"]),
            Jsonb(snapshot["affected_directions"]),
            Jsonb(snapshot["incident_info"]),
            parsed_updated,
            str(snapshot.get("updated_by") or "backup-restore"),
            parsed_saved,
        ))


def _replace_sqlite_database(sqlite_bytes):
    """Restore the validated SQLite database through SQLite's backup API."""
    fd, temp_path = tempfile.mkstemp(prefix="user_accounts_restore_", suffix=".db")
    os.close(fd)
    try:
        Path(temp_path).write_bytes(sqlite_bytes)
        with sqlite3.connect(temp_path) as source:
            with sqlite3.connect(DB_PATH, timeout=30) as target:
                source.backup(target)
                target.execute("PRAGMA integrity_check")
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def restore_database_backup(archive_bytes):
    """
    Restore both databases. A pre-restore snapshot is held in memory and used
    to roll back the first database if restoring the second one fails.
    """
    manifest, restored_pg, _config, restored_sqlite = _validate_backup_archive(archive_bytes)
    current_pg = _read_postgres_snapshot()
    current_sqlite = _sqlite_backup_bytes()

    # Restore PostgreSQL first; if SQLite restore fails, put PostgreSQL back.
    _write_postgres_snapshot(restored_pg)
    try:
        _replace_sqlite_database(restored_sqlite)
    except Exception:
        _write_postgres_snapshot(current_pg)
        raise

    # Clear cached/session copies so the next app run reads the restored snapshot.
    for key in (
        "capacities", "system_status", "affected_directions", "incident_info",
        "last_updated", "app_state_loaded", "incident_editor_records",
        "editing_incident_info", "editing_incident_system",
    ):
        st.session_state.pop(key, None)
    return manifest


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


@st.cache_resource(show_spinner=False)
def init_user_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Hoạt động',
                allowed_systems TEXT NOT NULL DEFAULT '[]'
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "allowed_systems" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN allowed_systems TEXT NOT NULL DEFAULT '[]'")



def _get_session_signing_secret():
    """Get a persistent random key used to sign browser-refresh session tokens."""
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_security_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            )
        """)
        row = conn.execute(
            "SELECT setting_value FROM app_security_settings WHERE setting_key = ?",
            ("session_signing_secret",),
        ).fetchone()
        if row:
            return str(row["setting_value"])
        secret = secrets.token_urlsafe(48)
        conn.execute(
            "INSERT INTO app_security_settings(setting_key, setting_value) VALUES (?, ?)",
            ("session_signing_secret", secret),
        )
        return secret


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_browser_session_token(username: str, role: str) -> str:
    """Create a signed, time-limited token so a hard browser refresh restores login."""
    payload = {
        "username": str(username),
        "role": str(role).lower(),
        "expires": int(time.time()) + 30 * 24 * 60 * 60,
    }
    encoded = _b64url_encode(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8"))
    signature = hmac.new(
        _get_session_signing_secret().encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return encoded + "." + _b64url_encode(signature)


def read_browser_session_token(token: str):
    """Verify token integrity/expiry and return its identity, otherwise None."""
    try:
        encoded, supplied_signature = str(token).split(".", 1)
        expected = hmac.new(
            _get_session_signing_secret().encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(supplied_signature)):
            return None
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
        if int(payload.get("expires", 0)) <= int(time.time()):
            return None
        username = str(payload.get("username") or "").strip()
        role = str(payload.get("role") or "").lower()
        if not username or role not in {"admin", "viewer", "operator"}:
            return None

        # Revalidate the identity and account state on every new browser session.
        admin_user, _ = get_admin_credentials()
        if role == "admin":
            if not admin_user or username.casefold() != admin_user.casefold():
                return None
            return {"username": admin_user, "role": "admin"}

        valid, _reason = managed_user_session_is_valid(username)
        if not valid:
            return None
        with db_connect() as conn:
            row = conn.execute(
                "SELECT role FROM users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        if not row or str(row["role"] or "viewer").lower() != role:
            return None
        return {"username": username, "role": role}
    except Exception:
        return None


def set_app_view_route(view: str, payload: dict | None = None):
    """Persist the active working view and its lightweight UI state in URL params.

    This is the common route mechanism for every modal/dialog in the app, not
    an Incident Information-only workaround. Only navigation/filter metadata is
    stored here; business data remains in the database.
    """
    allowed_views = {"incident_information", "incident_statistics", "event_logs"}
    if view not in allowed_views:
        return
    st.query_params["app_view"] = view
    if payload is not None:
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        ).decode("ascii").rstrip("=")
        st.query_params["app_view_state"] = encoded
    elif "app_view_state" in st.query_params:
        del st.query_params["app_view_state"]


def get_app_view_state() -> dict:
    """Decode the non-sensitive UI state associated with the active route."""
    raw = st.query_params.get("app_view_state", "")
    if not raw:
        return {}
    try:
        raw += "=" * (-len(raw) % 4)
        value = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def clear_app_view_route():
    """Clear the current app view route, including legacy dialog parameters."""
    for _key in (
        "app_view", "app_view_state",
        "app_dialog", "app_dialog_system", "app_dialog_mode",
    ):
        try:
            if _key in st.query_params:
                del st.query_params[_key]
        except Exception:
            pass


def set_incident_dialog_route(system: str, mode: str = "notice"):
    """Compatibility wrapper using the shared application view router."""
    if system not in SYSTEMS:
        return
    set_app_view_route("incident_information", {
        "system": system,
        "mode": "edit" if mode == "edit" else "notice",
    })
    # Keep legacy keys readable for old bookmarked URLs during migration.
    st.query_params["app_dialog"] = "incident_information"
    st.query_params["app_dialog_system"] = system
    st.query_params["app_dialog_mode"] = "edit" if mode == "edit" else "notice"


def clear_incident_dialog_route():
    """Backward-compatible alias: closing a dialog clears the common route."""
    clear_app_view_route()


def clear_browser_session_token():
    """Remove this app's authentication and transient dialog URL state."""
    try:
        if "app_session" in st.query_params:
            del st.query_params["app_session"]
    except Exception:
        pass
    clear_incident_dialog_route()
    try:
        if "app_tab" in st.query_params:
            del st.query_params["app_tab"]
    except Exception:
        pass


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
    role = str(user["role"] or "viewer").strip().lower()
    if role not in {"viewer", "operator"}:
        role = "viewer"
    return {"ok": True, "role": role, "username": user["username"], "message": ""}


def get_user_allowed_systems(username: str) -> list[str]:
    with db_connect() as conn:
        user = conn.execute(
            "SELECT role, allowed_systems FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    if not user or str(user["role"] or "").lower() != "operator":
        return []
    try:
        systems = json.loads(user["allowed_systems"] or "[]")
    except (TypeError, json.JSONDecodeError):
        systems = []
    return [system for system in systems if system in SYSTEMS]


def current_user_systems() -> list[str]:
    role = str(st.session_state.get("user_role") or "").lower()
    if role == "admin":
        return list(SYSTEMS)
    if role == "operator":
        return get_user_allowed_systems(st.session_state.get("current_username", ""))
    return []


def managed_user_session_is_valid(username: str) -> tuple[bool, str]:
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
            "Hệ thống được phép": "Tất cả",
            "Hạn sử dụng (GMT+7)": "Vĩnh viễn",
            "Trạng thái": "Hoạt động",
        })
    with db_connect() as conn:
        users = conn.execute("SELECT username, role, allowed_systems, expires_at, status FROM users ORDER BY username COLLATE NOCASE").fetchall()
    now = utc_now()
    for user in users:
        status = user["status"]
        if status == "Hoạt động" and now >= parse_timestamp(user["expires_at"]):
            status = "Hết hạn"
        role = str(user["role"] or "viewer").lower()
        try:
            assigned = json.loads(user["allowed_systems"] or "[]")
        except (TypeError, json.JSONDecodeError):
            assigned = []
        rows.append({
            "Tên đăng nhập": user["username"],
            "Vai trò": role,
            "Hệ thống được phép": ", ".join(x for x in assigned if x in SYSTEMS) if role == "operator" else "—",
            "Hạn sử dụng (GMT+7)": format_vietnam_time(user["expires_at"]),
            "Trạng thái": status,
        })
    return pd.DataFrame(rows, columns=["Tên đăng nhập", "Vai trò", "Hệ thống được phép", "Hạn sử dụng (GMT+7)", "Trạng thái"])


def create_user_account(username: str, password: str, duration_label: str, role: str, allowed_systems=None):
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
    role = str(role or "").strip().lower()
    if role not in {"viewer", "operator"}:
        return False, "Vui lòng chọn vai trò viewer hoặc operator."
    allowed_systems = [system for system in (allowed_systems or []) if system in SYSTEMS]
    if role == "operator" and not allowed_systems:
        return False, "Vui lòng chỉ định ít nhất một hệ thống cho tài khoản operator."
    if role == "viewer":
        allowed_systems = []
    now = utc_now()
    expires = add_duration(now.astimezone(VIETNAM_TZ), duration_label).astimezone(timezone.utc)
    password_hash, salt = hash_password(password)
    try:
        with db_connect() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, password_salt, role, created_at, expires_at, status, allowed_systems) VALUES (?, ?, ?, ?, ?, ?, 'Hoạt động', ?)",
                (username, password_hash, salt, role, now.isoformat(), expires.isoformat(), json.dumps(allowed_systems, ensure_ascii=False)),
            )
    except sqlite3.IntegrityError:
        return False, "Tên đăng nhập đã tồn tại (kể cả tài khoản đã xóa)."

    # User management is stored in SQLite, while Event Logs is stored
    # in PostgreSQL. Explicitly bridge the successful creation into the audit log.
    # Use the same timestamp as the SQLite created_at field to avoid boundary drift.
    try:
        write_history_log_event(
            activities=(
                "Quản lý người dùng / Tạo tài khoản Viewer"
                if role == "viewer"
                else "Quản lý người dùng / Tạo tài khoản Operator"
            ),
            old_value="Chưa tồn tại",
            new_value=(
                f"Tài khoản: {username}; Vai trò: {role}; "
                f"Hệ thống được phép: {', '.join(allowed_systems) if allowed_systems else 'Không áp dụng'}; "
                f"Thời hạn: {duration_label}; Hạn đến: {format_vietnam_time(expires.isoformat())}; "
                "Trạng thái: Hoạt động"
            ),
            changed_by=st.session_state.get("current_username") or admin_user or "admin",
            occurred_at=now,
        )
    except Exception as exc:
        # Do not silently lose the fact that account creation succeeded. Return
        # a warning in the admin UI while preserving the created account.
        print(f"Không ghi được Event Log Event tạo tài khoản '{username}': {exc}")
        return True, (
            f"Đã tạo tài khoản {role} '{username}', hạn đến "
            f"{format_vietnam_time(expires.isoformat())}. "
            "CẢNH BÁO: Chưa ghi được sự kiện vào Event Logs; "
            f"vui lòng kiểm tra kết nối PostgreSQL. Chi tiết: {exc}"
        )
    return True, f"Đã tạo tài khoản {role} '{username}', hạn đến {format_vietnam_time(expires.isoformat())}."


def backfill_viewer_creation_history_events():
    """Backfill Viewer accounts created before audit logging was added.

    This runs only when the administrator explicitly submits a History Log
    query. It is idempotent: an account already represented in the audit table
    is not inserted again.
    """
    with db_connect() as sqlite_conn:
        users = sqlite_conn.execute(
            "SELECT username, created_at, expires_at, status FROM users WHERE LOWER(role) = 'viewer'"
        ).fetchall()

    if not users:
        return 0

    activity_name = "Quản lý người dùng / Tạo tài khoản Viewer"
    with postgres_connect() as pg_conn:
        existing_rows = pg_conn.execute(
            """
            SELECT new_value
            FROM history_log_events
            WHERE activities = %s
            """,
            (activity_name,),
        ).fetchall()
        existing_usernames = set()
        for (new_value,) in existing_rows:
            text_value = str(new_value or "")
            marker = "Tài khoản: "
            if marker in text_value:
                existing_usernames.add(text_value.split(marker, 1)[1].split(";", 1)[0].strip().casefold())

        inserted = 0
        for user in users:
            username = str(user["username"])
            if username.casefold() in existing_usernames:
                continue
            try:
                created_at = parse_timestamp(user["created_at"])
            except Exception:
                # Do not fabricate a timestamp for malformed legacy records.
                continue
            new_value = (
                f"Tài khoản: {username}; Vai trò: Viewer; "
                f"Hạn đến: {format_vietnam_time(user['expires_at'])}; "
                f"Trạng thái hiện tại: {user['status']}"
            )
            pg_conn.execute(
                """
                INSERT INTO history_log_events
                    (activities, old_value, new_value, occurred_at, changed_by)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    activity_name,
                    "Chưa có nhật ký trước phiên bản này",
                    new_value,
                    created_at,
                    "system (backfill dữ liệu tài khoản cũ)",
                ),
            )
            existing_usernames.add(username.casefold())
            inserted += 1
    return inserted


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

# Streamlit session_state belongs to a WebSocket session and can be recreated
# by a hard browser refresh. Restore authentication from our signed URL token.
if not st.session_state.get("authenticated", False):
    _browser_token = st.query_params.get("app_session")
    if _browser_token:
        _browser_identity = read_browser_session_token(_browser_token)
        if _browser_identity:
            st.session_state.authenticated = True
            st.session_state.user_role = _browser_identity["role"]
            st.session_state.current_username = _browser_identity["username"]
        else:
            clear_browser_session_token()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user_role" not in st.session_state:
    st.session_state.user_role = None
if "current_username" not in st.session_state:
    st.session_state.current_username = None
if "last_updated" not in st.session_state:
    st.session_state.last_updated = datetime.now(VIETNAM_TZ)
if "capacities" not in st.session_state:
    st.session_state.capacities = {f"{s}|{d}": 0.0 for s, d in VALID_ROUTES}
if "system_status" not in st.session_state:
    st.session_state.system_status = {s: "Bình thường" for s in SYSTEMS}
if "affected_directions" not in st.session_state:
    st.session_state.affected_directions = {s: [] for s in SYSTEMS}
if "incident_info" not in st.session_state:
    st.session_state.incident_info = {s: [] for s in SYSTEMS}
if "incident_editor_records" not in st.session_state:
    st.session_state.incident_editor_records = {}
if "editing_incident_info" not in st.session_state:
    st.session_state.editing_incident_info = False

# Keep incident data and editor drafts initialized independently for EVERY system.
# This also repairs partially populated session state created by older app versions.
for _system in SYSTEMS:
    st.session_state.incident_info.setdefault(_system, [])
    st.session_state.incident_editor_records.setdefault(_system, None)
st.session_state.incident_editor_records = {
    _system: _draft
    for _system, _draft in st.session_state.incident_editor_records.items()
    if _draft is not None
}
if "editing_incident_system" not in st.session_state:
    st.session_state.editing_incident_system = None


def login_page():
    st.markdown("""
    <style>
    .login-wrap {max-width:390px; margin:2vh auto .8rem; padding:1.1rem 1.3rem;
        background:#ffffff; border:1px solid #dce5ef; border-radius:16px;
        box-shadow:0 8px 24px rgba(20,48,80,.09); text-align:center;}
    .login-title {font-size:1.45rem; font-weight:800; color:#16324f;}
    .login-sub {color:#64748b; margin-top:.25rem; margin-bottom:.2rem; font-size:.9rem;}
    div[data-testid="stForm"] h3 {text-align:center !important; width:100%;}
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
        if st.session_state.pop("restore_login_notice", False):
            st.success("Khôi phục database thành công. Vui lòng đăng nhập lại để ứng dụng nạp dữ liệu từ bản backup.")
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
                    # Persist the login across browser Refresh / hard reload.
                    st.query_params["app_session"] = create_browser_session_token(
                        result["username"], result["role"]
                    )
                    st.rerun()
                else:
                    st.error(result["message"])

if not st.session_state.authenticated:
    login_page()
    st.stop()

# Re-check viewer permissions on every rerun so a suspended, deleted, or expired
# account cannot continue using an already-open session.
if str(st.session_state.get("user_role") or "").lower() in {"viewer", "operator"}:
    valid, reason = managed_user_session_is_valid(st.session_state.get("current_username", ""))
    if not valid:
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.current_username = None
        clear_browser_session_token()
        st.error(reason + " Vui lòng đăng nhập lại sau khi được quản trị viên xử lý.")
        login_page()
        st.stop()

# Load the last committed snapshot from PostgreSQL once per logged-in session.
# Any database/configuration failure is surfaced instead of silently using temporary data.
if not st.session_state.get("app_state_loaded", False):
    try:
        init_postgres_state_db()
        load_app_state_from_postgres()
        st.session_state.app_state_loaded = True
    except Exception as exc:
        st.error(f"Không thể kết nối hoặc đọc dữ liệu PostgreSQL: {exc}")
        st.info("Hãy cấu hình PostgreSQL theo hướng dẫn đi kèm mã nguồn, sau đó tải lại ứng dụng.")
        st.stop()

# ---------------- Styling ----------------
st.markdown("""
<style>
.block-container {padding-top:3.2rem; padding-bottom:2rem; max-width:1500px;}
.main-title {font-size:1.85rem; font-weight:850; color:#16324f; margin-bottom:0;}
.sub-title {color:#64748b; margin-top:.2rem; margin-bottom:1rem;}

.st-key-status_card_SJC2, .st-key-status_card_APG, .st-key-status_card_AAG,
.st-key-status_card_AAE-1, .st-key-status_card_VSTN, .st-key-status_card_CSC {
    border: 0 !important; border-radius: 14px !important; padding: 12px 14px !important;
    height: 150px !important; min-height: 150px !important;
    box-sizing: border-box !important; margin-bottom: 8px;
}
.status-card {padding:14px 16px; border-radius:14px; color:white; min-height:112px;
    box-shadow:0 5px 16px rgba(15,23,42,.10); margin-bottom:8px;}
.status-ok {background:linear-gradient(135deg,#16803c,#22a653);}
.status-bad {background:linear-gradient(135deg,#b91c1c,#ef4444);}
.status-name {font-size:1.15rem; font-weight:800;}
.status-type {font-size:.82rem; opacity:.92;}
.status-state {font-size:1rem; font-weight:700; margin-top:10px;}
.small-muted {color:#64748b; font-size:.85rem;}
.metric-box {background:#f3f7fb; border:1px solid #e0eaf4; border-radius:12px; padding:12px 15px;}

/* Đồng bộ toàn bộ nút lệnh bằng xanh blue nhạt theo sắc xanh của biểu tượng 🌐. */
.stButton > button,
.stDownloadButton > button,
[data-testid="stFormSubmitButton"] > button,
[data-testid^="stBaseButton-"],
button[kind="primary"],
button[kind="secondary"] {
    background: #55B8E8 !important;
    background-color: #55B8E8 !important;
    background-image: none !important;
    color: #ffffff !important;
    border: 1px solid #55B8E8 !important;
    border-radius: 0.5rem !important;
    font-weight: 600 !important;
    transition: background-color .18s ease, border-color .18s ease,
                box-shadow .18s ease, transform .18s ease !important;
}
.stButton > button:hover,
.stDownloadButton > button:hover,
[data-testid="stFormSubmitButton"] > button:hover,
[data-testid^="stBaseButton-"]:hover,
button[kind="primary"]:hover,
button[kind="secondary"]:hover {
    background: #369FD6 !important;
    background-color: #369FD6 !important;
    border-color: #369FD6 !important;
    color: #ffffff !important;
    box-shadow: 0 3px 10px rgba(15, 23, 42, .16) !important;
    transform: translateY(-1px);
}
.stButton > button:active,
.stDownloadButton > button:active,
[data-testid="stFormSubmitButton"] > button:active,
[data-testid^="stBaseButton-"]:active,
button[kind="primary"]:active,
button[kind="secondary"]:active {
    background: #2389C2 !important;
    background-color: #2389C2 !important;
    border-color: #2389C2 !important;
    color: #ffffff !important;
    box-shadow: none !important;
    transform: translateY(0);
}
.stButton > button:focus,
.stDownloadButton > button:focus,
[data-testid="stFormSubmitButton"] > button:focus,
[data-testid^="stBaseButton-"]:focus,
button[kind="primary"]:focus,
button[kind="secondary"]:focus {
    outline: 2px solid #2389C2 !important;
    outline-offset: 2px !important;
}
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

def get_effective_df(raw_df=None):
    """Build effective capacity in one pass over the route matrix."""
    df = raw_df.copy() if isinstance(raw_df, pd.DataFrame) else get_raw_df()
    excluded_flags = []
    effective_values = []
    statuses = st.session_state.system_status
    affected_by_system = st.session_state.affected_directions
    for system, direction, connected, raw_value in zip(
        df["Hệ thống"], df["Hướng"], df["Có kết nối"], df["Dung lượng nhập (GB)"]
    ):
        excluded = bool(
            connected
            and statuses.get(system) == "Nghiêm trọng"
            and direction in affected_by_system.get(system, [])
        )
        excluded_flags.append(excluded)
        effective_values.append(0.0 if excluded else float(raw_value))
    df["Dung lượng đang khai thác (GB)"] = effective_values
    df["Bị loại do sự cố"] = excluded_flags
    return df

def mark_updated():
    st.session_state.last_updated = datetime.now(VIETNAM_TZ)


# Fixed palette: the same system always has the same clearly distinguishable color.
SYSTEM_COLORS = {
    "SJC2": "#0072B2",
    "APG": "#E69F00",
    "AAG": "#009E73",
    "AAE-1": "#CC79A7",
    "VSTN": "#D55E00",
    "CSC": "#56B4E9",
}


def create_analysis_figures(df_effective, font_scale=1.0, dpi=130):
    """Build shared Matplotlib charts; font_scale adjusts chart text for PDF only."""
    plt.rcParams.update({
        "font.size": 10 * font_scale,
        "axes.titlesize": 12 * font_scale,
        "axes.titleweight": "bold",
        "axes.labelsize": 10 * font_scale,
        "xtick.labelsize": 9 * font_scale,
        "ytick.labelsize": 9 * font_scale,
        "legend.fontsize": 8 * font_scale,
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
    fig1, ax1 = plt.subplots(figsize=(10, 4.3), dpi=dpi)
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
        ax1.bar_label(bars, labels=[f"{v:,.0f}" for v in totals.values], padding=3, fontsize=8 * font_scale)
        max_val = max(float(totals.max()), 1.0)
        ax1.set_ylim(0, max_val * 1.18)
    else:
        ax1.text(0.5, 0.55, "Chưa có dữ liệu dung lượng > 0 GB",
                 transform=ax1.transAxes, ha="center", va="center", color="#64748B")
    fig1.tight_layout()

    # Chart 2: per-direction percentage mix, with consistent system colors.
    fig2, ax2 = plt.subplots(figsize=(10, 4.5), dpi=dpi)
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
                         ha="center", va="center", fontsize=8 * font_scale, color="white",
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
    fig3, ax3 = plt.subplots(figsize=(8.2, 4.5), dpi=dpi)
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
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
            pctdistance=0.78,
            wedgeprops=dict(width=0.42, edgecolor="white", linewidth=1.2),
            textprops=dict(color="white", fontsize=8 * font_scale, fontweight="bold"),
        )
        ax3.text(0, 0, f"{total_effective:,.0f} GB", ha="center", va="center",
                 fontsize=12 * font_scale, fontweight="bold", color="#16324F")
        ax3.legend(
            wedges,
            [f"{s}: {totals[s]:,.0f} GB" for s in nonzero.index],
            loc="center left", bbox_to_anchor=(0.98, 0.5), frameon=False,
            fontsize=8 * font_scale,
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
    fig4, ax4 = plt.subplots(figsize=(10, 4.5), dpi=dpi)
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
    ax4.set_title("Tỷ trọng dung lượng hiện tại so với thời điểm an toàn theo hướng kết nối", pad=14, fontsize=12 * font_scale, fontweight="bold")
    ax4.set_ylabel("Dung lượng (GB)", fontsize=10 * font_scale, fontweight="bold")
    ax4.set_xticks(x, DIRECTIONS)
    ax4.tick_params(axis="x", labelsize=9, pad=5)
    ax4.tick_params(axis="y", labelsize=8)
    ax4.set_ylim(0, max(max_capacity * 1.35, 1.0))
    ax4.grid(axis="y", alpha=0.20, linestyle="--")
    ax4.set_axisbelow(True)
    ax4.spines["top"].set_visible(False)
    ax4.spines["right"].set_visible(False)
    ax4.legend(ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.15), frameon=False, fontsize=9 * font_scale)
    label_offset = max(max_capacity * 0.025, 0.12)
    # Percentage labels use opaque white badges with dark text for legibility
    # against either bar color; capacity labels are larger and bold above bars.
    for bar, capacity in zip(baseline_bars, raw_direction_totals.to_numpy()):
        pct = 100 if capacity > 0 else 0
        if capacity > 0:
            ax4.text(bar.get_x() + bar.get_width()/2, capacity * 0.52, f"{pct}%",
                     ha="center", va="center", fontsize=9 * font_scale, color="#16324F", fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.22", facecolor="white", edgecolor="none", alpha=0.94))
        else:
            ax4.text(bar.get_x() + bar.get_width()/2, label_offset * 0.35, "0%",
                     ha="center", va="bottom", fontsize=9 * font_scale, color="#16324F", fontweight="bold")
        ax4.text(bar.get_x() + bar.get_width()/2, capacity + label_offset,
                 f"{capacity:,.0f}", ha="center", va="bottom", fontsize=8 * font_scale, fontweight="bold", color="#1F2937",
                 bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.90))
    for bar, capacity, pct in zip(current_bars, effective_direction_totals.to_numpy(), remaining_pct):
        if capacity > 0:
            ax4.text(bar.get_x() + bar.get_width()/2, capacity * 0.52, f"{pct:.0f}%",
                     ha="center", va="center", fontsize=9 * font_scale, color="#14532D", fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.22", facecolor="white", edgecolor="none", alpha=0.94))
        else:
            ax4.text(bar.get_x() + bar.get_width()/2, label_offset * 0.35, "0%",
                     ha="center", va="bottom", fontsize=9 * font_scale, color="#14532D", fontweight="bold")
        ax4.text(bar.get_x() + bar.get_width()/2, capacity + label_offset,
                 f"{capacity:,.0f}", ha="center", va="bottom", fontsize=8 * font_scale, fontweight="bold", color="#1F2937",
                 bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.90))
    fig4.tight_layout(rect=[0, 0.11, 1, 0.96])
    return [fig1, fig2, fig3, fig4]


@st.cache_data(show_spinner=False, max_entries=8)
def build_dashboard_chart_images(df_effective):
    """Render charts once per data snapshot and reuse their PNG images."""
    figures = create_analysis_figures(df_effective, dpi=105)
    images = []
    try:
        for figure in figures:
            image_buffer = BytesIO()
            figure.savefig(image_buffer, format="png", dpi=105,
                           facecolor="white", bbox_inches="tight")
            images.append(image_buffer.getvalue())
            image_buffer.close()
    finally:
        for figure in figures:
            plt.close(figure)
    return images


@st.cache_data(show_spinner=False, max_entries=8)
def build_pdf(df_effective, df_raw, system_status_snapshot, affected_directions_snapshot, report_date):
    """Create/cache a balanced, single-page landscape A4 PDF report."""
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
    bad_count = sum(system_status_snapshot.get(s) == "Nghiêm trọng" for s in SYSTEMS)

    # Header.
    c.setFillColor(navy)
    c.roundRect(margin, page_h - 55, page_w - 2 * margin, 38, 8, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(PDF_BOLD, 13 * 1.15)
    c.drawCentredString(page_w / 2, page_h - 33, "BÁO CÁO HIỆN TRẠNG HOẠT ĐỘNG MẠNG CÁP QUỐC TẾ")
    c.setFont(PDF_REGULAR, 7.5 * 1.15)
    c.drawCentredString(page_w / 2, page_h - 46, "Ngày báo cáo: " + report_date)

    # Compact status cards.
    section_y = page_h - 72
    c.setFillColor(ink)
    c.setFont(PDF_BOLD, 8 * 1.15)
    c.drawString(margin, section_y, "TRẠNG THÁI HỆ THỐNG")
    card_gap = 6
    card_w = (page_w - 2 * margin - card_gap * 5) / 6
    card_y = page_h - 116
    for i, system in enumerate(SYSTEMS):
        x = margin + i * (card_w + card_gap)
        is_bad = system_status_snapshot.get(system) == "Nghiêm trọng"
        c.setFillColor(red if is_bad else green)
        c.roundRect(x, card_y, card_w, 28, 4, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(PDF_BOLD, 8 * 1.15)
        c.drawCentredString(x + card_w / 2, card_y + 19, system)
        c.setFont(PDF_REGULAR, 6 * 1.15)
        c.drawCentredString(x + card_w / 2, card_y + 8,
                            "NGHIÊM TRỌNG" if is_bad else "BÌNH THƯỜNG")

    # Show the affected directions directly beneath each red system card.
    # Keep the wording compact as requested: "Hướng HKG, SNG".
    for i, system in enumerate(SYSTEMS):
        if system_status_snapshot.get(system) != "Nghiêm trọng":
            continue
        affected = affected_directions_snapshot.get(system, [])
        direction_label = "Hướng " + (", ".join(affected) if affected else "chưa chọn")
        x = margin + i * (card_w + card_gap)
        c.setFillColor(red)
        c.setFont(PDF_BOLD, 5.8 * 1.15)
        c.drawString(x, card_y - 9, direction_label)

    # Three summary metrics, including the share of capacity still in service.
    utilization_pct = (total_effective / total_raw * 100.0) if total_raw > 0 else 0.0
    metrics = [
        ("TỔNG DUNG LƯỢNG", f"{total_raw:,.0f} GB"),
        ("DUNG LƯỢNG ĐANG KHAI THÁC", f"{total_effective:,.0f} GB ({utilization_pct:.0f}%)"),
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
        c.setFont(PDF_BOLD, 6.5 * 1.15)
        c.drawString(x + 7, metric_y - 8, label)
        c.setFillColor(navy)
        c.setFont(PDF_BOLD, 9 * 1.15)
        c.drawRightString(x + metric_w - 7, metric_y - 18, value)

    # Four charts in a balanced 2x2 grid on the same single-page A4 report.
    figures = create_analysis_figures(df_effective, font_scale=1.15, dpi=90)
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
            fig.savefig(image_buf, format="png", dpi=90,
                        facecolor="white")
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
    c.setFont(PDF_REGULAR, 5.8 * 1.15)
    # Nghiêng chữ bằng shear transform để vẫn giữ font Unicode hiện có.
    c.saveState()
    c.transform(1, 0, 0.20, 1, 0, 0)
    c.drawString(
        margin, 10,
        "Ghi chú: Số liệu chỉ phục vụ công tác thống kê báo cáo.",
    )
    c.restoreState()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()




def build_incident_statistics_pdf(incidents, capacity_snapshot, generated_at):
    """Create/cache the incident-statistics PDF with only the five requested fields per incident."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=28,
        leftMargin=28,
        topMargin=26,
        bottomMargin=26,
        title="Thống kê sự cố mạng cáp quốc tế",
        author="International Cable Network Dashboard",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "IncidentReportTitle", parent=styles["Title"], fontName=PDF_BOLD,
        fontSize=15, leading=19, textColor=colors.HexColor("#16324F"),
        alignment=TA_CENTER, spaceAfter=5,
    )
    subtitle_style = ParagraphStyle(
        "IncidentReportSubtitle", parent=styles["Normal"], fontName=PDF_REGULAR,
        fontSize=8.5, leading=11, textColor=colors.HexColor("#64748B"),
        alignment=TA_CENTER, spaceAfter=13,
    )
    label_style = ParagraphStyle(
        "IncidentLabel", parent=styles["Normal"], fontName=PDF_BOLD,
        fontSize=8.5, leading=11, textColor=colors.HexColor("#1F2937"),
    )
    value_style = ParagraphStyle(
        "IncidentValue", parent=styles["Normal"], fontName=PDF_REGULAR,
        fontSize=8.5, leading=11, textColor=colors.HexColor("#1F2937"),
        wordWrap="CJK",
    )
    incident_heading_style = ParagraphStyle(
        "IncidentHeading", parent=styles["Heading3"], fontName=PDF_BOLD,
        fontSize=10.5, leading=14, textColor=colors.HexColor("#16324F"),
        spaceBefore=3, spaceAfter=5,
    )

    story = [
        Paragraph("THỐNG KÊ SỰ CỐ MẠNG CÁP QUỐC TẾ", title_style),
        Paragraph(
            "Ngày xuất báo cáo: " + generated_at
            + " (UTC+7) | Tổng số sự cố: " + str(len(incidents)),
            subtitle_style,
        ),
    ]

    # Include the tentative repair schedule between occurrence and
    # stable traffic restoration time in every incident block.
    incident_pdf_fields = [
        "Fault",
        "Affected Traffic",
        "Affected Capacity",
        "Time of Occurrence",
        "Repair Schedule (Tentative)",
        "Stable Traffic Restoration Time",
    ]

    def esc(value):
        value = str(value if value not in (None, "") else "TBD")
        return (value.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace("\\n", "<br/>"))

    for incident_index, item in enumerate(incidents, start=1):
        system = item["system"]
        record = item["record"]
        affected = [
            direction for direction in record.get("affected_directions", [])
            if direction in DIRECTIONS and ROUTE_EXISTS.get(system, {}).get(direction, False)
        ]
        display = {
            "Fault": str(record.get("Fault") or ""),
            "Affected Traffic": ", ".join(affected) if affected else "—",
            "Affected Capacity": f"{sum(float(capacity_snapshot.get(f'{system}|{d}', 0.0) or 0.0) for d in affected):,.0f} GB",
        }
        # Create a short, numbered heading from the incident's Fault description.
        # Keep the original Fault value unchanged in its dedicated detail row below.
        fault_text = " ".join(str(record.get("Fault") or "").split())
        short_title = fault_text.split(".")[0].split(";")[0].split("\n")[0].strip()
        if not short_title:
            short_title = f"Sự cố hệ thống {system}"
        elif len(short_title) > 85:
            short_title = short_title[:82].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."
        incident_heading = f"SỰ CỐ {incident_index:02d} – {system} | {short_title}"
        story.append(Paragraph(esc(incident_heading), incident_heading_style))

        rows = []

        for field in incident_pdf_fields:
            if field in INCIDENT_SCHEDULE_FIELDS:
                schedule_formatter = (
                    _format_incident_date
                    if field == "Repair Schedule (Tentative)"
                    else _format_incident_time
                )
                start_value = schedule_formatter(
                    record.get(_incident_range_start_field(field))
                )
                end_value = schedule_formatter(
                    record.get(_incident_range_end_field(field))
                )
                if start_value == "TBD" and end_value == "TBD":
                    value = "TBD"
                else:
                    value = f"Start: {start_value}  |  End: {end_value}"
            elif field in INCIDENT_TIME_FIELDS:
                value = _format_incident_time(record.get(field))
            elif field == "Fault":
                value = display.get(field) or "TBD"
            else:
                value = display.get(field) or "TBD"

            rows.append([
                Paragraph(esc(field), label_style),
                Paragraph(esc(value), value_style),
            ])

        table = Table(
            rows,
            colWidths=[doc.width * 0.39, doc.width * 0.61],
            repeatRows=0,
        )
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#7B8794")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F5F9")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)
        story.append(Spacer(1, 13))

    if not incidents:
        story.append(Paragraph("Không tìm thấy sự cố phù hợp với các tiêu chí lọc.", value_style))

    def footer(canvas_obj, document):
        canvas_obj.saveState()
        canvas_obj.setFont(PDF_REGULAR, 7)
        canvas_obj.setFillColor(colors.HexColor("#64748B"))
        # Nghiêng riêng dòng ghi chú, giữ nguyên font Unicode hiện có.
        canvas_obj.saveState()
        canvas_obj.transform(1, 0, 0.20, 1, 0, 0)
        canvas_obj.drawString(28, 13, "Ghi chú: Số liệu chỉ phục vụ công tác thống kê báo cáo.")
        canvas_obj.restoreState()
        canvas_obj.drawRightString(landscape(A4)[0] - 28, 13, f"Trang {document.page}")
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buffer.seek(0)
    return buffer.getvalue()


# ---------------- Sidebar: data entry and session controls ----------------
with st.sidebar:
    st.markdown("## ⚙️ Hệ thống")
    st.caption(f"Đăng nhập: **{st.session_state.current_username}** · Vai trò: **{st.session_state.user_role}**")
    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_role = None
        st.session_state.current_username = None
        clear_browser_session_token()
        st.rerun()
    st.divider()

    if st.session_state.user_role == "admin":
        with st.container(border=True):
            st.markdown("### 📊 Cập nhật số liệu")
            st.markdown(f"**Cập nhật gần nhất:**  \n{st.session_state.last_updated.strftime('%d/%m/%Y %H:%M:%S')} (UTC+7)")
            with st.expander("Nhập dung lượng sử dụng (GB)", expanded=True):
                with st.form("capacity_form"):
                    edited = {}
                    for s, d in VALID_ROUTES:
                        key = f"{s}|{d}"
                        edited[key] = st.number_input(
                            f"{s} → {d} (GB)", min_value=0.0,
                            value=float(st.session_state.capacities.get(key, 0.0)),
                            step=1.0, format="%.0f", key=f"input_{s}_{d}",
                        )
                    save_capacity = st.form_submit_button("Lưu số liệu", type="primary", use_container_width=True)
                    if save_capacity:
                        st.session_state.capacities.update(edited)
                        mark_updated()
                        try:
                            save_app_state_to_postgres()
                        except Exception as exc:
                            st.error(f"Không thể lưu dữ liệu vào PostgreSQL: {exc}")
                        else:
                            st.success("Đã lưu toàn bộ số liệu đầu vào vào PostgreSQL.")
                            st.rerun()

            with st.expander("Cập nhật trạng thái hệ thống", expanded=True):
                # Keep all status controls inside a form: changing a radio button or
                # affected direction only stages a value in the browser. The app
                # applies/persists it only after the explicit submit action.
                with st.form("system_status_form"):
                    new_status = {}
                    new_affected = {}
                    for s in SYSTEMS:
                        st.markdown(f"**{s} — {SYSTEM_TYPE[s]}**")
                        new_status[s] = st.radio(
                            f"Trạng thái {s}", ["Bình thường", "Nghiêm trọng"],
                            index=0 if st.session_state.system_status[s] == "Bình thường" else 1,
                            horizontal=True, key=f"status_{s}", label_visibility="collapsed",
                        )
                        available = [d for d in DIRECTIONS if ROUTE_EXISTS[s][d]]
                        prior = [d for d in st.session_state.affected_directions.get(s, []) if d in available]
                        selected_affected = st.multiselect(
                            f"Hướng bị ảnh hưởng — {s}", options=available, default=prior,
                            key=f"affected_{s}", help='Trạng thái ảnh hưởng này chỉ thay đổi sau khi click nút lệnh "Lưu trạng thái" bên dưới',
                        )
                        # Form controls do not rerun on selection changes, so keep
                        # this selector visible for every system. A normal system
                        # is saved with an empty affected-direction list.
                        new_affected[s] = selected_affected if new_status[s] == "Nghiêm trọng" else []
                        st.divider()

                    save_status = st.form_submit_button(
                        "Lưu trạng thái", type="primary", use_container_width=True
                    )

                if save_status:
                    st.session_state.system_status.update(new_status)
                    st.session_state.affected_directions.update(new_affected)
                    mark_updated()
                    try:
                        save_app_state_to_postgres()
                    except Exception as exc:
                        st.error(f"Không thể lưu dữ liệu vào PostgreSQL: {exc}")
                    else:
                        st.success("Đã lưu trạng thái và toàn bộ số liệu đầu vào vào PostgreSQL.")
                        st.rerun()
    elif str(st.session_state.user_role).lower() == "operator":
        with st.sidebar.container(border=True):
            st.markdown("### 📊 Cập nhật số liệu")
            editable_systems = current_user_systems()
            if not editable_systems:
                st.warning("Tài khoản operator chưa được admin chỉ định hệ thống thao tác.")
            else:
                st.markdown(f"**Hệ thống được phân quyền:** {', '.join(editable_systems)}")
                with st.expander("Nhập dung lượng sử dụng (GB)", expanded=True):
                    with st.form("operator_capacity_form"):
                        edited = {}
                        for system, direction in VALID_ROUTES:
                            if system not in editable_systems:
                                continue
                            key = f"{system}|{direction}"
                            edited[key] = st.number_input(
                                f"{system} → {direction} (GB)", min_value=0.0,
                                value=float(st.session_state.capacities.get(key, 0.0)),
                                step=1.0, format="%.0f", key=f"operator_input_{system}_{direction}",
                            )
                        save_capacity = st.form_submit_button("Lưu số liệu", type="primary", use_container_width=True)
                    if save_capacity:
                        # Apply only the authorized route keys, never the entire dataset.
                        st.session_state.capacities.update(edited)
                        mark_updated()
                        try:
                            save_app_state_to_postgres()
                        except Exception as exc:
                            st.error(f"Không thể lưu dữ liệu vào PostgreSQL: {exc}")
                        else:
                            st.success("Đã lưu số liệu của các hệ thống được phân quyền.")
                            st.rerun()
                with st.expander("Cập nhật trạng thái hệ thống", expanded=True):
                    with st.form("operator_system_status_form"):
                        new_status, new_affected = {}, {}
                        for system in editable_systems:
                            st.markdown(f"**{system} — {SYSTEM_TYPE[system]}**")
                            new_status[system] = st.radio(
                                f"Trạng thái {system}", ["Bình thường", "Nghiêm trọng"],
                                index=0 if st.session_state.system_status[system] == "Bình thường" else 1,
                                horizontal=True, key=f"operator_status_{system}", label_visibility="collapsed",
                            )
                            available = [d for d in DIRECTIONS if ROUTE_EXISTS[system][d]]
                            prior = [d for d in st.session_state.affected_directions.get(system, []) if d in available]
                            selected = st.multiselect(
                                f"Hướng bị ảnh hưởng — {system}", options=available, default=prior,
                                key=f"operator_affected_{system}",
                            )
                            new_affected[system] = selected if new_status[system] == "Nghiêm trọng" else []
                            st.divider()
                        save_status = st.form_submit_button("Lưu trạng thái", type="primary", use_container_width=True)
                    if save_status:
                        st.session_state.system_status.update(new_status)
                        st.session_state.affected_directions.update(new_affected)
                        mark_updated()
                        try:
                            save_app_state_to_postgres()
                        except Exception as exc:
                            st.error(f"Không thể lưu dữ liệu vào PostgreSQL: {exc}")
                        else:
                            st.success("Đã lưu trạng thái của các hệ thống được phân quyền.")
                            st.rerun()
        st.caption("Operator chỉ được thao tác các hệ thống do admin chỉ định; không có quyền Quản lý người dùng hoặc Backup/Restore.")
    else:
        st.info("Tài khoản Viewer chỉ có quyền xem dashboard và xuất báo cáo.")

# ---------------- Incident information (per critical system) ----------------
# Guarantee an independent incident list for every configured cable system.
# Existing records are retained only under their own system key.
for _system in SYSTEMS:
    _raw_records = st.session_state.incident_info.get(_system, [])
    if isinstance(_raw_records, dict):
        st.session_state.incident_info[_system] = [_raw_records] if _raw_records else []
    elif not isinstance(_raw_records, list):
        st.session_state.incident_info[_system] = []

INCIDENT_TEXT_FIELDS = [
    "Fault",
    "Location",
    "Fault Classification",
]
INCIDENT_OCCURRENCE_TIME_FIELD = "Time of Occurrence"
INCIDENT_SCHEDULE_FIELDS = [
    "Pre-Repair Power Reconfiguration Schedule (if applicable)",
    "Repair Schedule (Tentative)",
    "Post-Repair Power Reconfiguration Schedule (if applicable)",
]
INCIDENT_STABLE_TIME_FIELD = "Stable Traffic Restoration Time"

def _incident_range_start_field(field):
    return f"{field} - Start"

def _incident_range_end_field(field):
    return f"{field} - End"

# Schedule entries are stored as two independent timestamps. The original
# single timestamp field is still read as a legacy start value during migration.
INCIDENT_BASE_TIME_FIELDS = [INCIDENT_OCCURRENCE_TIME_FIELD] + [
    key
    for field in INCIDENT_SCHEDULE_FIELDS
    for key in (_incident_range_start_field(field), _incident_range_end_field(field))
]
INCIDENT_TIME_FIELDS = INCIDENT_BASE_TIME_FIELDS + [INCIDENT_STABLE_TIME_FIELD]
INCIDENT_OTHER_FIELDS = ["Possibility of Delay"]
INCIDENT_FIELD_ORDER = (
    INCIDENT_TEXT_FIELDS
    + ["Affected Traffic", "Affected Capacity", INCIDENT_OCCURRENCE_TIME_FIELD]
    + INCIDENT_SCHEDULE_FIELDS
    + INCIDENT_OTHER_FIELDS
    + [INCIDENT_STABLE_TIME_FIELD]
)

def _blank_incident_record(system, affected=None):
    record = {
        "id": secrets.token_hex(6),
        **{field: "" for field in INCIDENT_TEXT_FIELDS + INCIDENT_OTHER_FIELDS},
        **{field: None for field in INCIDENT_TIME_FIELDS},
        "affected_directions": list(
            affected if affected is not None
            else st.session_state.affected_directions.get(system, [])
        ),
    }
    return record

def _incident_records(system):
    """Return normalized list of incident records for the selected system."""
    raw = st.session_state.incident_info.get(system, [])
    if isinstance(raw, dict):
        raw = [raw] if raw else []
    records = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        record.setdefault("id", secrets.token_hex(6))
        record.setdefault(
            "affected_directions",
            list(st.session_state.affected_directions.get(system, [])),
        )
        for field in INCIDENT_TEXT_FIELDS + INCIDENT_OTHER_FIELDS:
            record[field] = str(record.get(field) or "")
        for field in INCIDENT_TIME_FIELDS:
            record.setdefault(field, None)
        for schedule_field in INCIDENT_SCHEDULE_FIELDS:
            legacy_value = record.get(schedule_field)
            start_field = _incident_range_start_field(schedule_field)
            end_field = _incident_range_end_field(schedule_field)
            if record.get(start_field) is None and legacy_value:
                record[start_field] = legacy_value
            record.setdefault(start_field, None)
            record.setdefault(end_field, None)
        records.append(record)
    return records

def _incident_default(system, incident=None):
    """Build display values and calculate traffic/capacity from this incident's affected directions."""
    current = incident or _blank_incident_record(system)
    result = {
        field: str(current.get(field) or "")
        for field in INCIDENT_TEXT_FIELDS + INCIDENT_OTHER_FIELDS
    }
    result.update({field: current.get(field) for field in INCIDENT_TIME_FIELDS})

    affected = [
        direction for direction in current.get("affected_directions", [])
        if direction in DIRECTIONS and ROUTE_EXISTS.get(system, {}).get(direction, False)
    ]
    result["Affected Traffic"] = ", ".join(affected) if affected else "—"

    affected_capacity = sum(
        float(st.session_state.capacities.get(f"{system}|{direction}", 0.0) or 0.0)
        for direction in affected
    )
    result["Affected Capacity"] = f"{affected_capacity:,.0f} GB"
    return result

def _parse_saved_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

def _format_incident_time(value):
    parsed = _parse_saved_datetime(value)
    return parsed.strftime("%b %d, %Y %H:%M") + " (UTC+7)" if parsed else "TBD"


def _format_incident_date(value):
    """Format schedule dates without exposing the time component."""
    parsed = _parse_saved_datetime(value)
    return parsed.strftime("%b %d, %Y") if parsed else "TBD"

def _incident_sort_key(record):
    parsed = _parse_saved_datetime(record.get("Time of Occurrence"))
    return parsed or datetime.max

def incident_information_notice(system):
    """Compact read-only incident notice shown to every account."""
    records = _incident_records(system)
    records = sorted(records, key=_incident_sort_key)

    st.markdown(
        """
        <style>
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) {
            width: min(500px, calc(100vw - 2rem)) !important;
            max-width: min(500px, calc(100vw - 2rem)) !important;
            max-height: 82vh !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            font-size: 90% !important;
        }
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) h1,
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) h2,
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) h3,
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) p,
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) label,
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) [data-testid="stMarkdownContainer"],
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) [data-testid="stWidgetLabel"],
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) button {
            font-size: 90% !important;
            line-height: 1.22 !important;
        }
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) [data-testid="stHorizontalBlock"] {
            gap: .35rem !important;
        }
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) [data-testid="stVerticalBlock"] {
            gap: .45rem !important;
        }
        div[data-testid="stDialog"]:has(.incident-notice-font-scope) [data-testid="stDialogContent"] {
            padding: 1rem 1.15rem !important;
            max-height: 78vh !important;
            overflow-y: auto !important;
        }
        </style>
        <span class="incident-notice-font-scope"></span>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(f"**{system} — Incident Details**")
    st.divider()

    if not records:
        st.info("No incident information has been entered.")
    for index, incident in enumerate(records, start=1):
        record = _incident_default(system, incident)
        if len(records) > 1:
            st.markdown(f"**Incident {index}**")

        # Render the notice as one compact static table instead of creating
        # two Streamlit layout blocks for every field. This substantially
        # reduces the number of frontend elements when opening an incident.
        notice_rows = []
        for field in INCIDENT_FIELD_ORDER:
            if field in INCIDENT_SCHEDULE_FIELDS:
                schedule_formatter = (
                    _format_incident_date
                    if field == "Repair Schedule (Tentative)"
                    else _format_incident_time
                )
                start_value = schedule_formatter(
                    record.get(_incident_range_start_field(field))
                )
                end_value = schedule_formatter(
                    record.get(_incident_range_end_field(field))
                )
                # When both schedule endpoints are TBD, show a single TBD label.
                # If only one endpoint is TBD, retain the Start/End distinction.
                if start_value == "TBD" and end_value == "TBD":
                    value = "TBD"
                else:
                    value = f"Start: {start_value}  |  End: {end_value}"
            elif field in INCIDENT_TIME_FIELDS:
                value = _format_incident_time(record.get(field))
            else:
                value = record.get(field) or "—"
            notice_rows.append((field, value))

        # Render only data rows: intentionally omit the former Field / Information header.
        table_rows_html = "".join(
            "<tr>"
            f"<td style='width:42%;font-weight:600;vertical-align:top;padding:7px 10px 7px 0;border-bottom:1px solid #e5e7eb;'>{html_escape(str(field))}</td>"
            f"<td style='vertical-align:top;padding:7px 0;border-bottom:1px solid #e5e7eb;overflow-wrap:anywhere;'>{html_escape(str(value))}</td>"
            "</tr>"
            for field, value in notice_rows
        )
        st.markdown(
            "<table style='width:100%;border-collapse:collapse;font-size:90%;'>"
            f"<tbody>{table_rows_html}</tbody></table>",
            unsafe_allow_html=True,
        )
        if index < len(records):
            st.divider()

    if (str(st.session_state.user_role).lower() == "admin" or
            (str(st.session_state.user_role).lower() == "operator" and system in current_user_systems())):
        st.divider()
        if st.button("Chỉnh sửa", type="primary", use_container_width=True):
            st.session_state.incident_editor_records[system] = [
                dict(record) for record in records
            ]
            st.session_state.incident_editor_session_id = secrets.token_hex(5)
            st.session_state.editing_incident_system = system
            st.session_state.editing_incident_info = True
            set_incident_dialog_route(system, mode="edit")
            # Keep the editor route in the URL as well as session state so a
            # browser refresh restores the editor instead of the dashboard.
            st.rerun(scope="fragment")

def incident_information_editor(system):
    """Admins and operators assigned to this system may edit incident information."""
    if not (str(st.session_state.user_role).lower() == "admin" or
            (str(st.session_state.user_role).lower() == "operator" and system in current_user_systems())):
        st.error("Bạn không có quyền chỉnh sửa hệ thống này.")
        return

    if system not in st.session_state.incident_editor_records:
        st.session_state.incident_editor_records[system] = [
            dict(record) for record in _incident_records(system)
        ]
    draft_records = st.session_state.incident_editor_records[system]
    edit_session_id = st.session_state.get("incident_editor_session_id", "edit")
    st.caption(f"System: {system}")
    st.markdown(
        """
        <style>
        div[data-testid="stDialog"] > div,
        div[data-testid="stDialog"] [role="dialog"] {
            width: 96vw !important;
            max-width: 96vw !important;
            height: auto !important;
            max-height: calc(100vh - 2rem) !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            overscroll-behavior: contain;
        }
        div[data-testid="stDialog"] [data-testid="stDialogContent"],
        div[data-testid="stDialog"] [data-testid="stVerticalBlock"] {
            min-height: 0 !important;
        }
        div[data-testid="stDialog"] [data-testid="stDialogContent"] {
            max-height: calc(100vh - 7rem) !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            overscroll-behavior: contain;
            scrollbar-gutter: stable;
        }
        div[data-testid="stDialog"] div[data-testid="stMarkdownContainer"] p {
            margin-bottom: .25rem;
        }
        @media (max-width: 768px) {
            div[data-testid="stDialog"] > div,
            div[data-testid="stDialog"] [role="dialog"] {
                width: 96vw !important;
                max-width: 96vw !important;
                max-height: calc(100vh - 1rem) !important;
            }
            div[data-testid="stDialog"] [data-testid="stDialogContent"] {
                max-height: calc(100vh - 6rem) !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    available_directions = [
        direction for direction in DIRECTIONS
        if ROUTE_EXISTS.get(system, {}).get(direction, False)
    ]
    if not draft_records:
        st.info("Chưa có thông tin sự cố. Nhấn 'Thêm sự cố' để nhập sự cố đầu tiên.")

    for index, incident in enumerate(draft_records):
        incident_id = incident["id"]
        widget_id = f"{edit_session_id}_{incident_id}"
        st.markdown(f"### Incident {index + 1}")
        remove_key = f"incident_remove_{system}_{widget_id}"
        remove_this = st.checkbox(
            "Loại bỏ sự cố này",
            value=bool(incident.get("_remove", False)),
            key=remove_key,
        )

        # Fixed labels; each incident has its own affected directions.
        for field in INCIDENT_TEXT_FIELDS:
            left, right = st.columns([0.43, 0.57])
            with left:
                st.markdown(f"**{field}**")
            with right:
                st.text_input(
                    field,
                    value=incident.get(field, ""),
                    key=f"incident_editor_{system}_{widget_id}_{field}",
                    label_visibility="collapsed",
                    placeholder="",
                )

        left, right = st.columns([0.43, 0.57])
        with left:
            st.markdown("**Affected Traffic**")
        with right:
            st.multiselect(
                "Affected Traffic",
                options=available_directions,
                default=[
                    d for d in incident.get("affected_directions", [])
                    if d in available_directions
                ],
                key=f"incident_editor_{system}_{widget_id}_affected",
                label_visibility="collapsed",
                help="Chọn các hướng lưu lượng bị ảnh hưởng bởi riêng sự cố này.",
            )

        affected_now = st.session_state.get(
            f"incident_editor_{system}_{widget_id}_affected",
            incident.get("affected_directions", []),
        )
        affected_capacity = sum(
            float(st.session_state.capacities.get(f"{system}|{direction}", 0.0) or 0.0)
            for direction in affected_now
        )
        left, right = st.columns([0.43, 0.57])
        with left:
            st.markdown("**Affected Capacity**")
        with right:
            st.write(f"{affected_capacity:,.0f} GB")

        st.divider()

        # Time of occurrence remains a single timestamp.
        field = INCIDENT_OCCURRENCE_TIME_FIELD
        left, right = st.columns([0.43, 0.57])
        with left:
            st.markdown(f"**{field} (UTC+7)**")
        with right:
            saved_dt = _parse_saved_datetime(incident.get(field))
            is_tbd = st.checkbox(
                "TBD",
                value=(saved_dt is None),
                key=f"incident_editor_{system}_{widget_id}_{field}_tbd",
            )
            if not is_tbd:
                date_col, time_col = st.columns([0.52, 0.48])
                with date_col:
                    st.date_input(
                        "Date (UTC+7)",
                        value=saved_dt.date() if saved_dt else datetime.now(VIETNAM_TZ).date(),
                        key=f"incident_editor_{system}_{widget_id}_{field}_date",
                    )
                with time_col:
                    st.time_input(
                        "Time (UTC+7)",
                        value=saved_dt.time().replace(second=0, microsecond=0)
                        if saved_dt else datetime.now(VIETNAM_TZ).time().replace(second=0, microsecond=0),
                        key=f"incident_editor_{system}_{widget_id}_{field}_time",
                    )

        # Each schedule has two timestamps controlled by one shared TBD checkbox.
        for schedule_field in INCIDENT_SCHEDULE_FIELDS:
            st.markdown(f"**{schedule_field} (UTC+7)**")
            start_field = _incident_range_start_field(schedule_field)
            end_field = _incident_range_end_field(schedule_field)
            saved_start = _parse_saved_datetime(incident.get(start_field))
            saved_end = _parse_saved_datetime(incident.get(end_field))
            schedule_tbd = st.checkbox(
                "TBD",
                value=(saved_start is None and saved_end is None),
                key=f"incident_editor_{system}_{widget_id}_{schedule_field}_tbd",
            )
            if not schedule_tbd:
                range_cols = st.columns(2)
                for range_label, range_field, saved_dt, range_col in (
                    ("Start", start_field, saved_start, range_cols[0]),
                    ("End", end_field, saved_end, range_cols[1]),
                ):
                    with range_col:
                        st.caption(f"{range_label} (UTC+7)")
                        st.date_input(
                            f"{range_label} Date (UTC+7)",
                            value=saved_dt.date() if saved_dt else datetime.now(VIETNAM_TZ).date(),
                            key=f"incident_editor_{system}_{widget_id}_{range_field}_date",
                        )
                        if schedule_field != "Repair Schedule (Tentative)":
                            st.time_input(
                                f"{range_label} Time (UTC+7)",
                                value=saved_dt.time().replace(second=0, microsecond=0)
                                if saved_dt else datetime.now(VIETNAM_TZ).time().replace(second=0, microsecond=0),
                                key=f"incident_editor_{system}_{widget_id}_{range_field}_time",
                            )
            st.divider()

        left, right = st.columns([0.43, 0.57])
        with left:
            st.markdown("**Possibility of Delay**")
        with right:
            st.text_input(
                "Possibility of Delay",
                value=incident.get("Possibility of Delay", ""),
                key=f"incident_editor_{system}_{widget_id}_Possibility of Delay",
                label_visibility="collapsed",
                placeholder="",
            )

        field = INCIDENT_STABLE_TIME_FIELD
        left, right = st.columns([0.43, 0.57])
        with left:
            st.markdown(f"**{field} (UTC+7)**")
        with right:
            saved_dt = _parse_saved_datetime(incident.get(field))
            is_tbd = st.checkbox(
                "TBD",
                value=(saved_dt is None),
                key=f"incident_editor_{system}_{widget_id}_{field}_tbd",
            )
            if not is_tbd:
                date_col, time_col = st.columns([0.52, 0.48])
                with date_col:
                    st.date_input(
                        "Date (UTC+7)",
                        value=saved_dt.date() if saved_dt else datetime.now(VIETNAM_TZ).date(),
                        key=f"incident_editor_{system}_{widget_id}_{field}_date",
                    )
                with time_col:
                    st.time_input(
                        "Time (UTC+7)",
                        value=saved_dt.time().replace(second=0, microsecond=0)
                        if saved_dt else datetime.now(VIETNAM_TZ).time().replace(second=0, microsecond=0),
                        key=f"incident_editor_{system}_{widget_id}_{field}_time",
                    )

        if index < len(draft_records):
            st.divider()

    # Add incident appends a separate draft card; existing entered values remain in keyed widgets.
    button_cols = st.columns([1, 1, 1])
    with button_cols[0]:
        if st.button("Lưu thông tin", type="primary", use_container_width=True):
            saved_records = []
            for incident in draft_records:
                incident_id = incident["id"]
                widget_id = f"{edit_session_id}_{incident_id}"
                if st.session_state.get(f"incident_remove_{system}_{widget_id}", False):
                    continue

                updated = {
                    "id": incident_id,
                    "affected_directions": st.session_state.get(
                        f"incident_editor_{system}_{widget_id}_affected",
                        incident.get("affected_directions", []),
                    ),
                }
                for field in INCIDENT_TEXT_FIELDS:
                    updated[field] = st.session_state.get(
                        f"incident_editor_{system}_{widget_id}_{field}",
                        incident.get(field, ""),
                    )
                # Single timestamp milestones keep their own TBD controls.
                for field in (INCIDENT_OCCURRENCE_TIME_FIELD, INCIDENT_STABLE_TIME_FIELD):
                    tbd = st.session_state.get(
                        f"incident_editor_{system}_{widget_id}_{field}_tbd",
                        _parse_saved_datetime(incident.get(field)) is None,
                    )
                    if tbd:
                        updated[field] = None
                    else:
                        date_value = st.session_state.get(
                            f"incident_editor_{system}_{widget_id}_{field}_date"
                        )
                        time_value = st.session_state.get(
                            f"incident_editor_{system}_{widget_id}_{field}_time"
                        )
                        if date_value and time_value:
                            updated[field] = datetime.combine(
                                date_value, time_value
                            ).isoformat(timespec="minutes")
                        else:
                            updated[field] = incident.get(field)

                # One shared TBD checkbox governs both Start and End for each schedule.
                for schedule_field in INCIDENT_SCHEDULE_FIELDS:
                    schedule_tbd = st.session_state.get(
                        f"incident_editor_{system}_{widget_id}_{schedule_field}_tbd",
                        (
                            _parse_saved_datetime(
                                incident.get(_incident_range_start_field(schedule_field))
                            ) is None
                            and _parse_saved_datetime(
                                incident.get(_incident_range_end_field(schedule_field))
                            ) is None
                        ),
                    )
                    start_field = _incident_range_start_field(schedule_field)
                    end_field = _incident_range_end_field(schedule_field)
                    if schedule_tbd:
                        updated[start_field] = None
                        updated[end_field] = None
                    else:
                        for range_field in (start_field, end_field):
                            date_value = st.session_state.get(
                                f"incident_editor_{system}_{widget_id}_{range_field}_date"
                            )
                            if date_value:
                                if schedule_field == "Repair Schedule (Tentative)":
                                    updated[range_field] = datetime.combine(date_value, datetime.min.time()).isoformat(timespec="minutes")
                                else:
                                    time_value = st.session_state.get(
                                        f"incident_editor_{system}_{widget_id}_{range_field}_time"
                                    )
                                    if time_value:
                                        updated[range_field] = datetime.combine(date_value, time_value).isoformat(timespec="minutes")
                                    else:
                                        updated[range_field] = incident.get(range_field)
                            else:
                                updated[range_field] = incident.get(range_field)
                updated["Possibility of Delay"] = st.session_state.get(
                    f"incident_editor_{system}_{widget_id}_Possibility of Delay",
                    incident.get("Possibility of Delay", ""),
                )
                saved_records.append(updated)

            saved_records.sort(key=_incident_sort_key)
            st.session_state.incident_info[system] = saved_records
            try:
                save_app_state_to_postgres()
            except Exception as exc:
                st.error(f"Unable to save incident information to PostgreSQL: {exc}")
            else:
                # Keep this system's editor state active. A dialog-scoped rerun
                # refreshes the saved values without dismissing the modal.
                st.session_state.incident_editor_records[system] = [
                    dict(record) for record in saved_records
                ]
                st.success("Incident information saved successfully.")
                st.rerun(scope="fragment")

    with button_cols[1]:
        if st.button("Thêm sự cố", use_container_width=True):
            draft_records.append(_blank_incident_record(system))
            st.session_state.incident_editor_records[system] = draft_records
            # Keep the Incident Information dialog open while refreshing its
            # contents to display the newly added incident form.
            st.rerun(scope="fragment")

    with button_cols[2]:
        if st.button("Đóng", use_container_width=True):
            st.session_state.incident_editor_records.pop(system, None)
            st.session_state.incident_editor_session_id = None
            if st.session_state.get("editing_incident_system") == system:
                st.session_state.editing_incident_system = None
            st.session_state.editing_incident_info = False
            clear_incident_dialog_route()
            st.rerun(scope="app")



def _on_incident_information_dismiss():
    """Clear stale incident route when the user closes the dialog with X/ESC."""
    clear_incident_dialog_route()


@st.dialog("Incident Information", width="large", on_dismiss=_on_incident_information_dismiss)
def incident_information_dialog(system):
    """Open the incident notice on demand; switch to editor within this same dialog."""
    is_editing = (
        (str(st.session_state.user_role).lower() == "admin" or
         (str(st.session_state.user_role).lower() == "operator" and system in current_user_systems()))
        and st.session_state.get("editing_incident_system") == system
        and st.session_state.get("editing_incident_info", False)
    )
    if is_editing:
        incident_information_editor(system)
    else:
        incident_information_notice(system)

def _on_incident_statistics_dismiss():
    """Clear the persisted dialog route when the user closes it with X/ESC."""
    clear_app_view_route()


@st.dialog("Thống kê sự cố", width="large", on_dismiss=_on_incident_statistics_dismiss)
def incident_statistics_dialog():
    """Apply incident filters only on click; retain the last result between edits."""

    today = datetime.now(VIETNAM_TZ).date()
    date_col1, date_col2 = st.columns(2)
    with date_col1:
        date_from_input = st.date_input(
            "Từ ngày xảy ra sự cố",
            value=today.replace(day=1),
            key="incident_stats_from",
        )
    with date_col2:
        date_to_input = st.date_input(
            "Đến ngày xảy ra sự cố",
            value=today,
            key="incident_stats_to",
        )

    # Use the native multiselect "Select all" option; do not add a separate
    # Vietnamese "Tất cả" choice to either filter.
    system_filter_input = st.multiselect(
        "Hệ thống",
        options=(current_user_systems() if str(st.session_state.user_role).lower() == "operator" else SYSTEMS),
        default=(current_user_systems() if str(st.session_state.user_role).lower() == "operator" else SYSTEMS),
        key="incident_stats_systems",
    )
    direction_filter_input = st.multiselect(
        "Hướng ảnh hưởng",
        options=DIRECTIONS,
        default=DIRECTIONS,
        key="incident_stats_directions",
    )
    fault_query_input = st.text_input(
        "Tên sự cố",
        placeholder="Ví dụ: SJC2 S4",
        key="incident_stats_fault_query",
    )
    # Persist the current dialog's draft criteria as route metadata so a browser
    # refresh restores the same filter configuration, not just the dialog shell.
    set_app_view_route("incident_statistics", {
        "date_from": date_from_input.isoformat(),
        "date_to": date_to_input.isoformat(),
        "systems": list(system_filter_input),
        "directions": list(direction_filter_input),
        "fault_query": fault_query_input,
    })

    # Both actions share one horizontal row. Editing criteria may rerun the
    # Streamlit script, but it does not execute the filtering loop; the last
    # applied result remains visible until the user clicks Lọc thống kê.
    filter_action_col, pdf_action_col = st.columns(2)
    with filter_action_col:
        filter_clicked = st.button(
            "Lọc thống kê", type="primary", use_container_width=True,
            key="apply_incident_statistics_filter",
        )

    if filter_clicked:
        if date_from_input > date_to_input:
            st.session_state.incident_stats_filtered_result = None
            st.session_state.incident_stats_applied_filters = None
            st.error("Ngày bắt đầu không được lớn hơn ngày kết thúc.")
        else:
            applied_filters = {
                "date_from": date_from_input,
                "date_to": date_to_input,
                "systems": list(system_filter_input),
                "directions": list(direction_filter_input),
                "fault_query": fault_query_input.strip(),
            }
            filtered_now = []
            stats_systems = current_user_systems() if str(st.session_state.user_role).lower() == "operator" else SYSTEMS
            for system in stats_systems:
                if system not in applied_filters["systems"]:
                    continue
                for record in _incident_records(system):
                    occurrence = _parse_saved_datetime(record.get("Time of Occurrence"))
                    if occurrence is None:
                        continue
                    occurrence_date = occurrence.date()
                    if not (applied_filters["date_from"] <= occurrence_date <= applied_filters["date_to"]):
                        continue

                    affected = [
                        d for d in record.get("affected_directions", [])
                        if d in DIRECTIONS
                    ]
                    selected_directions = applied_filters["directions"]
                    if selected_directions and not set(affected).intersection(selected_directions):
                        continue
                    if not selected_directions and affected:
                        continue

                    fault_name = str(record.get("Fault") or "")
                    query = applied_filters["fault_query"]
                    if query and query.casefold() not in fault_name.casefold():
                        continue
                    filtered_now.append({"system": system, "record": dict(record)})

            filtered_now.sort(
                key=lambda item: (
                    _incident_sort_key(item["record"]),
                    SYSTEMS.index(item["system"]),
                )
            )
            st.session_state.incident_stats_applied_filters = applied_filters
            st.session_state.incident_stats_filtered_result = filtered_now

    applied_filters = st.session_state.get("incident_stats_applied_filters")
    filtered = st.session_state.get("incident_stats_filtered_result")

    with pdf_action_col:
        if applied_filters is not None and filtered is not None:
            incident_records_snapshot = [
                {"system": item["system"], "record": dict(item["record"])}
                for item in filtered
            ]
            capacity_snapshot = dict(st.session_state.capacities)
            generated_at = datetime.now(VIETNAM_TZ).strftime("%d/%m/%Y %H:%M")

            def make_incident_statistics_pdf(
                records=incident_records_snapshot,
                capacities=capacity_snapshot,
                timestamp=generated_at,
            ):
                return build_incident_statistics_pdf(records, capacities, timestamp)

            st.download_button(
                "Tạo file PDF thống kê",
                data=make_incident_statistics_pdf,
                file_name=(
                    f"Thong_ke_su_co_{applied_filters['date_from']:%Y%m%d}_"
                    f"{applied_filters['date_to']:%Y%m%d}.pdf"
                ),
                mime="application/pdf",
                type="primary",
                use_container_width=True,
                on_click="ignore",
            )
        else:
            st.button(
                "Tạo file PDF thống kê", disabled=True,
                use_container_width=True, key="incident_stats_pdf_disabled",
            )

    if applied_filters is None or filtered is None:
        st.info("Hãy nhập các tiêu chí và nhấn **Lọc thống kê** để xem kết quả.")
        return

    # Warn when the visible draft differs from the last applied filter set.
    draft_filters = {
        "date_from": date_from_input,
        "date_to": date_to_input,
        "systems": list(system_filter_input),
        "directions": list(direction_filter_input),
        "fault_query": fault_query_input.strip(),
    }
    if draft_filters != applied_filters:
        st.info("Các tiêu chí vừa thay đổi chưa được áp dụng. Nhấn **Lọc thống kê** để cập nhật kết quả.")

    st.caption(f"Tìm thấy **{len(filtered)}** sự cố phù hợp với tiêu chí đã áp dụng.")
    if filtered:
        preview_rows = []
        for item in filtered:
            rec = item["record"]
            disp = _incident_default(item["system"], rec)
            preview_rows.append({
                "Hệ thống": item["system"],
                "Fault": rec.get("Fault") or "—",
                "Time of Occurrence": _format_incident_time(rec.get("Time of Occurrence")),
                "Affected Traffic": disp.get("Affected Traffic"),
                "Affected Capacity": disp.get("Affected Capacity"),
            })
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Không có sự cố phù hợp với các tiêu chí lọc. Các sự cố chưa có Time of Occurrence sẽ không nằm trong thống kê theo thời gian.")


# ---------------- Top-level horizontal navigation ----------------
tab_status, tab_export, tab_admin = st.tabs(["Hiện trạng hoạt động", "Xuất báo cáo", "Quản trị"])
with tab_status:
    st.markdown('<div class="main-title">🌐 BÁO CÁO HIỆN TRẠNG HOẠT ĐỘNG MẠNG CÁP QUỐC TẾ</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Dashboard giám sát trạng thái và phân tích dung lượng khai thác</div>', unsafe_allow_html=True)

    df_raw = get_raw_df()
    df_effective = get_effective_df(df_raw)
    total_raw = df_raw["Dung lượng nhập (GB)"].sum()
    total_effective = df_effective["Dung lượng đang khai thác (GB)"].sum()
    critical_count = sum(st.session_state.system_status[s] == "Nghiêm trọng" for s in SYSTEMS)
    top1, top2 = st.columns(2)
    utilization_ratio = float(total_effective / total_raw) if total_raw > 0 else 0.0
    utilization_ratio = min(max(utilization_ratio, 0.0), 1.0)
    with top1:
        st.metric("Đang khai thác", f"{total_effective:,.0f} GB")
        progress_html = (
            '<div style="height:12px;width:100%;background:#e5e7eb;border-radius:999px;overflow:hidden;margin-top:-2px;">'
            f'<div style="height:100%;width:{utilization_ratio * 100:.2f}%;background:#16a34a;border-radius:999px;"></div>'
            '</div>'
            f'<div style="font-size:.8rem;color:#64748b;margin-top:5px;">Dung lượng đang khai thác / tổng dung lượng: <span style="font-size:1.6rem;font-weight:800;line-height:1.1;color:#16324f;">{utilization_ratio * 100:.0f}%</span></div>'
        )
        st.markdown(progress_html, unsafe_allow_html=True)
    top2.metric("Hệ thống bị ảnh hưởng", f"{critical_count}/6")

    st.caption(f"Ngày cập nhật số liệu gần nhất: **{st.session_state.last_updated.strftime('%d/%m/%Y %H:%M:%S')} (UTC+7)**")

    st.markdown("## Trạng thái hoạt động")
    status_cols = st.columns(3)
    for i, s in enumerate(SYSTEMS):
        is_bad = st.session_state.system_status[s] == "Nghiêm trọng"
        status_text = "🔴 NGHIÊM TRỌNG" if is_bad else "🟢 BÌNH THƯỜNG"
        affected = st.session_state.affected_directions.get(s, []) if is_bad else []
        detail = "Hướng ảnh hưởng: " + (", ".join(affected) if affected else "Chưa chọn hướng")

        card_background = (
            "linear-gradient(135deg,#b91c1c,#ef4444)"
            if is_bad else "linear-gradient(135deg,#16803c,#22a653)"
        )
        st.markdown(
            f"""
            <style>
            .st-key-status_card_{s} {{
                background: {card_background} !important;
                border: 0 !important;
                border-radius: 14px !important;
                padding: 12px 14px !important;
                min-height: 130px;
                margin-bottom: 8px;
                color: #ffffff !important;
                box-shadow: 0 5px 16px rgba(15,23,42,.10);
            }}
            .st-key-status_card_{s} [data-testid="stMarkdownContainer"],
            .st-key-status_card_{s} [data-testid="stMarkdownContainer"] p {{
                color: #ffffff !important;
            }}
            .st-key-status_card_{s} button {{
                background: rgba(255,255,255,.18) !important;
                border: 1px solid rgba(255,255,255,.8) !important;
                color: #ffffff !important;
                border-radius: 50% !important;
                min-width: 2rem !important;
                width: 2rem !important;
                max-width: 2rem !important;
                height: 2rem !important;
                min-height: 2rem !important;
                max-height: 2rem !important;
                aspect-ratio: 1 / 1 !important;
                flex: 0 0 2rem !important;
                padding: 0 !important;
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
                font-size: 1.1rem !important;
                font-weight: 700 !important;
                line-height: 1 !important;
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )
        with status_cols[i % 3]:
            with st.container(border=True, key=f"status_card_{s}"):
                st.markdown(
                    f'<div class="status-name">{s}</div>'
                    f'<div class="status-type">{SYSTEM_TYPE[s]}</div>'
                    f'<div class="status-state">{status_text}</div>',
                    unsafe_allow_html=True,
                )
                if is_bad:
                    detail_col, info_col = st.columns([0.88, 0.12])
                    with detail_col:
                        st.markdown(
                            f'<div style="font-size:.78rem; margin-top:5px; color:inherit;">{detail}</div>',
                            unsafe_allow_html=True,
                        )
                    with info_col:
                        # Every critical system gets its own information entry point.
                        # Incident records/drafts are isolated by system name.
                        st.session_state.incident_info.setdefault(s, [])
                        if st.button(
                            "ⓘ",
                            key=f"incident_info_button_{s}",
                            help=f"Open incident information for {s}",
                            type="secondary",
                            use_container_width=False,
                        ):
                            # Reset any abandoned edit state, then persist the selected
                            # dialog route so browser Refresh can restore this exact view.
                            st.session_state.editing_incident_system = None
                            st.session_state.editing_incident_info = False
                            st.session_state.incident_editor_records.pop(s, None)
                            st.session_state.incident_editor_session_id = None
                            set_incident_dialog_route(s, mode="notice")
                # Không hiển thị chú thích phụ đối với hệ thống bình thường.

    # Restore the active working window after every full app run, including a new
    # WebSocket session created by browser Refresh. All dialog types use this same
    # route dispatcher; access is revalidated before any dialog is reopened.
    _active_view = st.query_params.get("app_view") or st.query_params.get("app_dialog")
    _active_view_state = get_app_view_state()
    if _active_view == "incident_information":
        _active_dialog_system = _active_view_state.get("system") or st.query_params.get("app_dialog_system")
        _active_dialog_mode = _active_view_state.get("mode") or st.query_params.get("app_dialog_mode", "notice")
        if _active_dialog_system not in SYSTEMS:
            clear_app_view_route()
        else:
            _active_role = str(st.session_state.get("user_role") or "").lower()
            _can_view_system = (
                _active_role in {"admin", "viewer"}
                or (_active_role == "operator" and _active_dialog_system in current_user_systems())
            )
            if not _can_view_system:
                clear_app_view_route()
            else:
                if _active_dialog_mode == "edit":
                    _can_edit_system = (
                        _active_role == "admin"
                        or (_active_role == "operator" and _active_dialog_system in current_user_systems())
                    )
                    if _can_edit_system:
                        st.session_state.editing_incident_system = _active_dialog_system
                        st.session_state.editing_incident_info = True
                        st.session_state.incident_editor_records.setdefault(
                            _active_dialog_system,
                            [dict(record) for record in _incident_records(_active_dialog_system)],
                        )
                        st.session_state.setdefault("incident_editor_session_id", "restored")
                    else:
                        clear_app_view_route()
                        st.session_state.editing_incident_system = None
                        st.session_state.editing_incident_info = False
                else:
                    st.session_state.editing_incident_system = None
                    st.session_state.editing_incident_info = False
                    st.session_state.incident_editor_records.pop(_active_dialog_system, None)
                    st.session_state.incident_editor_session_id = None
                if st.query_params.get("app_view") == "incident_information" or st.query_params.get("app_dialog") == "incident_information":
                    incident_information_dialog(_active_dialog_system)
    elif _active_view == "event_logs" and str(st.session_state.get("user_role") or "").lower() != "admin":
        # Event Logs is an administrator-only window; never restore it for other roles.
        clear_app_view_route()
    elif _active_view == "incident_statistics":
        # Rehydrate filter widgets before the dialog creates them. These values are
        # ordinary UI state only; the underlying incident records remain in DB.
        try:
            if _active_view_state.get("date_from"):
                st.session_state["incident_stats_from"] = datetime.fromisoformat(_active_view_state["date_from"]).date()
            if _active_view_state.get("date_to"):
                st.session_state["incident_stats_to"] = datetime.fromisoformat(_active_view_state["date_to"]).date()
            if isinstance(_active_view_state.get("systems"), list):
                st.session_state["incident_stats_systems"] = [x for x in _active_view_state["systems"] if x in SYSTEMS]
            if isinstance(_active_view_state.get("directions"), list):
                st.session_state["incident_stats_directions"] = [x for x in _active_view_state["directions"] if x in DIRECTIONS]
            if "fault_query" in _active_view_state:
                st.session_state["incident_stats_fault_query"] = str(_active_view_state["fault_query"])
        except Exception:
            pass
        incident_statistics_dialog()

    with st.expander("Số liệu gốc thống kê do người dùng cung cấp trong tình trạng toàn mạng an toàn", expanded=False):
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
        st.dataframe(matrix.style.format({d: "{:,.0f} GB" for d in DIRECTIONS}), use_container_width=True)

    st.markdown("## Phân tích hiện trạng dung lượng, tỷ trọng")
    _analysis_tab_names = ["Dung lượng", "Tỷ trọng theo hướng"]
    _saved_analysis_tab = st.query_params.get("app_tab")
    if _saved_analysis_tab in _analysis_tab_names and "analysis_tabs" not in st.session_state:
        # Streamlit versions supporting keyed tabs can restore the selected tab.
        st.session_state["analysis_tabs"] = _saved_analysis_tab
    try:
        tab1, tab2 = st.tabs(
            _analysis_tab_names,
            key="analysis_tabs",
            on_change="rerun",
        )
        _selected_analysis_tab = st.session_state.get("analysis_tabs")
        if _selected_analysis_tab in _analysis_tab_names:
            st.query_params["app_tab"] = _selected_analysis_tab
    except TypeError:
        # Compatibility fallback for older Streamlit releases that do not yet
        # expose tab selection state. The rest of the app remains fully functional.
        tab1, tab2 = st.tabs(_analysis_tab_names)

    system_totals = df_effective.groupby("Hệ thống")["Dung lượng đang khai thác (GB)"].sum().reindex(SYSTEMS, fill_value=0).reset_index()
    system_totals.columns = ["Hệ thống", "Dung lượng đang khai thác (GB)"]

    # Reuse cached chart images across reruns; only changed data triggers rendering.
    dashboard_chart_images = build_dashboard_chart_images(df_effective)
    with tab1:
        # Retain the former "So sánh 6 hệ thống" content first.
        st.image(dashboard_chart_images[0], use_container_width=True)

        # Move all content from the former "Cơ cấu toàn mạng" tab below it.
        overall = system_totals.copy()
        overall["Tỷ trọng toàn mạng (%)"] = np.where(
            total_effective > 0,
            overall["Dung lượng đang khai thác (GB)"] / total_effective * 100,
            0,
        )
        col_a, col_b = st.columns([1.2, 1])
        with col_a:
            st.image(dashboard_chart_images[2], use_container_width=True)
        with col_b:
            st.markdown("### Tỷ trọng toàn mạng")
            st.dataframe(
                overall[["Hệ thống", "Dung lượng đang khai thác (GB)", "Tỷ trọng toàn mạng (%)"]].style.format({
                    "Dung lượng đang khai thác (GB)": "{:,.0f}",
                    "Tỷ trọng toàn mạng (%)": "{:.0f}%",
                }), use_container_width=True, hide_index=True)
            st.markdown("### Tổng theo hướng")
            by_direction = df_effective.groupby("Hướng")["Dung lượng đang khai thác (GB)"].sum().reindex(DIRECTIONS, fill_value=0).reset_index()
            by_direction["Tỷ trọng toàn mạng (%)"] = np.where(
                total_effective > 0,
                by_direction["Dung lượng đang khai thác (GB)"] / total_effective * 100,
                0,
            )
            st.dataframe(by_direction.style.format({
                "Dung lượng đang khai thác (GB)": "{:,.0f}",
                "Tỷ trọng toàn mạng (%)": "{:.0f}%",
            }), use_container_width=True, hide_index=True)

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
        st.image(dashboard_chart_images[1], use_container_width=True)
        st.markdown("### Tỷ trọng dung lượng hiện tại so với thời điểm an toàn theo hướng kết nối")
        st.image(dashboard_chart_images[3], use_container_width=True)
        show_share = share.copy()
        show_share["Tổng hướng (GB)"] = direction_total
        st.dataframe(
            show_share.style.format({**{system: "{:,.0f}" for system in SYSTEMS}, "Tổng hướng (GB)": "{:,.0f}"}),
            use_container_width=True,
        )

# ---------------- Administration tab with vertical sub-menu ----------------
with tab_admin:
    if str(st.session_state.get("user_role") or "").lower() == "admin":
        admin_menu = st.radio(
            "Chức năng quản trị",
            ["Quản lý người dùng", "Event Logs", "Backup & Restore database"],
            vertical=True, label_visibility="collapsed", key="admin_main_menu"
        )
    else:
        admin_menu = "Không có quyền quản trị"
        st.info("Tài khoản Viewer/Operator không có quyền truy cập các chức năng quản trị.")

    if admin_menu == "Quản lý người dùng" and str(st.session_state.get("user_role") or "").lower() == "admin":
        with st.container(border=True):
            st.markdown("### 👥 Quản lý người dùng")
            with st.expander("Tạo tài khoản mới", expanded=False):
                # Keep role and system selection outside the form so Streamlit
                # reruns immediately when the role changes. This lets the
                # system selector be enabled only for operator accounts.
                new_role = st.selectbox(
                    "Vai trò", ["viewer", "operator"], key="new_user_role"
                )
                new_operator_systems = st.multiselect(
                    "Hệ thống được phép thao tác",
                    options=SYSTEMS,
                    default=[],
                    key="new_operator_systems",
                    disabled=(new_role != "operator"),
                    help="Operator chỉ được nhập liệu và chỉnh sửa các hệ thống được chọn.",
                )
                with st.form("create_user_account_form", clear_on_submit=True):
                    new_username = st.text_input("Tên đăng nhập", key="new_user_username")
                    new_password = st.text_input("Mật khẩu (tối thiểu 8 ký tự)", type="password", key="new_user_password")
                    new_duration = st.selectbox("Thời hạn sử dụng", list(DURATION_OPTIONS.keys()), key="new_user_duration")
                    create_submitted = st.form_submit_button("Tạo tài khoản", type="primary", use_container_width=True)
                    if create_submitted:
                        selected_systems = new_operator_systems if new_role == "operator" else []
                        ok, message = create_user_account(new_username, new_password, new_duration, new_role, selected_systems)
                        (st.success if ok else st.error)(message)
                        if ok:
                            st.session_state.show_user_list = True
                            st.rerun()

            # Expander contents execute even when collapsed; share one DB read.
            managed_df = list_managed_users()
            with st.expander("Danh sách người dùng", expanded=st.session_state.get("show_user_list", False)):
                st.dataframe(managed_df, use_container_width=True, hide_index=True)

            with st.expander("Khóa / Xóa / Gia hạn tài khoản", expanded=False):
                admin_user, _ = get_admin_credentials()
                selectable = [u for u in managed_df["Tên đăng nhập"].tolist()
                              if not admin_user or u.casefold() != admin_user.casefold()]
                selectable = [u for u in selectable if u]
                if not selectable:
                    st.info("Chưa có tài khoản viewer hoặc operator để quản lý.")
                else:
                    selected_user = st.selectbox("Chọn tài khoản", selectable, key="manage_selected_user")
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

    if admin_menu == "Backup & Restore database" and str(st.session_state.get("user_role") or "").lower() == "admin":
        with st.container(border=True):
            st.markdown("### 💾 Backup & Restore database")
            # Build the PostgreSQL + SQLite archive only when requested, not on
            # every Streamlit rerun while the admin is using another function.
            _backup_created_by = str(st.session_state.get("current_username") or "unknown")
            st.download_button(
                "Backup database",
                data=lambda: create_database_backup(_backup_created_by),
                file_name=f"backup_cable_dashboard_{datetime.now(VIETNAM_TZ).strftime('%Y%m%d_%H%M%S')}.zip",
                mime="application/zip",
                use_container_width=True,
                help="Tạo và tải file ZIP khi nhấn nút; lưu file ở nơi an toàn trước khi cập nhật ứng dụng.",
            )

            st.markdown("#### ♻️ Restore database")
            # Hide Streamlit's default uploader size/type helper text so the section
            # contains only the requested controls and labels.
            st.markdown(
                "<style>div[data-testid='stFileUploader'] small {display:none !important;}</style>",
                unsafe_allow_html=True,
            )
            _restore_widget_version = st.session_state.get("database_restore_widget_version", 0)
            restore_file = st.file_uploader(
                "Upload backup file",
                type=["zip"],
                key=f"database_restore_upload_{_restore_widget_version}",
                help=None,
            )
            confirm_restore = st.checkbox(
                "Xác nhận khôi phục và ghi đè dữ liệu",
                key=f"confirm_database_restore_{_restore_widget_version}",
            )
            if st.button(
                "♻️ Restore database",
                disabled=(restore_file is None or not confirm_restore),
                use_container_width=True,
            ):
                try:
                    restore_database_backup(restore_file.getvalue())
                    # Force a fresh login. The next authenticated session will load
                    # the restored PostgreSQL snapshot instead of retaining cached data.
                    st.session_state.authenticated = False
                    st.session_state.user_role = None
                    st.session_state.current_username = None
                    clear_browser_session_token()
                    st.session_state.app_state_loaded = False
                    st.session_state.restore_login_notice = True
                    # Rotate uploader/checkbox keys so the previous file and consent
                    # are not retained after the administrator signs in again.
                    st.session_state.database_restore_widget_version = _restore_widget_version + 1
                    for _key in (
                        "capacities", "system_status", "affected_directions",
                        "incident_info", "last_updated", "incident_editor_records",
                        "editing_incident_info", "editing_incident_system",
                    ):
                        st.session_state.pop(_key, None)
                    st.rerun()
                except Exception as restore_exc:
                    st.error(f"Khôi phục thất bại; dữ liệu hiện tại được giữ nguyên nếu thao tác rollback thành công. Chi tiết: {restore_exc}")


    if admin_menu == "Event Logs" and str(st.session_state.get("user_role") or "").lower() == "admin":
        # ---------------- Event Logs ----------------
        st.markdown("## Event Logs")


        def _on_event_logs_dismiss():
            """Clear the saved Event Logs route when the user closes with X/ESC."""
            clear_app_view_route()


        @st.dialog("Event Logs", width="large", on_dismiss=_on_event_logs_dismiss)
        def show_history_log_dialog(result_df, query_info=None):
            """Display retrieved events and provide an Excel download."""
            if not isinstance(result_df, pd.DataFrame):
                st.info("Không có dữ liệu để hiển thị.")
                return

            display_columns = [
                "Activities", "Old value", "New value", "Time of Occurrence"
            ]
            st.caption(
                f"Tìm thấy {len(result_df):,} sự kiện."
                + (
                    f" | Thời gian: {query_info[0].strftime('%d/%m/%Y')} – "
                    f"{query_info[1].strftime('%d/%m/%Y')}"
                    if query_info else ""
                )
            )
            st.dataframe(
                result_df[display_columns],
                use_container_width=True,
                hide_index=True,
            )

            # Excel remains the preferred export. If openpyxl is not installed in the
            # deployment, keep the export feature usable through a UTF-8-BOM CSV file.
            excel_buffer = BytesIO()
            try:
                import openpyxl  # noqa: F401 - explicitly verify the optional engine
                with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                    result_df[display_columns].to_excel(
                        writer, index=False, sheet_name="Event Logs"
                    )
                    worksheet = writer.sheets["Event Logs"]
                    worksheet.freeze_panes = "A2"
                    worksheet.auto_filter.ref = worksheet.dimensions
                    for column_cells in worksheet.columns:
                        max_length = max(
                            len(str(cell.value or "")) for cell in column_cells
                        )
                        worksheet.column_dimensions[
                            column_cells[0].column_letter
                        ].width = min(max(max_length + 2, 16), 60)

                excel_buffer.seek(0)
                st.download_button(
                    "Xuất Excel Event Logs",
                    data=excel_buffer.getvalue(),
                    file_name=(
                        "Event_Logs_"
                        f"{datetime.now(VIETNAM_TZ).strftime('%Y%m%d_%H%M%S')}.xlsx"
                    ),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                    key="history_log_excel_download_dialog",
                )
            except (ImportError, ModuleNotFoundError):
                st.warning(
                    "Môi trường chưa cài openpyxl nên chưa thể tạo file Excel (.xlsx). "
                    "Bạn vẫn có thể xuất dữ liệu CSV bên dưới; để bật lại Excel, thêm "
                    "'openpyxl>=3.1' vào requirements.txt và khởi động lại ứng dụng."
                )
                csv_data = result_df[display_columns].to_csv(
                    index=False, encoding="utf-8-sig"
                )
                st.download_button(
                    "Xuất dữ liệu Event Logs (CSV)",
                    data=csv_data,
                    file_name=(
                        "Event_Logs_"
                        f"{datetime.now(VIETNAM_TZ).strftime('%Y%m%d_%H%M%S')}.csv"
                    ),
                    mime="text/csv",
                    type="primary",
                    key="history_log_csv_download_dialog",
                )


        # Rehydrate the Event Logs query controls before their widgets are created.
        if st.query_params.get("app_view") == "event_logs":
            _saved_event_state = get_app_view_state()
            try:
                if _saved_event_state.get("date_from") and _saved_event_state.get("date_to"):
                    st.session_state["history_log_date_range"] = (
                        datetime.fromisoformat(_saved_event_state["date_from"]).date(),
                        datetime.fromisoformat(_saved_event_state["date_to"]).date(),
                    )
                st.session_state["history_log_activity_filter"] = str(_saved_event_state.get("activity", ""))
            except Exception:
                pass

        with st.expander("Cấu hình truy xuất Event Logs", expanded=False):
            today_vn = datetime.now(VIETNAM_TZ).date()
            default_start = today_vn - timedelta(days=30)

            # A Streamlit form batches widget changes: editing dates/Activities does not
            # rerun this section or execute a database query until submit is clicked.
            with st.form("history_log_query_form", clear_on_submit=False):
                date_cols = st.columns(2)
                with date_cols[0]:
                    history_dates = st.date_input(
                        "Thời gian thực hiện",
                        value=(default_start, today_vn),
                        key="history_log_date_range",
                        help="Chọn ngày bắt đầu và ngày kết thúc (theo múi giờ GMT+7).",
                    )
                with date_cols[1]:
                    history_activity = st.text_input(
                        "Activities",
                        placeholder="Nhập nội dung hoạt động cần tìm kiếm",
                        key="history_log_activity_filter",
                    )

                query_clicked = st.form_submit_button(
                    "Truy xuất",
                    type="primary",
                    use_container_width=False,
                )

        if query_clicked:
            if not isinstance(history_dates, (tuple, list)) or len(history_dates) != 2:
                st.error("Vui lòng chọn đầy đủ ngày bắt đầu và ngày kết thúc.")
            elif history_dates[0] > history_dates[1]:
                st.error("Ngày bắt đầu không được lớn hơn ngày kết thúc.")
            else:
                try:
                    # Import legacy Viewer creation timestamps only after explicit submit.
                    # This also makes accounts created before the fix visible in history.
                    backfill_viewer_creation_history_events()
                    # This is the only place that queries the audit table.
                    result_df = query_history_log_events(
                        history_dates[0],
                        history_dates[1],
                        history_activity.strip(),
                    )
                    query_info = (
                        history_dates[0],
                        history_dates[1],
                        history_activity.strip(),
                    )
                    set_app_view_route("event_logs", {
                        "date_from": history_dates[0].isoformat(),
                        "date_to": history_dates[1].isoformat(),
                        "activity": history_activity.strip(),
                    })
                except Exception as exc:
                    st.error(f"Không thể truy xuất Event Logs: {exc}")

        # Re-run the saved Event Logs query after Refresh and reopen its result window.
        if st.query_params.get("app_view") == "event_logs":
            _event_state = get_app_view_state()
            try:
                _event_from = datetime.fromisoformat(_event_state["date_from"]).date()
                _event_to = datetime.fromisoformat(_event_state["date_to"]).date()
                if _event_from <= _event_to:
                    _event_activity = str(_event_state.get("activity", ""))
                    _event_result = query_history_log_events(_event_from, _event_to, _event_activity)
                    show_history_log_dialog(
                        _event_result,
                        query_info=(_event_from, _event_to, _event_activity),
                    )
                else:
                    clear_app_view_route()
            except Exception as _event_restore_exc:
                st.warning(f"Không thể khôi phục cửa sổ Event Logs: {_event_restore_exc}")
                clear_app_view_route()# ---------------- Report export tab with vertical sub-menu ----------------
with tab_export:
    export_menu = st.radio(
        "Loại báo cáo", ["Hiện trạng hoạt động mạng", "Thống kê sự cố"],
        vertical=True, label_visibility="collapsed", key="export_main_menu"
    )
    if export_menu == "Hiện trạng hoạt động mạng":
        # Snapshot the current dashboard values, but defer chart/PDF rendering until click.
        effective_snapshot = df_effective.copy()
        raw_snapshot = df_raw.copy()
        status_snapshot = dict(st.session_state.system_status)
        affected_snapshot = {
            system: list(st.session_state.affected_directions.get(system, []))
            for system in SYSTEMS
        }
        report_date = datetime.now(VIETNAM_TZ).strftime("%d/%m/%Y")

        def make_status_pdf(
            effective=effective_snapshot,
            raw=raw_snapshot,
            status=status_snapshot,
            affected=affected_snapshot,
            date_label=report_date,
        ):
            return build_pdf(effective, raw, status, affected, date_label)

        st.download_button(
            "Hiện trạng hoạt động mạng",
            data=make_status_pdf,
            file_name=f"Hien_trang_hoat_dong_mang_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
            on_click="ignore",
        )
    if export_menu == "Thống kê sự cố":
        if st.button("Thống kê sự cố", type="primary", use_container_width=True):
            set_app_view_route("incident_statistics")
            incident_statistics_dialog()

st.divider()
st.caption("International Cable Network Dashboard • Đơn vị dung lượng: GB • Dữ liệu chỉ được cập nhật khi người vận hành lưu thay đổi.")
