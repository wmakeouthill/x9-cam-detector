import sqlite3
import os
import time
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
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
    except Exception:
        pass
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
            image_profile_mode TEXT NOT NULL DEFAULT 'auto',
            night_start_time TEXT NOT NULL DEFAULT '22:00',
            night_end_time TEXT NOT NULL DEFAULT '05:00',
            clip_min_duration INTEGER NOT NULL DEFAULT 20,
            clip_max_duration INTEGER NOT NULL DEFAULT 120,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migração idempotente para bases já existentes
    cursor.execute("PRAGMA table_info(camera_config)")
    existing_cols = [c["name"] for c in cursor.fetchall()]
    if "image_profile_mode" not in existing_cols:
        cursor.execute("ALTER TABLE camera_config ADD COLUMN image_profile_mode TEXT NOT NULL DEFAULT 'auto'")
    if "night_start_time" not in existing_cols:
        cursor.execute("ALTER TABLE camera_config ADD COLUMN night_start_time TEXT NOT NULL DEFAULT '22:00'")
    if "night_end_time" not in existing_cols:
        cursor.execute("ALTER TABLE camera_config ADD COLUMN night_end_time TEXT NOT NULL DEFAULT '05:00'")
    if "clip_min_duration" not in existing_cols:
        cursor.execute("ALTER TABLE camera_config ADD COLUMN clip_min_duration INTEGER NOT NULL DEFAULT 20")
    if "clip_max_duration" not in existing_cols:
        cursor.execute("ALTER TABLE camera_config ADD COLUMN clip_max_duration INTEGER NOT NULL DEFAULT 120")

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

    # Tabela 3: Fila de Sincronização e Reconciliação Offline com Google Drive
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gdrive_sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            file_type TEXT NOT NULL DEFAULT 'snapshot',
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            synced_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela 4: Eventos de Detecção de Pessoas
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

    # Tabela 5: Configurações do WhatsApp (Evolution API v2 com chave criptografada)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 0,
            api_url TEXT NOT NULL DEFAULT '',
            api_key_encrypted TEXT NOT NULL DEFAULT '',
            instance_name TEXT NOT NULL DEFAULT '',
            target_number TEXT NOT NULL DEFAULT '',
            send_photo INTEGER NOT NULL DEFAULT 1,
            send_video INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela 6: Configurações de Gravação 24h (DVR Contínuo)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dvr_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 1,
            segment_seconds INTEGER NOT NULL DEFAULT 900,
            retention_days INTEGER NOT NULL DEFAULT 15,
            subtype INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela 7: Segmentos de Gravação 24h (Blocos MP4 locais + subpasta Gravacoes_24h no Google Drive)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dvr_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            duration_seconds INTEGER NOT NULL DEFAULT 900,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'recording',
            drive_file_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    cursor.execute("SELECT COUNT(*) as count FROM whatsapp_config WHERE id = 1")
    if cursor.fetchone()["count"] == 0:
        cursor.execute("""
            INSERT INTO whatsapp_config (id, enabled, api_url, api_key_encrypted, instance_name, target_number, send_photo, send_video)
            VALUES (1, ?, ?, ?, ?, ?, 1, 1)
        """, (
            1 if (settings.EVOLUTION_API_URL and settings.EVOLUTION_INSTANCE_NAME) else 0,
            settings.EVOLUTION_API_URL,
            encrypt_credential(settings.EVOLUTION_API_KEY) if settings.EVOLUTION_API_KEY else "",
            settings.EVOLUTION_INSTANCE_NAME,
            settings.EVOLUTION_TARGET_NUMBERS,
        ))

    cursor.execute("SELECT COUNT(*) as count FROM dvr_config WHERE id = 1")
    if cursor.fetchone()["count"] == 0:
        cursor.execute("""
            INSERT INTO dvr_config (id, enabled, segment_seconds, retention_days, subtype)
            VALUES (1, 1, 900, 15, 0)
        """)
    else:
        cursor.execute("UPDATE dvr_config SET segment_seconds = 900 WHERE id = 1 AND segment_seconds = 60")

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
    data.setdefault("image_profile_mode", "auto")
    data.setdefault("night_start_time", "22:00")
    data.setdefault("night_end_time", "05:00")
    data.setdefault("clip_min_duration", 20)
    data.setdefault("clip_max_duration", 120)
    return data

