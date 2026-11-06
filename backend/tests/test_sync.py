import os
import pytest
from datetime import timedelta
from app.core.time import get_now
from app.db.db import (
    init_db,
    enqueue_sync_file,
    mark_file_synced,
    increment_sync_retry,
    get_pending_sync_files,
    is_file_synced_or_untracked,
    cleanup_retention,
    cleanup_synced_local_files,
    get_connection,
)
from app.core.config import settings

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = str(tmp_path / "test_sync_x9.db")
    monkeypatch.setenv("DB_PATH", test_db)
    init_db()
    yield test_db

def test_sync_queue_lifecycle():
    enqueue_sync_file("snap_offline_1.jpg", file_type="snapshot", description="Teste Offline 1")
    enqueue_sync_file("video_offline_1.mp4", file_type="recording", description="Teste Offline 2")

    pending = get_pending_sync_files(limit=10)
    assert len(pending) == 2
    assert pending[0]["filepath"] == "snap_offline_1.jpg"
    assert pending[0]["status"] == "pending"
    assert pending[0]["retry_count"] == 0

    increment_sync_retry("snap_offline_1.jpg")
    pending = get_pending_sync_files(limit=10)
    assert pending[0]["retry_count"] == 1

    mark_file_synced("snap_offline_1.jpg")
    pending = get_pending_sync_files(limit=10)
    assert len(pending) == 1
    assert pending[0]["filepath"] == "video_offline_1.mp4"

    # Duplicata não deve crashar
    enqueue_sync_file("video_offline_1.mp4", file_type="recording", description="Duplicata")
    assert len(get_pending_sync_files(limit=10)) == 1

def test_cleanup_retention_protects_offline_pending_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_ENABLED", True)

    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    # 1. Arquivo antigo (16 dias) mas PENDENTE de sync (internet estava fora)
    pending_file = snapshots_dir / "snap_pending_old.jpg"
    pending_file.write_text("dummy image content")

    # 2. Arquivo antigo (16 dias) já SINCRONIZADO
    synced_file = snapshots_dir / "snap_synced_old.jpg"
    synced_file.write_text("dummy image content")

    enqueue_sync_file("snap_pending_old.jpg", file_type="snapshot")
    enqueue_sync_file("snap_synced_old.jpg", file_type="snapshot")
    mark_file_synced("snap_synced_old.jpg")

    assert is_file_synced_or_untracked("snap_pending_old.jpg") is False
    assert is_file_synced_or_untracked("snap_synced_old.jpg") is True

    # Insere eventos de 16 dias atrás no banco
    conn = get_connection()
    c = conn.cursor()
    past_date = (get_now() - timedelta(days=16)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO detection_events (timestamp, confidence, flash_triggered, snapshot_path, video_path)
        VALUES (?, 0.9, 1, 'snap_pending_old.jpg', NULL)
    """, (past_date,))
    c.execute("""
        INSERT INTO detection_events (timestamp, confidence, flash_triggered, snapshot_path, video_path)
        VALUES (?, 0.9, 1, 'snap_synced_old.jpg', NULL)
    """, (past_date,))
    conn.commit()
    conn.close()

    # Executa a retenção de 15 dias
    cleanup_retention(retention_days=15)

    # O arquivo pendente NUNCA deve ser deletado se ainda não foi pro Drive
    assert is_file_synced_or_untracked("snap_pending_old.jpg") is False

def test_orphan_reconciliation_picks_unsynced_events_and_disk(tmp_path, monkeypatch):
    from app.services.gdrive_service import gdrive_service, GoogleDriveService
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_ENABLED", True)

    # 1. Simula arquivo que foi salvo no disco por um processo anterior que caiu
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    orphan_snap = snap_dir / "snapshot_orphan_123.jpg"
    orphan_snap.write_text("dummy")

    # Mocka resolve_full_path para apontar para tmp_path
    original_resolve = gdrive_service._resolve_full_path
    monkeypatch.setattr(
        gdrive_service,
        "_resolve_full_path",
        lambda f: str(orphan_snap) if "orphan" in f else original_resolve(f)
    )

    # 2. Registra evento no banco sem que ele tenha entrado na fila de sync (simula crash pós-detecção)
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO detection_events (timestamp, confidence, flash_triggered, snapshot_path, video_path)
        VALUES ('2026-11-01 10:00:00', 0.95, 1, 'snapshot_orphan_123.jpg', NULL)
    """)
    conn.commit()
    conn.close()

    # Executa a varredura de auto-cura
    gdrive_service._reconcile_orphaned_files()

    # O arquivo órfão agora foi resgatado e está pendente de envio para o Drive
    pending = get_pending_sync_files(limit=100)
    pending_files = [p["filepath"] for p in pending]
    assert "snapshot_orphan_123.jpg" in pending_files

