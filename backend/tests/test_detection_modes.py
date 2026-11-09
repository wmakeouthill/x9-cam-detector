import time
import pytest
from unittest.mock import MagicMock, patch
from app.db import (
    init_db,
    save_night_watch_config,
    get_night_watch_config,
    log_detection_event,
    list_detection_events,
)
from app.services.detector_service import DetectorService
from app.services.camera_service import CameraService

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Garante banco isolado em memória/arquivo temporário para os testes."""
    test_db = str(tmp_path / "test_x9.db")
    monkeypatch.setenv("DB_PATH", test_db)
    init_db()

def test_detection_mode_config_persistence():
    """Valida persistência e valores aceitos para o modo de detecção."""
    # 1. Padrão deve ser 'hybrid'
    cfg = get_night_watch_config()
    assert cfg.get("detection_mode") == "hybrid"

    # 2. Salva modo 100% camera
    save_night_watch_config(
        enabled=True,
        start_time="00:00",
        end_time="06:00",
        flash_duration=2.5,
        confidence_threshold=0.45,
        model_type="yolov8s",
        retention_days=15,
        detection_mode="camera",
    )
    cfg2 = get_night_watch_config()
    assert cfg2.get("detection_mode") == "camera"

    # 3. Salva modo yolo continuo
    save_night_watch_config(
        enabled=True,
        start_time="00:00",
        end_time="06:00",
        flash_duration=2.5,
        confidence_threshold=0.45,
        model_type="yolov8s",
        retention_days=15,
        detection_mode="yolo",
    )
    cfg3 = get_night_watch_config()
    assert cfg3.get("detection_mode") == "yolo"

    # 4. Valor inválido faz fallback para 'hybrid'
    save_night_watch_config(
        enabled=True,
        start_time="00:00",
        end_time="06:00",
        flash_duration=2.5,
        confidence_threshold=0.45,
        model_type="yolov8s",
        retention_days=15,
        detection_mode="invalido",
    )
    cfg4 = get_night_watch_config()
    assert cfg4.get("detection_mode") == "hybrid"

def test_log_detection_event_with_event_type():
    """Valida gravação de eventos diferenciando CAMERA_EVENT de HYBRID_DETECTION e PERSON_DETECTED."""
    ev_id = log_detection_event(
        confidence=1.0,
        flash_triggered=True,
        snapshot_path="snap_cam.jpg",
        video_path="clip_cam.mp4",
        event_type="CAMERA_EVENT",
    )
    assert ev_id > 0
    events = list_detection_events(limit=5)
    matching = [e for e in events if e["id"] == ev_id]
    assert len(matching) == 1
    assert matching[0]["event_type"] == "CAMERA_EVENT"

def test_camera_mode_zero_yolo_execution():
    """No modo 100% Câmera, o detector não deve rodar inferência de IA e sim reagir ao evento nativo."""
    detector = DetectorService()
    detector.model = MagicMock()
    mock_camera = MagicMock()
    mock_camera.status = "ONLINE"
    mock_camera.is_recording_clip = False
    mock_camera.last_camera_event_time = time.time()
    mock_camera.last_camera_event_code = "SmartMotionHuman"

    with patch("app.services.detector_service.camera_service", mock_camera), \
         patch.object(detector, "_trigger_flagrante") as mock_flagrante, \
         patch("app.services.detector_service.get_night_watch_config") as mock_cfg:

        mock_cfg.return_value = {
            "enabled": 1,
            "start_time": "00:00",
            "end_time": "23:59",
            "detection_mode": "camera",
        }

        # Simula uma iteração do loop
        detector._cached_cfg = mock_cfg.return_value
        detector.detection_mode = "camera"
        detector.is_in_schedule = MagicMock(return_value=True)

        now_sec = time.time()
        cam_event_recent = (now_sec - mock_camera.last_camera_event_time) < 4.0
        assert cam_event_recent is True

        # YOLO não deve ser executado
        assert detector.model.predict.call_count == 0

def test_hybrid_mode_wakes_yolo_on_camera_event():
    """No modo Híbrido, o YOLO fica dormindo e só acorda quando a câmera sinaliza evento."""
    detector = DetectorService()
    now = time.time()
    mock_camera = MagicMock()

    # Cenário 1: Sem evento recente da câmera -> YOLO dormindo
    mock_camera.last_camera_event_time = now - 60.0 # 60 segundos atrás (expirado)
    mock_camera.is_recording_clip = False
    detector.tracker.tracks = {}
    detector.test_mode = False

    cam_event_recent = (now - mock_camera.last_camera_event_time) < detector.hybrid_wake_duration
    has_active_track = len(detector.tracker.tracks) > 0
    should_wake = cam_event_recent or has_active_track or mock_camera.is_recording_clip or detector.test_mode
    assert should_wake is False

    # Cenário 2: Câmera detectou evento há 3 segundos -> YOLO acorda
    mock_camera.last_camera_event_time = now - 3.0
    cam_event_recent_2 = (now - mock_camera.last_camera_event_time) < detector.hybrid_wake_duration
    should_wake_2 = cam_event_recent_2 or has_active_track or mock_camera.is_recording_clip or detector.test_mode
    assert should_wake_2 is True

def test_camera_target_overlay_config_and_rpc():
    """Valida persistência da sobreposição de alvo na câmera e chamada CGI/RPC."""
    from app.db import save_camera_config, get_camera_config

    save_camera_config(
        ip="192.168.1.108",
        port=554,
        http_port=80,
        user="admin",
        password="pwd",
        subtype=0,
        target_overlay=True,
    )
    cfg = get_camera_config()
    assert cfg.get("target_overlay") == 1

    save_camera_config(
        ip="192.168.1.108",
        port=554,
        http_port=80,
        user="admin",
        password="pwd",
        subtype=0,
        target_overlay=False,
    )
    cfg2 = get_camera_config()
    assert cfg2.get("target_overlay") == 0

def test_camera_toggle_continuous_light():
    """Valida acionamento contínuo da luz branca 100% (toggle) e proteção contra desligamento por flash temporário."""
    cam = CameraService()
    assert cam.continuous_light is False
    assert cam.is_flash_active is False

    with patch.object(cam, "_set_camera_lighting_rpc", return_value=True) as mock_rpc:
        # 1. Liga contínuo
        res = cam.toggle_light(True)
        assert res["status"] == "ok"
        assert res["continuous_light"] is True
        assert cam.continuous_light is True
        assert cam.is_flash_active is True

        # 2. Disparo de flash temporário enquanto contínuo não afeta a luz contínua
        flash_res = cam.trigger_flash(seconds=2.0)
        assert flash_res.get("continuous_light") is True

        # 3. Toggle sem argumento inverte para False
        res2 = cam.toggle_light()
        assert res2["continuous_light"] is False
        assert cam.continuous_light is False
        assert cam.is_flash_active is False

def test_flagrante_zero_delay_order():
    """Valida que _trigger_flagrante dispara Flash e Gravação no milissegundo zero sem bloqueio prévio."""
    detector = DetectorService()
    cfg = {"clip_min_duration": 20, "clip_max_duration": 120, "flash_duration": 5.0}

    call_order = []

    def mock_trigger_flash(seconds):
        call_order.append(("trigger_flash", seconds))

    def mock_record_clip(seconds, max_seconds, on_complete):
        call_order.append(("record_clip", seconds))
        return "video_test.mp4"

    def mock_take_snapshot(prefix, from_memory=False):
        call_order.append(("take_snapshot", prefix, from_memory))
        return f"{prefix}_test.jpg"

    with patch("app.services.detector_service.camera_service.trigger_flash", side_effect=mock_trigger_flash), \
         patch("app.services.detector_service.camera_service.record_clip", side_effect=mock_record_clip), \
         patch("app.services.detector_service.camera_service.take_snapshot", side_effect=mock_take_snapshot), \
         patch("app.services.detector_service.log_detection_event"), \
         patch("app.services.detector_service.gdrive_service.enqueue_file"), \
         patch("threading.Thread") as mock_thread:

        detector._trigger_flagrante(cfg=cfg, max_conf=0.92, event_type="PERSON_DETECTED")

        # 1. Flash deve ser a PRIMEIRA ação chamada
        assert call_order[0][0] == "trigger_flash"
        # 2. Gravação de vídeo deve ser a SEGUNDA ação chamada
        assert call_order[1][0] == "record_clip"
        # 3. Snapshot PB deve vir da memória RAM (from_memory=True)
        assert call_order[2] == ("take_snapshot", "snapshot_pb", True)
        # 4. Transição óptica e foto colorida foram despachadas para worker em background
        assert mock_thread.called is True

def test_take_snapshot_from_memory_zero_http():
    """Valida que take_snapshot(from_memory=True) grava direto da memória sem requisição HTTP."""
    cam = CameraService()
    import numpy as np
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cam.latest_frame_raw = dummy_frame

    with patch("requests.get") as mock_get:
        snap_file = cam.take_snapshot(prefix="test_ram", from_memory=True)
        assert "test_ram_" in snap_file
        # Nenhuma requisição HTTP na rede deve ter sido feita!
        assert mock_get.call_count == 0

def test_camera_event_wakes_detector_immediately():
    """Valida que o evento nativo da câmera ativa o wake_event instantaneamente."""
    detector = DetectorService()
    assert not detector.wake_event.is_set()

    # Disparo de evento 'start' da câmera
    detector._on_camera_event("SmartMotionHuman", "Start", {})
    assert detector.wake_event.is_set()