def save_camera_config(
    ip: str,
    port: int,
    http_port: int,
    user: str,
    password: str,
    subtype: int,
    image_profile_mode: str = "auto",
    night_start_time: str = "22:00",
    night_end_time: str = "05:00",
    clip_min_duration: int = 20,
    clip_max_duration: int = 120,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO camera_config (
            id, ip, port, http_port, user, password_encrypted, subtype,
            image_profile_mode, night_start_time, night_end_time,
            clip_min_duration, clip_max_duration, updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            ip = excluded.ip,
            port = excluded.port,
            http_port = excluded.http_port,
            user = excluded.user,
            password_encrypted = excluded.password_encrypted,
            subtype = excluded.subtype,
            image_profile_mode = excluded.image_profile_mode,
            night_start_time = excluded.night_start_time,
            night_end_time = excluded.night_end_time,
            clip_min_duration = excluded.clip_min_duration,
            clip_max_duration = excluded.clip_max_duration,
            updated_at = CURRENT_TIMESTAMP
    """, (
        ip,
        port,
        http_port,
        user,
        encrypt_credential(password),
        subtype,
        image_profile_mode,
        night_start_time,
        night_end_time,
        int(clip_min_duration),
        int(clip_max_duration),
    ))
    conn.commit()
    conn.close()

def save_image_profile_mode(mode: str, start_time: str | None = None, end_time: str | None = None):
    conn = get_connection()
    cursor = conn.cursor()
    if start_time and end_time:
        cursor.execute("""
            UPDATE camera_config
            SET image_profile_mode = ?, night_start_time = ?, night_end_time = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (mode, start_time, end_time))
    else:
        cursor.execute("""
            UPDATE camera_config
            SET image_profile_mode = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (mode,))
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

    # Remove também da fila de sincronização SQLite para não sobrar na fila
    try:
        conn = get_connection()
        c = conn.cursor()
        for p in [snapshot_path, video_path]:
            if p:
                c.execute("DELETE FROM gdrive_sync_queue WHERE filepath = ?", (p,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Erro ao remover arquivo da fila de sincronização: {e}")

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

def enqueue_sync_file(filepath: str, file_type: str = "snapshot", description: str = ""):
    """Registra um arquivo na fila persistente de sincronização offline com o Google Drive."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO gdrive_sync_queue (filepath, file_type, description, status)
        VALUES (?, ?, ?, 'pending')
    """, (filepath, file_type, description))
    conn.commit()
    conn.close()

def mark_file_synced(filepath: str):
    """Marca um arquivo como sincronizado com sucesso no Google Drive."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE gdrive_sync_queue
        SET status = 'synced', synced_at = CURRENT_TIMESTAMP
        WHERE filepath = ?
    """, (filepath,))
    conn.commit()
    conn.close()

def increment_sync_retry(filepath: str):
    """Incrementa contador de tentativas de sincronização para um arquivo."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE gdrive_sync_queue
        SET retry_count = retry_count + 1
        WHERE filepath = ?
    """, (filepath,))
    conn.commit()
    conn.close()

def delete_sync_file(filepath: str):
    """Remove um registro da fila de sincronização (ex: arquivo descartado/apagado)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM gdrive_sync_queue WHERE filepath = ?", (filepath,))
    conn.commit()
    conn.close()

def get_pending_sync_files(limit: int = 50) -> list[dict]:
    """Retorna arquivos locais que aguardam upload para o Google Drive."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM gdrive_sync_queue
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_all_synced_filepaths() -> set[str]:
    """Retorna um conjunto com os nomes de todos os arquivos já marcados como sincronizados."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filepath FROM gdrive_sync_queue WHERE status = 'synced'")
    rows = cursor.fetchall()
    conn.close()
    return {row["filepath"] for row in rows}

def get_unsynced_detection_events() -> list[dict]:
    """Retorna caminhos de snapshots e vídeos de eventos que não constam como sincronizados na fila."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.id, e.snapshot_path, e.video_path, e.confidence, e.timestamp
        FROM detection_events e
        WHERE (
            e.snapshot_path IS NOT NULL AND e.snapshot_path NOT IN (
                SELECT filepath FROM gdrive_sync_queue WHERE status = 'synced'
            )
        ) OR (
            e.video_path IS NOT NULL AND e.video_path NOT IN (
                SELECT filepath FROM gdrive_sync_queue WHERE status = 'synced'
            )
        )
        ORDER BY e.id DESC
        LIMIT 100
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def is_file_synced_or_untracked(filepath: str) -> bool:
    """
    Verifica se o arquivo já foi sincronizado com sucesso no Google Drive.
    Se o Google Drive estiver desativado ou o arquivo não constar na fila, retorna True para liberar limpeza.
    """
    if not settings.GOOGLE_DRIVE_ENABLED:
        return True
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM gdrive_sync_queue WHERE filepath = ?", (filepath,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return True
    return row["status"] == "synced"

def cleanup_retention(retention_days: int = 15):
    """
    Remove eventos, snapshots e vídeos locais mais antigos que retention_days (padrão 15 dias).
    Proteção Offline: Arquivos locais NÃO são apagados se o upload para o Google Drive ainda estiver pendente!
    """
    cutoff = (get_now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT snapshot_path, video_path FROM detection_events WHERE timestamp < ?", (cutoff,))
    old_records = cursor.fetchall()

    for rec in old_records:
        snap = rec["snapshot_path"]
        vid = rec["video_path"]

        # Proteção: só apaga snapshot se confirmado no Google Drive (ou Drive desativado)
        if snap:
            if is_file_synced_or_untracked(snap):
                for base in ["snapshots", "backend/snapshots"]:
                    target = os.path.join(base, snap)
                    if os.path.exists(target):
                        try:
                            os.remove(target)
                        except Exception:
                            pass
            else:
                logger.warning(f"[Retenção Local] Mantendo snapshot ({snap}) pois upload para Google Drive ainda está pendente.")

        # Proteção: só apaga vídeo se confirmado no Google Drive (ou Drive desativado)
        if vid:
            if is_file_synced_or_untracked(vid):
                for base in ["recordings", "backend/recordings"]:
                    target = os.path.join(base, vid)
                    if os.path.exists(target):
                        try:
                            os.remove(target)
                        except Exception:
                            pass
            else:
                logger.warning(f"[Retenção Local] Mantendo vídeo ({vid}) pois upload para Google Drive ainda está pendente.")

    cursor.execute("DELETE FROM detection_events WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount
    
    # Limpa registros antigos da fila de sync para manter tabela enxuta
    cursor.execute("DELETE FROM gdrive_sync_queue WHERE status = 'synced' AND created_at < ?", (cutoff,))
    
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info(f"Limpeza de retenção local ({retention_days} dias): {deleted} registros antigos removidos com proteção de nuvem.")

def cleanup_synced_local_files(older_than_hours: int = 24) -> int:
    """
    Libera espaço do disco local diariamente apagando APENAS os arquivos físicos (.jpg e .mp4)
    de snapshots e gravações que já foram sincronizados com sucesso no Google Drive
    e têm mais de older_than_hours (padrão 24h = 1 dia).
    Mantém os registros em detection_events e gdrive_sync_queue intactos para que a
    Galeria continue exibindo tudo direto da nuvem.
    """
    if not settings.GOOGLE_DRIVE_ENABLED:
        return 0

    synced_files = get_all_synced_filepaths()
    if not synced_files:
        return 0

    now_ts = time.time()
    min_age_seconds = max(0, older_than_hours) * 3600
    removed_count = 0

    scan_dirs = [
        "snapshots",
        os.path.join("backend", "snapshots"),
        os.path.join("..", "snapshots"),
        "recordings",
        os.path.join("backend", "recordings"),
        os.path.join("..", "recordings"),
    ]
    seen_realpaths = set()
    for folder in scan_dirs:
        if not os.path.exists(folder):
            continue
        try:
            entries = os.listdir(folder)
        except Exception:
            continue

        for fname in entries:
            if not fname.lower().endswith((".jpg", ".jpeg", ".mp4")):
                continue
            if fname not in synced_files:
                continue

            fpath = os.path.join(folder, fname)
            try:
                real_p = os.path.abspath(fpath)
                if real_p in seen_realpaths:
                    continue
                seen_realpaths.add(real_p)

                stat = os.stat(fpath)
                if (now_ts - stat.st_mtime) >= min_age_seconds:
                    os.remove(fpath)
                    removed_count += 1
            except OSError:
                continue

    if removed_count > 0:
        logger.info(
            f"[Limpeza Diária Disco] {removed_count} snapshot(s)/vídeo(s) local(is) já sincronizados no Google Drive foram removidos do disco local."
        )
    return removed_count

def get_whatsapp_config(decrypt_key: bool = False) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM whatsapp_config WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {
            "enabled": False,
            "api_url": "",
            "instance_name": "",
            "target_number": "",
            "send_photo": True,
            "send_video": True,
            "has_key": False,
            "api_key": "",
        }
    encrypted_key = row["api_key_encrypted"] or ""
    has_key = bool(encrypted_key)
    api_key_val = ""
    if has_key:
        api_key_val = decrypt_credential(encrypted_key) if decrypt_key else "••••••••"
    return {
        "enabled": bool(row["enabled"]),
        "api_url": row["api_url"] or "",
        "instance_name": row["instance_name"] or "",
        "target_number": row["target_number"] or "",
        "send_photo": bool(row["send_photo"]),
        "send_video": bool(row["send_video"]),
        "has_key": has_key,
        "api_key": api_key_val,
    }

def save_whatsapp_config(
    enabled: bool,
    api_url: str,
    instance_name: str,
    target_number: str,
    send_photo: bool = True,
    send_video: bool = True,
    api_key: str = None,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT api_key_encrypted FROM whatsapp_config WHERE id = 1")
    existing = cursor.fetchone()
    existing_encrypted = existing["api_key_encrypted"] if existing else ""

    if api_key and api_key.strip() and api_key != "••••••••":
        new_encrypted = encrypt_credential(api_key.strip())
    else:
        new_encrypted = existing_encrypted

    cursor.execute("""
        INSERT INTO whatsapp_config (id, enabled, api_url, api_key_encrypted, instance_name, target_number, send_photo, send_video, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            enabled = excluded.enabled,
            api_url = excluded.api_url,
            api_key_encrypted = excluded.api_key_encrypted,
            instance_name = excluded.instance_name,
            target_number = excluded.target_number,
            send_photo = excluded.send_photo,
            send_video = excluded.send_video,
            updated_at = CURRENT_TIMESTAMP
    """, (
        1 if enabled else 0,
        api_url.strip(),
        new_encrypted,
        instance_name.strip(),
        target_number.strip(),
        1 if send_photo else 0,
        1 if send_video else 0,
    ))
    conn.commit()
    conn.close()
    logger.info("Configurações do WhatsApp (Evolution API) atualizadas com sucesso.")

# ==============================================================================
# Gravação 24h (DVR Contínuo 15 dias + Google Drive Subpasta Gravacoes_24h)
# ==============================================================================

def get_dvr_config() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dvr_config WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {
            "enabled": True,
            "segment_seconds": 900,
            "retention_days": 15,
            "subtype": 0,
        }
    return {
        "enabled": bool(row["enabled"]),
        "segment_seconds": int(row["segment_seconds"]),
        "retention_days": int(row["retention_days"]),
        "subtype": int(row["subtype"]),
    }

def save_dvr_config(enabled: bool, segment_seconds: int = 900, retention_days: int = 15, subtype: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO dvr_config (id, enabled, segment_seconds, retention_days, subtype, updated_at)
        VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            enabled = excluded.enabled,
            segment_seconds = excluded.segment_seconds,
            retention_days = excluded.retention_days,
            subtype = excluded.subtype,
            updated_at = CURRENT_TIMESTAMP
    """, (1 if enabled else 0, segment_seconds, retention_days, subtype))
    conn.commit()
    conn.close()

def upsert_dvr_segment(
    filename: str,
    start_time: str,
    end_time: str | None = None,
    duration_seconds: int = 300,
    size_bytes: int = 0,
    status: str = "local",
    drive_file_id: str | None = None,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO dvr_segments (filename, start_time, end_time, duration_seconds, size_bytes, status, drive_file_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(filename) DO UPDATE SET
            start_time = excluded.start_time,
            end_time = COALESCE(excluded.end_time, dvr_segments.end_time),
            duration_seconds = CASE
                WHEN excluded.duration_seconds > 0 THEN excluded.duration_seconds
                ELSE dvr_segments.duration_seconds
            END,
            size_bytes = CASE
                WHEN excluded.size_bytes > 0 THEN excluded.size_bytes
                ELSE dvr_segments.size_bytes
            END,
            status = CASE
                WHEN dvr_segments.status = 'synced' AND excluded.status != 'recording' THEN 'synced'
                ELSE excluded.status
            END,
            drive_file_id = COALESCE(excluded.drive_file_id, dvr_segments.drive_file_id)
    """, (filename, start_time, end_time, duration_seconds, size_bytes, status, drive_file_id))
    cursor.execute("SELECT id FROM dvr_segments WHERE filename = ?", (filename,))
    row = cursor.fetchone()
    seg_id = row["id"] if row else cursor.lastrowid
    conn.commit()
    conn.close()
    return seg_id

def mark_dvr_segment_synced(filename: str, drive_file_id: str | None = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE dvr_segments
        SET status = 'synced',
            drive_file_id = COALESCE(?, drive_file_id)
        WHERE filename = ?
    """, (drive_file_id, filename))
    conn.commit()
    conn.close()

def _build_dvr_filter(date_str: str | None = None, hour_str: str | None = None):
    clauses = []
    params = []
    if date_str and hour_str is not None and str(hour_str).strip() != "":
        h = str(hour_str).strip().zfill(2)
        clauses.append("start_time LIKE ?")
        params.append(f"{date_str} {h}:%")
    elif date_str:
        clauses.append("start_time LIKE ?")
        params.append(f"{date_str}%")
    elif hour_str is not None and str(hour_str).strip() != "":
        h = str(hour_str).strip().zfill(2)
        clauses.append("substr(start_time, 12, 2) = ?")
        params.append(h)

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params

def list_dvr_segments(
    date_str: str | None = None,
    hour_str: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    where_sql, params = _build_dvr_filter(date_str, hour_str)
    query = f"""
        SELECT * FROM dvr_segments
        {where_sql}
        ORDER BY start_time DESC
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, (*params, limit, offset))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def count_dvr_segments(date_str: str | None = None, hour_str: str | None = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    where_sql, params = _build_dvr_filter(date_str, hour_str)
    query = f"SELECT COUNT(*) as count FROM dvr_segments {where_sql}"
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()
    return row["count"] if row else 0

def list_dvr_available_dates() -> list[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT substr(start_time, 1, 10) AS dt
        FROM dvr_segments
        WHERE start_time IS NOT NULL AND length(start_time) >= 10 AND status != 'failed'
        ORDER BY dt DESC
        LIMIT 15
    """)
    dates = [row["dt"] for row in cursor.fetchall() if row["dt"]]
    conn.close()
    return dates

def list_dvr_hours_summary(date_str: str | None = None) -> list[dict]:
    """
    Retorna resumo agrupado por hora cheia (00h a 23h) para o dia selecionado:
    total de blocos, duração somada, tamanho e se está em gravação ou na nuvem.
    Permite à interface exibir a 'HORA TODA' consolidada ao invés de minutos soltos.
    """
    if not date_str:
        date_str = get_now().strftime("%Y-%m-%d")
    clean_date = date_str.strip()[:10]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            substr(start_time, 12, 2) AS hour,
            COUNT(*) as total_blocks,
            SUM(COALESCE(duration_seconds, 60)) as total_duration,
            SUM(COALESCE(size_bytes, 0)) as total_bytes,
            MIN(start_time) as first_block_start,
            MAX(start_time) as last_block_start,
            MAX(CASE WHEN drive_file_id IS NOT NULL THEN 1 ELSE 0 END) as has_drive,
            MAX(CASE WHEN status = 'recording' THEN 1 ELSE 0 END) as is_recording
        FROM dvr_segments
        WHERE start_time LIKE ? AND status != 'failed'
        GROUP BY hour
        ORDER BY hour DESC
    """, (f"{clean_date}%",))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def _is_dvr_file_available(filename: str, drive_file_id: str | None = None) -> bool:
    """Verifica se o arquivo do segmento realmente existe no disco local ou no Google Drive."""
    if drive_file_id:
        return True
    if not filename:
        return False
    search_dirs = [
        "/tmp/x9_dvr_buffer",
        "recordings_24h",
        os.path.join("backend", "recordings_24h"),
        os.path.join("..", "recordings_24h"),
    ]
    for folder in search_dirs:
        target = os.path.join(folder, filename)
        if os.path.exists(target) and os.path.getsize(target) >= 16384:
            return True
    return False

def find_dvr_segment_for_seek(seconds_ago: int = 30, target_time_str: str | None = None) -> dict | None:
    if target_time_str:
        clean_ts = target_time_str.strip().replace("T", " ").split(".")[0].replace("Z", "")
        try:
            target_dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            target_dt = get_now().replace(tzinfo=None) - timedelta(seconds=seconds_ago)
    else:
        target_dt = get_now().replace(tzinfo=None) - timedelta(seconds=seconds_ago)

    target_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    cursor = conn.cursor()

    # 1. Busca até 20 segmentos próximos anteriores ou iguais a target_str
    cursor.execute("""
        SELECT * FROM dvr_segments
        WHERE start_time <= ? AND status != 'failed'
        ORDER BY start_time DESC
        LIMIT 20
    """, (target_str,))
    candidates = [dict(r) for r in cursor.fetchall()]

    chosen = None
    to_mark_failed = []

    for seg in candidates:
        fname = seg.get("filename") or ""
        drive_id = seg.get("drive_file_id")
        if not _is_dvr_file_available(fname, drive_id):
            if not drive_id and seg.get("status") != "recording":
                to_mark_failed.append(seg.get("id"))
            continue

        try:
            seg_start = datetime.strptime(seg["start_time"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        seg_dur = max(1, int(seg.get("duration_seconds") or 300))
        if seg.get("end_time"):
            try:
                seg_end = datetime.strptime(seg["end_time"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                seg_end = seg_start + timedelta(seconds=seg_dur)
        else:
            seg_end = seg_start + timedelta(seconds=max(seg_dur, 300))

        # Se o segmento está gravando, só pode ser escolhido se target_dt >= seg_start
        if seg.get("status") == "recording":
            if seg_start <= target_dt:
                chosen = seg
                break
        elif seg_start <= target_dt <= seg_end:
            chosen = seg
            break

    # 2. Fallback: se target_dt caiu num intervalo intermediário, pega o segmento válido mais próximo
    if not chosen:
        for seg in candidates:
            if _is_dvr_file_available(seg.get("filename") or "", seg.get("drive_file_id")):
                chosen = seg
                break

    # 3. Fallback absoluto: o mais recente segmento válido do sistema
    if not chosen:
        cursor.execute("SELECT * FROM dvr_segments WHERE status != 'failed' ORDER BY start_time DESC LIMIT 10")
        for r in cursor.fetchall():
            row_dict = dict(r)
            if _is_dvr_file_available(row_dict.get("filename") or "", row_dict.get("drive_file_id")):
                chosen = row_dict
                break

    # Marca no banco arquivos orfãos detectados
    if to_mark_failed:
        placeholders = ",".join("?" for _ in to_mark_failed)
        cursor.execute(f"UPDATE dvr_segments SET status = 'failed' WHERE id IN ({placeholders})", to_mark_failed)
        conn.commit()

    conn.close()
    if not chosen:
        return None

    try:
        seg_start_dt = datetime.strptime(chosen["start_time"], "%Y-%m-%d %H:%M:%S")
        raw_offset = int((target_dt - seg_start_dt).total_seconds())
    except Exception:
        raw_offset = 0

    seg_duration = int(chosen.get("duration_seconds") or 300)
    seek_offset = max(0, raw_offset)
    if chosen.get("status") != "recording" and chosen.get("end_time") and seg_duration > 2:
        seek_offset = min(seek_offset, max(0, seg_duration - 2))
    elif seg_duration > 1:
        seek_offset = min(seek_offset, max(0, seg_duration - 1))

    return {
        "segment": chosen,
        "seek_offset": seek_offset,
    }

def _remove_local_dvr_file(filename: str):
    if not filename:
        return
    for folder in [
        "/tmp/x9_dvr_buffer",
        "recordings_24h",
        os.path.join("backend", "recordings_24h"),
        os.path.join("..", "recordings_24h"),
    ]:
        target = os.path.join(folder, filename)
        if os.path.exists(target):
            try:
                os.remove(target)
            except Exception as e:
                logger.warning(f"Erro ao remover segmento DVR local {target}: {e}")

def delete_dvr_segment(segment_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dvr_segments WHERE id = ?", (segment_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False

    seg = dict(row)
    filename = seg["filename"]
    drive_file_id = seg.get("drive_file_id")

    _remove_local_dvr_file(filename)

    try:
        from app.services.gdrive_service import gdrive_service
        gdrive_service.delete_dvr_file_by_name(filename, drive_file_id=drive_file_id)
    except Exception as e:
        logger.warning(f"Erro ao excluir segmento DVR '{filename}' no Google Drive: {e}")

    cursor.execute("DELETE FROM dvr_segments WHERE id = ?", (segment_id,))
    conn.commit()
    conn.close()
    return True

def cleanup_dvr_retention(retention_days: int = 15) -> int:
    cutoff = (get_now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dvr_segments WHERE start_time < ?", (cutoff,))
    old_rows = [dict(r) for r in cursor.fetchall()]

    deleted_count = 0
    for seg in old_rows:
        if settings.GOOGLE_DRIVE_ENABLED and seg.get("status") != "synced":
            logger.warning(
                f"[Retenção DVR 24h] Mantendo segmento '{seg['filename']}' pois upload para Google Drive ainda está pendente."
            )
            continue

        _remove_local_dvr_file(seg["filename"])
        cursor.execute("DELETE FROM dvr_segments WHERE id = ?", (seg["id"],))
        deleted_count += 1

    conn.commit()
    conn.close()
    if deleted_count > 0:
        logger.info(f"[Retenção DVR 24h] {deleted_count} segmento(s) com mais de {retention_days} dias removido(s).")
    return deleted_count

