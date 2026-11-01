import sqlite3
import os
import logging
from datetime import datetime, timedelta
from app.core.crypto import encrypt_credential, decrypt_credential
from app.core.config import settings
from app.core.time import get_now, get_now_str

logger = logging.getLogger("x9.db")
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB_DIR = os.path.join(BACKEND_DIR, "data")
DB_PATH = os.getenv("DB_PATH", os.path.join(DEFAULT_DB_DIR, "x9.db"))

def get_connection():
    db_path = os.getenv("DB_PATH", DB_PATH)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Tabela 1: Configurações da Câmera (com senha criptografada em AES-256)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS camera_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            ip TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 554,
            http_port INTEGER NOT NULL DEFAULT 80,
            user TEXT NOT NULL DEFAULT 'admin',
            password_encrypted TEXT NOT NULL,
            subtype INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela 2: Regras do Vigia Noturno
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS night_watch_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 1,
            start_time TEXT NOT NULL DEFAULT '00:00',
            end_time TEXT NOT NULL DEFAULT '06:00',
            flash_duration REAL NOT NULL DEFAULT 2.5,
            confidence_threshold REAL NOT NULL DEFAULT 0.45,
            model_type TEXT NOT NULL DEFAULT 'yolov8s',
            retention_days INTEGER NOT NULL DEFAULT 15,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela 3: Eventos de Detecção de Pessoas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detection_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            event_type TEXT NOT NULL DEFAULT 'PERSON_DETECTED',
            confidence REAL NOT NULL,
            flash_triggered INTEGER NOT NULL DEFAULT 1,
            snapshot_path TEXT,
            video_path TEXT
        )
    """)

    # Popula dados padrão se tabela estiver vazia
    cursor.execute("SELECT COUNT(*) as count FROM camera_config WHERE id = 1")
    if cursor.fetchone()["count"] == 0:
        cursor.execute("""
            INSERT INTO camera_config (id, ip, port, http_port, user, password_encrypted, subtype)
            VALUES (1, ?, ?, ?, ?, ?, ?)
        """, (
            settings.CAMERA_IP,
            settings.CAMERA_PORT,
            settings.CAMERA_HTTP_PORT,
            settings.CAMERA_USER,
            encrypt_credential(settings.CAMERA_PASSWORD),
            settings.CAMERA_SUBTYPE,
        ))

    cursor.execute("SELECT COUNT(*) as count FROM night_watch_config WHERE id = 1")
    if cursor.fetchone()["count"] == 0:
        cursor.execute("""
            INSERT INTO night_watch_config (id, enabled, start_time, end_time, flash_duration, confidence_threshold, model_type, retention_days)
            VALUES (1, 1, '00:00', '06:00', 2.5, 0.45, 'yolov8s', 15)
        """)

    conn.commit()
    conn.close()
    logger.info("Banco de dados SQLite inicializado com sucesso em: %s", DB_PATH)

def get_camera_config() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM camera_config WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {}
    data = dict(row)
    data["password"] = decrypt_credential(data.pop("password_encrypted"))
    return data

def save_camera_config(ip: str, port: int, http_port: int, user: str, password: str, subtype: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO camera_config (id, ip, port, http_port, user, password_encrypted, subtype, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            ip = excluded.ip,
            port = excluded.port,
            http_port = excluded.http_port,
            user = excluded.user,
            password_encrypted = excluded.password_encrypted,
            subtype = excluded.subtype,
            updated_at = CURRENT_TIMESTAMP
    """, (ip, port, http_port, user, encrypt_credential(password), subtype))
    conn.commit()
    conn.close()

def get_night_watch_config() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM night_watch_config WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}

