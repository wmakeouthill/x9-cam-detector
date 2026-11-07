import os
import pytest
import numpy as np
from datetime import timedelta
from unittest.mock import MagicMock, patch
from starlette.requests import Request
from app.core.time import get_now
from app.core.config import settings
from app.db.db import (
    init_db,
    get_dvr_config,
    save_dvr_config,
    upsert_dvr_segment,
    mark_dvr_segment_synced,
    list_dvr_segments,
    count_dvr_segments,
    list_dvr_available_dates,
    find_dvr_segment_for_seek,
    delete_dvr_segment,
    cleanup_dvr_retention,
    log_detection_event,
    list_detection_events,
)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = str(tmp_path / "test_dvr_x9.db")
    monkeypatch.setenv("DB_PATH", test_db)
    init_db()
    yield test_db


def test_dvr_config_defaults_and_update():
    cfg = get_dvr_config()
    assert cfg["enabled"] is True
    assert cfg["segment_seconds"] == 900
    assert cfg["retention_days"] == 15
    assert cfg["subtype"] == 0

    save_dvr_config(enabled=False, segment_seconds=900, retention_days=15, subtype=0)
    updated = get_dvr_config()
    assert updated["enabled"] is False
    assert updated["segment_seconds"] == 900
    assert updated["subtype"] == 0


def test_dvr_segments_crud_and_filters():
    upsert_dvr_segment(
        filename="dvr_20261104_100000.mp4",
        start_time="2026-11-04 10:00:00",
        end_time="2026-11-04 10:05:00",
        duration_seconds=300,
        size_bytes=1024000,
        status="synced",
        drive_file_id="drv_1",
    )
    upsert_dvr_segment(
        filename="dvr_20261105_180000.mp4",
        start_time="2026-11-05 18:00:00",
        end_time="2026-11-05 18:05:00",
        duration_seconds=300,
        size_bytes=2048000,
        status="local",
    )
    upsert_dvr_segment(
        filename="dvr_20261105_190000.mp4",
        start_time="2026-11-05 19:00:00",
        end_time=None,
        duration_seconds=120,
        size_bytes=512000,
        status="recording",
    )

    # Listagem geral ordenada DESC
    all_segs = list_dvr_segments()
    assert len(all_segs) == 3
    assert all_segs[0]["filename"] == "dvr_20261105_190000.mp4"

    # Filtro por data
    nov05 = list_dvr_segments(date_str="2026-11-05")
    assert len(nov05) == 2
    assert count_dvr_segments(date_str="2026-11-05") == 2

    # Filtro por data + hora
    nov05_18h = list_dvr_segments(date_str="2026-11-05", hour_str="18")
    assert len(nov05_18h) == 1
    assert nov05_18h[0]["filename"] == "dvr_20261105_180000.mp4"

    # Datas disponíveis
    dates = list_dvr_available_dates()
    assert dates == ["2026-11-05", "2026-11-04"]

    # Marcar como sincronizado preserva status='synced' mesmo se re-escaneado como 'local'
    mark_dvr_segment_synced("dvr_20261105_180000.mp4", drive_file_id="drv_2")
    upsert_dvr_segment(
        filename="dvr_20261105_180000.mp4",
        start_time="2026-11-05 18:00:00",
        end_time="2026-11-05 18:05:00",
        duration_seconds=300,
        size_bytes=2048000,
        status="local",
    )
    refreshed = list_dvr_segments(date_str="2026-11-05", hour_str="18")[0]
    assert refreshed["status"] == "synced"
    assert refreshed["drive_file_id"] == "drv_2"


