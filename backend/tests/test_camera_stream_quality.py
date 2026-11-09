import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from app.routers.camera import (
    change_stream_subtype,
    SubtypeRequest,
    get_camera_configuration,
    set_target_overlay_endpoint,
    TargetOverlayRequest,
    set_clip_duration_endpoint,
    ClipDurationRequest,
)

def test_get_camera_configuration_persisted():
    with patch("app.routers.camera.get_camera_config") as mock_cfg, \
         patch("app.routers.camera.camera_service") as mock_cam:
        mock_cfg.return_value = {
            "ip": "192.168.1.213",
            "port": 554,
            "http_port": 80,
            "user": "admin",
            "subtype": 1,
            "image_profile_mode": "auto",
            "night_start_time": "19:00",
            "night_end_time": "05:00",
            "clip_min_duration": 20,
            "clip_max_duration": 120,
            "target_overlay": 1,
            "password": "secret_password",
        }
        res = get_camera_configuration(user="admin")
        assert res["ip"] == "192.168.1.213"
        assert res["subtype"] == 1
        assert res["night_start_time"] == "19:00"
        assert res["night_end_time"] == "05:00"
        assert res["target_overlay"] is True
        assert res["has_password"] is True
        assert "password" not in res  # Senha nunca exposta no endpoint

def test_set_target_overlay_endpoint():
    with patch("app.routers.camera.camera_service") as mock_cam:
        mock_cam.set_target_overlay.return_value = {"status": "ok", "target_overlay": False}
        res = set_target_overlay_endpoint(TargetOverlayRequest(target_overlay=False), user="admin")
        assert res["status"] == "ok"
        mock_cam.set_target_overlay.assert_called_with(False)

def test_set_clip_duration_endpoint():
    with patch("app.routers.camera.camera_service") as mock_cam:
        mock_cam.set_clip_duration.return_value = {
            "status": "ok",
            "clip_min_duration": 25,
            "clip_max_duration": 180,
        }
        res = set_clip_duration_endpoint(
            ClipDurationRequest(clip_min_duration=25, clip_max_duration=180),
            user="admin",
        )
        assert res["status"] == "ok"
        mock_cam.set_clip_duration.assert_called_with(25, 180)

def test_set_clip_duration_endpoint_invalid():
    with pytest.raises(HTTPException) as exc:
        set_clip_duration_endpoint(
            ClipDurationRequest(clip_min_duration=30, clip_max_duration=10),
            user="admin",
        )
    assert exc.value.status_code == 400

def test_change_stream_subtype_valid():
    with patch("app.routers.camera.camera_service") as mock_cam, \
         patch("app.routers.camera.get_camera_config") as mock_cfg:
        mock_cam.ip = "192.168.1.230"
        mock_cam.user = "admin"
        mock_cam.port = 554
        mock_cam.http_port = 80
        mock_cfg.return_value = {"password": "test_password"}

        # Subtype 0 (1080p)
        res0 = change_stream_subtype(SubtypeRequest(subtype=0), user="admin")
        assert res0["status"] == "updated"
        assert res0["subtype"] == 0
        assert res0["name"] == "1080p FHD"
        mock_cam.update_config.assert_called_with(
            ip="192.168.1.230",
            user="admin",
            password="test_password",
            port=554,
            http_port=80,
            subtype=0,
        )

        # Subtype 1 (480p)
        res1 = change_stream_subtype(SubtypeRequest(subtype=1), user="admin")
        assert res1["status"] == "updated"
        assert res1["subtype"] == 1
        assert res1["name"] == "480p Substream"

def test_change_stream_subtype_invalid():
    with pytest.raises(HTTPException) as exc:
        change_stream_subtype(SubtypeRequest(subtype=99), user="admin")
    assert exc.value.status_code == 400