def save_night_watch_config(enabled: bool, start_time: str, end_time: str, flash_duration: float, confidence_threshold: float, model_type: str, retention_days: int = 15):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO night_watch_config (id, enabled, start_time, end_time, flash_duration, confidence_threshold, model_type, retention_days, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            enabled = excluded.enabled,
            start_time = excluded.start_time,
            end_time = excluded.end_time,
            flash_duration = excluded.flash_duration,
            confidence_threshold = excluded.confidence_threshold,
            model_type = excluded.model_type,
            retention_days = excluded.retention_days,
            updated_at = CURRENT_TIMESTAMP
    """, (1 if enabled else 0, start_time, end_time, flash_duration, confidence_threshold, model_type, retention_days))
    conn.commit()
    conn.close()

def log_detection_event(confidence: float, flash_triggered: bool, snapshot_path: str, video_path: str | None = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    brt_ts = get_now_str()
    cursor.execute("""
        INSERT INTO detection_events (timestamp, confidence, flash_triggered, snapshot_path, video_path)
        VALUES (?, ?, ?, ?, ?)
    """, (brt_ts, confidence, 1 if flash_triggered else 0, snapshot_path, video_path))
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return event_id

def list_detection_events(limit: int = 50, offset: int = 0) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM detection_events
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def count_detection_events() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM detection_events")
    row = cursor.fetchone()
    conn.close()
    return row["count"] if row else 0

def _remove_snapshot_and_video_files(snapshot_path: str | None, video_path: str | None = None):
    if snapshot_path:
        for folder in ["snapshots", os.path.join("backend", "snapshots"), os.path.join("..", "snapshots")]:
            target = os.path.join(folder, snapshot_path)
            if os.path.exists(target):
                try:
                    os.remove(target)
                except Exception as e:
                    logger.warning(f"Erro ao remover snapshot {target}: {e}")
    if video_path:
        for folder in ["recordings", os.path.join("backend", "recordings"), os.path.join("..", "recordings")]:
            target = os.path.join(folder, video_path)
            if os.path.exists(target):
                try:
                    os.remove(target)
                except Exception as e:
                    logger.warning(f"Erro ao remover gravação {target}: {e}")

    # Remove também do Google Drive se o backup estiver ativado
    try:
        from app.services.gdrive_service import gdrive_service
        if snapshot_path:
            gdrive_service.delete_file_by_name(snapshot_path)
        if video_path:
            gdrive_service.delete_file_by_name(video_path)
    except Exception as e:
        logger.warning(f"Erro ao excluir arquivo correspondente no Google Drive: {e}")

def delete_detection_event(event_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT snapshot_path, video_path FROM detection_events WHERE id = ?", (event_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False

    _remove_snapshot_and_video_files(row["snapshot_path"], row["video_path"])
    cursor.execute("DELETE FROM detection_events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
    return True

def delete_detection_events_batch(event_ids: list[int]) -> int:
    if not event_ids:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in event_ids)
    cursor.execute(f"SELECT snapshot_path, video_path FROM detection_events WHERE id IN ({placeholders})", event_ids)
    rows = cursor.fetchall()
    for row in rows:
        _remove_snapshot_and_video_files(row["snapshot_path"], row["video_path"])

    cursor.execute(f"DELETE FROM detection_events WHERE id IN ({placeholders})", event_ids)
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count

def cleanup_retention(retention_days: int = 15):
    """
    Remove eventos, snapshots e vídeos mais antigos que retention_days.
    Mantém o disco da VPS sempre enxuto.
    """
    cutoff = (get_now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT snapshot_path, video_path FROM detection_events WHERE timestamp < ?", (cutoff,))
    old_records = cursor.fetchall()

    for rec in old_records:
        snap = rec["snapshot_path"]
        vid = rec["video_path"]
        if snap and os.path.exists(os.path.join("snapshots", snap)):
            try:
                os.remove(os.path.join("snapshots", snap))
            except Exception:
                pass
        if vid and os.path.exists(os.path.join("recordings", vid)):
            try:
                os.remove(os.path.join("recordings", vid))
            except Exception:
                pass

    cursor.execute("DELETE FROM detection_events WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info(f"Limpeza de retenção ({retention_days} dias): {deleted} registros antigos removidos.")