def test_missing_file_never_marked_synced(monkeypatch):
    from app.services.gdrive_service import gdrive_service, GoogleDriveService
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_ENABLED", True)
    monkeypatch.setattr(GoogleDriveService, "is_configured", property(lambda self: True))
    monkeypatch.setattr(gdrive_service, "_get_service", lambda: True)

    enqueue_sync_file("non_existent_file.mp4", file_type="recording")
    pending_before = get_pending_sync_files()
    target_before = next(p for p in pending_before if p["filepath"] == "non_existent_file.mp4")
    assert target_before["retry_count"] == 0

    # Executa ciclo de sync
    gdrive_service._sync_offline_pending()

    # Arquivo inexistente NUNCA deve ser marcado como 'synced'! Deve continuar 'pending' com retry_count incrementado
    pending_after = get_pending_sync_files()
    target_after = next(p for p in pending_after if p["filepath"] == "non_existent_file.mp4")
    assert target_after["status"] == "pending"
    assert target_after["retry_count"] == 1

def test_pre_enqueue_recording_persists_before_upload(monkeypatch):
    from app.services.gdrive_service import gdrive_service
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_ENABLED", True)

    video_name = "video_20261101_crash_test.mp4"
    gdrive_service.enqueue_file(video_name, description="Vídeo gravando", file_type="recording")

    # Verifica se já está no SQLite antes mesmo do upload terminar
    pending = get_pending_sync_files()
    assert any(p["filepath"] == video_name and p["status"] == "pending" for p in pending)


def test_cleanup_synced_local_files_deletes_only_old_synced_and_keeps_db(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_ENABLED", True)
    monkeypatch.chdir(tmp_path)

    snap_dir = tmp_path / "snapshots"
    rec_dir = tmp_path / "recordings"
    snap_dir.mkdir(parents=True, exist_ok=True)
    rec_dir.mkdir(parents=True, exist_ok=True)

    old_synced_snap = snap_dir / "snap_old_synced.jpg"
    old_pending_snap = snap_dir / "snap_old_pending.jpg"
    recent_synced_vid = rec_dir / "vid_recent_synced.mp4"

    old_synced_snap.write_text("img1")
    old_pending_snap.write_text("img2")
    recent_synced_vid.write_text("vid1")

    # Envelhece os dois primeiros arquivos para 26 horas atrás
    old_mtime = time.time() - (26 * 3600)
    os.utime(old_synced_snap, (old_mtime, old_mtime))
    os.utime(old_pending_snap, (old_mtime, old_mtime))

    enqueue_sync_file("snap_old_synced.jpg", file_type="snapshot")
    mark_file_synced("snap_old_synced.jpg")

    enqueue_sync_file("snap_old_pending.jpg", file_type="snapshot")

    enqueue_sync_file("vid_recent_synced.mp4", file_type="recording")
    mark_file_synced("vid_recent_synced.mp4")

    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO detection_events (timestamp, confidence, flash_triggered, snapshot_path, video_path)
        VALUES ('2026-11-04 10:00:00', 0.9, 1, 'snap_old_synced.jpg', 'vid_recent_synced.mp4')
    """)
    conn.commit()
    conn.close()

    removed = cleanup_synced_local_files(older_than_hours=24)
    assert removed == 1
    assert not old_synced_snap.exists()
    assert old_pending_snap.exists()
    assert recent_synced_vid.exists()

    # Registro no banco continua intacto para que a Galeria busque do Google Drive
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM detection_events WHERE snapshot_path = 'snap_old_synced.jpg'").fetchone()[0]
    conn.close()
    assert count == 1

