import pytest
from app.db.db import (
    init_db,
    save_camera_config,
    get_camera_config,
    save_night_watch_config,
    get_night_watch_config,
    log_detection_event,
    list_detection_events,
    count_detection_events,
    delete_detection_event,
    delete_detection_events_batch,
)
from app.core.crypto import encrypt_credential, decrypt_credential

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = str(tmp_path / "test_x9.db")
    monkeypatch.setenv("DB_PATH", test_db)
    init_db()
    yield test_db

def test_credential_encryption():
    secret = "minha_senha_super_secreta_#123"
    encrypted = encrypt_credential(secret)
    assert encrypted != secret
    decrypted = decrypt_credential(encrypted)
    assert decrypted == secret

def test_database_init_and_crud():
    init_db()
    save_camera_config("192.168.1.200", 554, 80, "admin", "p@ssword!#", 1)
    cfg = get_camera_config()
    assert cfg["ip"] == "192.168.1.200"
    assert cfg["password"] == "p@ssword!#"
    assert cfg["subtype"] == 1

    # Testa atualização parcial de configurações individuais sem resetar os demais
    from app.db import update_camera_config_partial
    updated_cfg = update_camera_config_partial(
        night_start_time="23:30",
        night_end_time="06:30",
        clip_min_duration=25,
        clip_max_duration=150,
        target_overlay=0,
    )
    assert updated_cfg["ip"] == "192.168.1.200"
    assert updated_cfg["password"] == "p@ssword!#"
    assert updated_cfg["night_start_time"] == "23:30"
    assert updated_cfg["night_end_time"] == "06:30"
    assert updated_cfg["clip_min_duration"] == 25
    assert updated_cfg["clip_max_duration"] == 150
    assert updated_cfg["target_overlay"] == 0

    save_night_watch_config(True, "00:30", "05:30", 3.0, 0.50, "yolov8s", 15)
    nw = get_night_watch_config()
    assert nw["start_time"] == "00:30"
    assert nw["flash_duration"] == 3.0
    assert nw["retention_days"] == 15

    initial_count = count_detection_events()
    event_id1 = log_detection_event(0.88, True, "snapshot_test_1.jpg")
    event_id2 = log_detection_event(0.92, True, "snapshot_test_2.jpg")
    assert count_detection_events() == initial_count + 2

    events = list_detection_events(limit=10)
    assert len(events) >= 2
    snapshot_paths = [e["snapshot_path"] for e in events]
    assert "snapshot_test_1.jpg" in snapshot_paths or "snapshot_test_2.jpg" in snapshot_paths

    # Test single delete
    deleted = delete_detection_event(event_id1)
    assert deleted is True
    assert count_detection_events() == initial_count + 1

    # Test batch delete
    deleted_count = delete_detection_events_batch([event_id2])
    assert deleted_count == 1
    assert count_detection_events() == initial_count
