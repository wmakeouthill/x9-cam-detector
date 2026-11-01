import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from app.routers.camera import change_stream_subtype, SubtypeRequest

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
