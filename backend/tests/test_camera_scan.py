import pytest
from unittest.mock import patch, MagicMock
from app.services.camera_service import CameraService

def test_parse_subnets_various_formats():
    # Testa IP direto de roteador/modem
    res = CameraService._parse_subnets("192.168.2.1")
    assert res == ["192.168.2"]

    # Testa notação CIDR
    res = CameraService._parse_subnets("192.168.1.0/24")
    assert res == ["192.168.1"]

    # Testa lista separada por vírgula com múltiplos modens
    res = CameraService._parse_subnets("192.168.1.1, 192.168.2.1, 10.0.0.1")
    assert res == ["10.0.0", "192.168.1", "192.168.2"]

    # Testa formato array
    res = CameraService._parse_subnets(["192.168.1", "192.168.2"])
    assert res == ["192.168.1", "192.168.2"]

def test_parse_subnets_fallback():
    # Entrada vazia ou inválida deve retornar sub-redes válidas
    res = CameraService._parse_subnets("")
    assert len(res) >= 1
    assert all(len(s.split(".")) == 3 for s in res)

def test_scan_network_cameras_mocked():
    # Mock do socket para simular duas câmeras encontradas
    def fake_connect_ex(self, addr):
        ip, port = addr
        if ip == "192.168.2.108" and port in (554, 80, 37777):
            return 0 # Porta aberta na câmera Intelbras
        if ip == "192.168.1.50" and port == 80:
            return 0 # Apenas dispositivo HTTP
        return 111 # Conexão recusada

    with patch("socket.socket.connect_ex", fake_connect_ex):
        result = CameraService.scan_network_cameras("192.168.1, 192.168.2")
        assert "cameras" in result
        assert "scanned_subnets" in result
        assert "192.168.1.0/24" in result["scanned_subnets"]
        assert "192.168.2.0/24" in result["scanned_subnets"]

        cams = result["cameras"]
        assert len(cams) >= 2
        # A câmera deve ser priorizada no topo da lista
        top_cam = cams[0]
        assert top_cam["ip"] == "192.168.2.108"
        assert top_cam["name"] == "Intelbras / Dahua"
        assert top_cam["is_camera"] is True
        assert 554 in top_cam["ports"]
        assert 37777 in top_cam["ports"]