def test_find_dvr_segment_for_seek():
    now_dt = get_now().replace(tzinfo=None)
    seg1_start = now_dt - timedelta(seconds=400)
    seg1_end = now_dt - timedelta(seconds=100)
    seg2_start = now_dt - timedelta(seconds=100)

    upsert_dvr_segment(
        filename="dvr_prev.mp4",
        start_time=seg1_start.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=seg1_end.strftime("%Y-%m-%d %H:%M:%S"),
        duration_seconds=300,
        size_bytes=5000000,
        status="local",
    )
    upsert_dvr_segment(
        filename="dvr_curr.mp4",
        start_time=seg2_start.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=None,
        duration_seconds=100,
        size_bytes=1500000,
        status="recording",
    )

    # Voltar 30s deve cair dentro do segmento atual em gravação (offset ~70s)
    res_30s = find_dvr_segment_for_seek(seconds_ago=30)
    assert res_30s is not None
    assert res_30s["segment"]["filename"] == "dvr_curr.mp4"
    assert 65 <= res_30s["seek_offset"] <= 75

    # Voltar 200s (3m20s) deve cair no segmento anterior (offset ~200s a partir de seg1_start)
    res_200s = find_dvr_segment_for_seek(seconds_ago=200)
    assert res_200s is not None
    assert res_200s["segment"]["filename"] == "dvr_prev.mp4"
    assert 195 <= res_200s["seek_offset"] <= 205


