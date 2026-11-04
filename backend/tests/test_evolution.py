import pytest
from unittest.mock import patch, MagicMock
from app.services.evolution_service import evolution_service
from app.db import save_whatsapp_config, get_whatsapp_config

def test_evolution_normalize_number():
    # 11 dígitos com DDD
    assert evolution_service.normalize_number("11987654321") == "5511987654321"
    # Formatado com máscara
    assert evolution_service.normalize_number("(11) 98765-4321") == "5511987654321"
    # Já com DDI 55
    assert evolution_service.normalize_number("5511987654321") == "5511987654321"
    # Com +55 e espaços
    assert evolution_service.normalize_number("+55 11 98765-4321") == "5511987654321"
    # Vazio
    assert evolution_service.normalize_number("") == ""

def test_evolution_parse_multiple_numbers():
    raw = "(11) 98765-4321, 5511999998888; 11977776666\n11966665555"
    numbers = evolution_service.parse_numbers(raw)
    assert len(numbers) == 4
    assert "5511987654321" in numbers
    assert "5511999998888" in numbers
    assert "5511977776666" in numbers
    assert "5511966665555" in numbers

def test_whatsapp_config_storage_and_encryption():
    save_whatsapp_config(
        enabled=True,
        api_url="http://localhost:8080",
        instance_name="teste-instancia",
        target_number="11999998888",
        send_photo=True,
        send_video=False,
        api_key="super-segredo-123",
    )

    masked = get_whatsapp_config(decrypt_key=False)
    assert masked["enabled"] is True
    assert masked["api_url"] == "http://localhost:8080"
    assert masked["instance_name"] == "teste-instancia"
    assert masked["target_number"] == "11999998888"
    assert masked["send_photo"] is True
    assert masked["send_video"] is False
    assert masked["has_key"] is True
    assert masked["api_key"] == "••••••••"

    decrypted = get_whatsapp_config(decrypt_key=True)
    assert decrypted["api_key"] == "super-segredo-123"

    # Atualiza sem alterar a chave (mantendo a anterior)
    save_whatsapp_config(
        enabled=False,
        api_url="http://localhost:8080",
        instance_name="teste-instancia",
        target_number="11999998888",
        send_photo=True,
        send_video=True,
        api_key="••••••••",
    )
    reloaded = get_whatsapp_config(decrypt_key=True)
    assert reloaded["enabled"] is False
    assert reloaded["api_key"] == "super-segredo-123"
    assert reloaded["send_video"] is True

@patch("requests.get")
def test_evolution_test_connection_open(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"instance": {"state": "open"}}
    mock_get.return_value = mock_resp

    res = evolution_service.test_connection("http://localhost:8080", "secret", "minha-instancia")
    assert res["connected"] is True
    assert res["state"] == "open"

@patch("requests.post")
def test_evolution_send_test_message(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_post.return_value = mock_resp

    res = evolution_service.send_test_message("http://localhost:8080", "secret", "inst", "11987654321")
    assert res["success"] is True
    assert mock_post.called
    call_args = mock_post.call_args
    assert "5511987654321" in call_args[1]["json"]["number"]

@patch("requests.get")
@patch("requests.delete")
def test_evolution_enforce_single_session(mock_delete, mock_get):
    # Simula 3 instâncias existentes no Evolution: 1 ativa ('x9-vigia') e 2 órfãs ('old-session', 'test-session')
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = [
        {"name": "old-session"},
        {"instanceName": "x9-vigia"},
        {"name": "test-session"}
    ]
    mock_get.return_value = mock_get_resp

    mock_del_resp = MagicMock()
    mock_del_resp.status_code = 200
    mock_delete.return_value = mock_del_resp

    removed = evolution_service.enforce_single_session("http://localhost:8080", "secret", "x9-vigia")
    assert removed == 2
    assert mock_delete.call_count == 2
    # Verifica que deletou as duas órfãs e NÃO deletou a ativa
    deleted_urls = [call.args[0] for call in mock_delete.call_args_list]
    assert any("old-session" in u for u in deleted_urls)
    assert any("test-session" in u for u in deleted_urls)
    assert not any("x9-vigia" in u for u in deleted_urls)

@patch("requests.get")
@patch("requests.delete")
def test_evolution_reset_instance(mock_delete, mock_get):
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = [{"name": "x9-vigia"}]
    mock_get.return_value = mock_get_resp

    mock_del_resp = MagicMock()
    mock_del_resp.status_code = 200
    mock_delete.return_value = mock_del_resp

    res = evolution_service.reset_instance("http://localhost:8080", "secret", "x9-vigia")
    assert res["success"] is True
    assert mock_delete.called
    assert "x9-vigia" in mock_delete.call_args[0][0]
