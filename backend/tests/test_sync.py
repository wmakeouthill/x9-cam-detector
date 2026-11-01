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