def test_cleanup_dvr_retention_15_days(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_ENABLED", True)
    now_dt = get_now().replace(tzinfo=None)
    old_ts = (now_dt - timedelta(days=16)).strftime("%Y-%m-%d %H:%M:%S")
    recent_ts = (now_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Antigo (16 dias) já sincronizado -> DEVE ser removido
    upsert_dvr_segment(
        filename="dvr_old_synced.mp4",
        start_time=old_ts,
        duration_seconds=300,
        status="synced",
        drive_file_id="id_old_synced",
    )
    # 2. Antigo (16 dias) ainda local/pendente -> DEVE ser preservado
    upsert_dvr_segment(
        filename="dvr_old_pending.mp4",
        start_time=old_ts,
        duration_seconds=300,
        status="local",
    )
    # 3. Recente (2 dias) -> DEVE ser preservado
    upsert_dvr_segment(
        filename="dvr_recent.mp4",
        start_time=recent_ts,
        duration_seconds=300,
        status="synced",
    )

    deleted = cleanup_dvr_retention(retention_days=15)
    assert deleted == 1

    remaining = {s["filename"] for s in list_dvr_segments()}
    assert "dvr_old_synced.mp4" not in remaining
    assert "dvr_old_pending.mp4" in remaining
    assert "dvr_recent.mp4" in remaining


def test_isolation_between_gallery_events_and_dvr_24h(monkeypatch):
    from app.services.gdrive_service import gdrive_service, GoogleDriveService

    monkeypatch.setattr(settings, "GOOGLE_DRIVE_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_FOLDER_ID", "root_folder_123")
    monkeypatch.setattr(GoogleDriveService, "is_configured", property(lambda self: True))

    # Simula list_drive_files retornando foto da galeria e subpasta Gravacoes_24h (caso viesse)
    monkeypatch.setattr(
        gdrive_service,
        "list_drive_files",
        lambda: [
            {"id": "f_snap", "name": "snapshot_20261105_120000.jpg", "mimeType": "image/jpeg", "createdTime": "2026-11-05T12:00:00Z"},
            {"id": "f_folder", "name": "Gravacoes_24h", "mimeType": "application/vnd.google-apps.folder", "createdTime": "2026-11-05T00:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        gdrive_service,
        "list_dvr_drive_files",
        lambda: [
            {"id": "f_dvr1", "name": "dvr_20261105_120000.mp4", "mimeType": "video/mp4", "size": "10485760", "createdTime": "2026-11-05T12:00:00Z"},
        ],
    )

    gdrive_service.reconcile_events_from_drive(force=True)
    gdrive_service.reconcile_dvr_from_drive(force=True)

    # Galeria contém APENAS o snapshot, nunca a pasta Gravacoes_24h nem o bloco DVR
    events = list_detection_events(limit=50)
    assert len(events) == 1
    assert events[0]["snapshot_path"] == "snapshot_20261105_120000.jpg"

    # DVR contém APENAS o bloco de 5 min da subpasta Gravacoes_24h
    dvr_segs = list_dvr_segments()
    assert len(dvr_segs) == 1
    assert dvr_segs[0]["filename"] == "dvr_20261105_120000.mp4"
    assert dvr_segs[0]["drive_file_id"] == "f_dvr1"


def test_yolo_annotations_drawn_on_1080p_frame_and_range_streaming(tmp_path):
    from app.services.camera_service import camera_service
    from app.routers.camera import _serve_video_with_range

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    tracks = [{"box": (200, 150, 500, 800), "track_id": 1, "conf": 0.91}]
    annotated = camera_service.draw_tracks_on_frame(frame.copy(), tracks)
    # Deve ter desenhado os pixels da caixa e label no frame 1080p
    assert int(annotated.sum()) > 0

    # Testa suporte a HTTP Range (206 Partial Content) para seek instantâneo em arquivo MP4
    dummy_mp4 = tmp_path / "dvr_20261105_180000.mp4"
    dummy_mp4.write_bytes(b"0123456789ABCDEF" * 100)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/camera/dvr/video/dvr_20261105_180000.mp4",
        "headers": [(b"range", b"bytes=10-25")],
    }
    req = Request(scope)
    resp = _serve_video_with_range(req, filepath=str(dummy_mp4))
    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == "bytes 10-25/1600"
    assert len(resp.body) == 16


def test_clean_audio_filter_and_camera_aac_16khz_rpc(monkeypatch):
    from app.routers.stream import CLEAN_AUDIO_FILTER
    from app.services.camera_service import camera_service

    assert "highpass=f=180" in CLEAN_AUDIO_FILTER
    assert "lowpass=f=3800" in CLEAN_AUDIO_FILTER
    assert "afftdn=nr=18:nf=-30:tn=1" in CLEAN_AUDIO_FILTER
    assert "equalizer=f=2400:t=q:w=1.2:g=5" in CLEAN_AUDIO_FILTER
    assert "acompressor=threshold=-28dB:ratio=3.5:attack=15:release=250:makeup=4" in CLEAN_AUDIO_FILTER
    assert "alimiter=limit=0.88" in CLEAN_AUDIO_FILTER

    calls = []

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeSession:
        def post(self, url, json=None, timeout=5.0):
            calls.append((url, json))
            method = (json or {}).get("method")
            params = (json or {}).get("params") or {}
            if method == "global.login" and not params.get("password"):
                return FakeResp({"session": "sid123", "params": {"realm": "Login", "random": "999"}})
            if method == "global.login" and params.get("password"):
                return FakeResp({"result": True, "session": "sid123"})
            if method == "configManager.getConfig" and params.get("name") == "AudioInputVolume":
                return FakeResp({"result": True, "params": {"table": [80]}})
            if method == "configManager.getConfig" and params.get("name") == "Encode":
                return FakeResp({
                    "result": True,
                    "params": {
                        "table": [
                            {
                                "MainFormat": [{"Audio": {"Compression": "G.711A", "Frequency": 8000}, "AudioEnable": True}],
                                "ExtraFormat": [{"Audio": {"Compression": "G.711A", "Frequency": 8000}, "AudioEnable": True}],
                            }
                        ]
                    },
                })
            return FakeResp({"result": True})

    import requests
    monkeypatch.setattr(requests, "Session", FakeSession)
    ok = camera_service._ensure_optimal_camera_audio_rpc()
    assert ok is True

    set_encode_calls = [
        c[1] for c in calls
        if c[1].get("method") == "configManager.setConfig" and c[1].get("params", {}).get("name") == "Encode"
    ]
    assert len(set_encode_calls) == 1
    updated_table = set_encode_calls[0]["params"]["table"]
    assert updated_table[0]["MainFormat"][0]["Audio"]["Compression"] == "AAC"
    assert updated_table[0]["MainFormat"][0]["Audio"]["Frequency"] == 16000
    assert updated_table[0]["ExtraFormat"][0]["Audio"]["Compression"] == "AAC"
    assert updated_table[0]["ExtraFormat"][0]["Audio"]["Frequency"] == 16000


def test_camera_image_wdr_day_night_and_flash_restore_rpc(monkeypatch):
    from app.services.camera_service import camera_service

    calls = []

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeSession:
        def post(self, url, json=None, timeout=5.0):
            calls.append((url, json))
            method = (json or {}).get("method")
            params = (json or {}).get("params") or {}
            name = params.get("name")
            if method == "global.login" and not params.get("password"):
                return FakeResp({"session": "sid123", "params": {"realm": "Login", "random": "999"}})
            if method == "global.login" and params.get("password"):
                return FakeResp({"result": True, "session": "sid123"})
            if method == "configManager.getConfig":
                if name == "NTP":
                    return FakeResp({"result": True, "params": {"table": {"Enable": False, "Address": "a.ntp.br", "TimeZone": 22}}})
                if name == "VideoInBacklight":
                    return FakeResp({"result": True, "params": {"table": [[{"Mode": "Off", "WideDynamicRange": 50} for _ in range(3)]]}})
                if name == "VideoInExposure":
                    return FakeResp({"result": True, "params": {"table": [[{"Compensation": 50, "GainMax": 50, "WideDynamicRangeMode": 0, "WideDynamicRange": 0} for _ in range(3)]]}})
                if name == "VideoInDayNight":
                    return FakeResp({"result": True, "params": {"table": [[{"Mode": "Color", "Sensitivity": 2, "Delay": 2} for _ in range(3)]]}})
                if name == "LightingScheme":
                    return FakeResp({"result": True, "params": {"table": [[{"LightingMode": "WhiteMode"} for _ in range(3)]]}})
                if name == "Lighting_V2":
                    return FakeResp({
                        "result": True,
                        "params": {
                            "table": [[
                                [
                                    {"LightType": "InfraredLight", "Mode": "Manual", "Correction": 50, "MiddleLight": [{"Light": 100, "Angle": 50}]},
                                    {"LightType": "WhiteLight", "Mode": "Manual", "PercentOfMaxBrightness": 100, "NearLight": [{"Light": 100, "Angle": 50}]},
                                ]
                                for _ in range(3)
                            ]]
                        },
                    })
                if name == "VideoColor":
                    return FakeResp({"result": True, "params": {"table": [[{"Brightness": 50, "Contrast": 50, "Saturation": 50, "Gamma": 50, "TimeSection": "0 00:00:00-24:00:00"} for _ in range(3)]]}})
                if name == "VideoInSharpness":
                    return FakeResp({"result": True, "params": {"table": [[{"Sharpness": 50, "Level": 50} for _ in range(3)]]}})
                if name == "VideoInDenoise":
                    return FakeResp({"result": True, "params": {"table": [[{"2DEnable": True, "2DLevel": 3, "3DType": "Auto", "3DAutoType": {"AutoLevel": 50}} for _ in range(3)]]}})
                if name == "VideoInOptions":
                    return FakeResp({"result": True, "params": {"table": [{"WideDynamicRangeMode": 0, "NightOptions": {}, "NormalOptions": {}}]}})
                if name == "VideoInMode":
                    return FakeResp({"result": True, "params": {"table": [{"Mode": 0, "Config": [0], "TimeSection": []}]}})
            return FakeResp({"result": True})

    import requests
    monkeypatch.setattr(requests, "Session", FakeSession)
    monkeypatch.setattr(camera_service, "_is_night_window", lambda: True)

    ok_img = camera_service._ensure_optimal_camera_image_rpc()
    assert ok_img is True

    set_Configs = {
        c[1]["params"]["name"]: c[1]["params"]["table"]
        for c in calls
        if c[1].get("method") == "configManager.setConfig"
    }
    assert set_Configs["NTP"]["Enable"] is True
    assert set_Configs["VideoInBacklight"][0][0]["Mode"] == "WideDynamic"
    assert set_Configs["VideoInBacklight"][0][0]["WideDynamicRange"] == 38
    assert set_Configs["VideoInBacklight"][0][1]["WideDynamicRange"] == 38
    assert set_Configs["VideoInExposure"][0][0]["Compensation"] == 40
    assert set_Configs["VideoInDayNight"][0][0]["Mode"] == "BlackWhite"
    assert set_Configs["VideoInDayNight"][0][1]["Mode"] == "BlackWhite"
    assert set_Configs["Lighting_V2"][0][0][0]["Mode"] == "Auto"
    assert set_Configs["VideoInMode"][0]["TimeSection"][0][1] == "0 05:00:00-18:59:59"
    assert set_Configs["VideoInMode"][0]["TimeSection"][0][2] == "1 19:00:00-24:00:00"

    # Testa acionamento do flash (on=True): deve desativar WDR e aplicar 6_flash_wdr_off
    calls.clear()
    ok_flash_on = camera_service._set_camera_lighting_rpc(on=True)
    assert ok_flash_on is True
    on_configs = {
        c[1]["params"]["name"]: c[1]["params"]["table"]
        for c in calls
        if c[1].get("method") == "configManager.setConfig"
    }
    assert on_configs["VideoInBacklight"][0][0]["Mode"] == "Off"
    assert on_configs["VideoInExposure"][0][0]["WideDynamicRangeMode"] == 0
    assert on_configs["VideoInOptions"][0]["WideDynamicRangeMode"] == 0
    assert on_configs["VideoColor"][0][0]["Contrast"] == 56
    assert on_configs["VideoColor"][0][0]["Gamma"] == 46
    assert on_configs["VideoColor"][0][0]["Saturation"] == 56

    # Testa restauração pós-flash à noite (on=False): restaura 4_ir_with_wdr38 em todos os slots [0..2]
    calls.clear()
    ok_flash_off = camera_service._set_camera_lighting_rpc(on=False)
    assert ok_flash_off is True
    off_configs = {
        c[1]["params"]["name"]: c[1]["params"]["table"]
        for c in calls
        if c[1].get("method") == "configManager.setConfig"
    }
    assert [p["Mode"] for p in off_configs["VideoInDayNight"][0]] == ["BlackWhite", "BlackWhite", "BlackWhite"]
    assert off_configs["Lighting_V2"][0][0][0]["Mode"] == "Auto"
    assert off_configs["Lighting_V2"][0][0][1]["Mode"] == "Off"
    assert off_configs["VideoInBacklight"][0][0]["Mode"] == "WideDynamic"
    assert off_configs["VideoInBacklight"][0][0]["WideDynamicRange"] == 38
    assert off_configs["VideoInExposure"][0][0]["WideDynamicRangeMode"] == 1
    assert off_configs["VideoInOptions"][0]["WideDynamicRange"] == 38
    assert off_configs["VideoColor"][0][0]["Contrast"] == 62
    assert off_configs["VideoColor"][0][0]["Gamma"] == 40
    assert any(c[1].get("method") == "global.logout" for c in calls)

    # Testa reboot remoto da câmera (CGI fallback para RPC2 magicBox.reboot + logout)
    calls.clear()
    monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw( RuntimeError("CGI indisponível") ))
    res_reboot = camera_service.reboot_camera()
    assert res_reboot["status"] == "rebooting"
    assert res_reboot["method"] == "RPC2 magicBox.reboot"
    assert any(c[1].get("method") == "magicBox.reboot" for c in calls)
    assert any(c[1].get("method") == "global.logout" for c in calls)

def test_dynamic_clip_and_dvr_config_extension():
    from app.services.camera_service import camera_service
    from app.services.dvr_service import dvr_service

    # Verifica status inicial de clip
    assert camera_service.is_recording_clip is False
    assert camera_service.extend_clip(10) is False

    # Testa atualização de dvr config
    st = dvr_service.update_config(segment_seconds=900, retention_days=20, subtype=0)
    assert st["segment_duration"] == 900
    assert st["retention_days"] == 20




