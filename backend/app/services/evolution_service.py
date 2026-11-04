import os
import base64
import logging
import threading
import requests
from typing import Optional, Dict, Any
from app.core.time import get_now_str
from app.db import get_whatsapp_config

logger = logging.getLogger("x9.evolution")

class EvolutionService:
    @staticmethod
    def normalize_number(number: str) -> str:
        """Normaliza o número para formato padrão internacional do WhatsApp (ex: 5511999999999)."""
        if not number:
            return ""
        digits = "".join(c for c in number if c.isdigit())
        # Se usuário colocou 10 ou 11 dígitos (DDD + celular do Brasil sem DDI 55)
        if len(digits) in (10, 11):
            digits = f"55{digits}"
        return digits

    @classmethod
    def parse_numbers(cls, raw: str) -> list[str]:
        """Extrai e normaliza múltiplos números separados por vírgula, ponto-e-vírgula ou quebra de linha."""
        if not raw:
            return []
        parts = [raw]
        for delimiter in [",", ";", "\n", "/"]:
            new_parts = []
            for p in parts:
                new_parts.extend(p.split(delimiter))
            parts = new_parts

        normalized_list = []
        for p in parts:
            clean = cls.normalize_number(p.strip())
            if clean and clean not in normalized_list:
                normalized_list.append(clean)
        return normalized_list

    @classmethod
    def resolve_api_url(cls, api_url: str) -> str:
        """Resolve a URL da Evolution API, traduzindo automaticamente 'localhost' para o container interno quando roda no Docker."""
        url = (api_url or "").strip().rstrip("/")
        if not url:
            return "http://evolution-api:8080"
        
        is_docker = os.path.exists("/.dockerenv") or os.getenv("DOCKER_ENV") == "true"
        if is_docker:
            for prefix in [
                "http://localhost:39082", "http://127.0.0.1:39082",
                "http://localhost:8080", "http://127.0.0.1:8080",
                "http://localhost", "http://127.0.0.1"
            ]:
                if url == prefix or url.startswith(prefix + "/"):
                    return "http://evolution-api:8080"
        return url

    def _resolve_full_path(self, filepath: str) -> str:
        if not filepath:
            return ""
        if os.path.isabs(filepath) and os.path.exists(filepath):
            return filepath
        filename = os.path.basename(filepath)
        for base in ["snapshots", "backend/snapshots", "recordings", "backend/recordings", ""]:
            cand = os.path.join(base, filename) if base else filepath
            if os.path.exists(cand):
                return cand
        return ""

    def list_instances(self, api_url: str, api_key: str) -> list[str]:
        """Retorna os nomes de todas as instâncias existentes na Evolution API."""
        api_url = self.resolve_api_url(api_url)
        if not api_url or not api_key:
            return []
        url = f"{api_url.rstrip('/')}/instance/fetchInstances"
        headers = {"apikey": api_key.strip()}
        try:
            r = requests.get(url, headers=headers, timeout=4.0)
            if r.status_code == 200:
                data = r.json()
                instances = []
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            name = item.get("name") or item.get("instanceName") or (item.get("instance") or {}).get("instanceName")
                            if name:
                                instances.append(str(name).strip())
                return instances
        except Exception as e:
            logger.warning(f"[WhatsApp] Falha ao listar instâncias da Evolution API: {e}")
        return []

    def delete_instance(self, api_url: str, api_key: str, instance_name: str) -> bool:
        """Exclui completamente uma instância na Evolution API para liberar memória e sockets."""
        api_url = self.resolve_api_url(api_url)
        if not api_url or not instance_name:
            return False
        url = f"{api_url.rstrip('/')}/instance/delete/{instance_name.strip()}"
        headers = {"apikey": api_key.strip()}
        try:
            r = requests.delete(url, headers=headers, timeout=5.0)
            return r.status_code in (200, 204)
        except Exception as e:
            logger.warning(f"[WhatsApp] Falha ao deletar instância {instance_name}: {e}")
            return False

    def enforce_single_session(self, api_url: str, api_key: str, active_instance_name: str) -> int:
        """Garante estritamente apenas uma sessão ativa no Evolution API, deletando quaisquer instâncias órfãs/antigas."""
        active = (active_instance_name or "").strip()
        if not active:
            return 0
        all_instances = self.list_instances(api_url, api_key)
        removed = 0
        for inst in all_instances:
            if inst != active:
                logger.info(f"[WhatsApp] Removendo instância órfã '{inst}' para manter sessão única ('{active}').")
                if self.delete_instance(api_url, api_key, inst):
                    removed += 1
        return removed

    def reset_instance(self, api_url: str, api_key: str, instance_name: str) -> Dict[str, Any]:
        """Reseta a sessão do WhatsApp na Evolution API, deletando a instância para interromper loops de QR Code e limpar recursos."""
        api_url = self.resolve_api_url(api_url)
        instance = (instance_name or "").strip()
        if not api_url or not instance:
            return {"success": False, "message": "URL e Nome da Instância são obrigatórios."}
        
        # 1. Limpa instâncias órfãs
        self.enforce_single_session(api_url, api_key, instance)
        # 2. Deleta a própria instância ativa para limpar cache e Baileys
        self.delete_instance(api_url, api_key, instance)
        return {
            "success": True,
            "message": f"Sessão da instância '{instance}' resetada com sucesso. Qualquer geração pendente foi cancelada."
        }

    def test_connection(self, api_url: str, api_key: str, instance_name: str) -> Dict[str, Any]:
        """Testa o status de conexão da instância na Evolution API v2 com timeout ágil de 3.5s."""
        api_url = self.resolve_api_url(api_url)
        if not api_url or not instance_name:
            return {"connected": False, "state": "error", "message": "URL e Nome da Instância são obrigatórios."}
        
        url = f"{api_url.rstrip('/')}/instance/connectionState/{instance_name.strip()}"
        headers = {"apikey": api_key.strip()}
        try:
            r = requests.get(url, headers=headers, timeout=3.5)
            if r.status_code == 200:
                data = r.json()
                # Na Evolution v2: {"instance": {"state": "open"}}
                inst_data = data.get("instance", {})
                state = inst_data.get("state") or data.get("state", "open")
                is_open = (state == "open")
                msg = "WhatsApp conectado e pronto!" if is_open else f"Instância encontrada, mas estado é '{state}' (aguardando QR Code ou reconexão)."
                return {"connected": is_open, "state": state, "message": msg}
            elif r.status_code == 404:
                return {"connected": False, "state": "not_found", "message": f"Instância '{instance_name}' não encontrada na Evolution API."}
            elif r.status_code in (401, 403):
                return {"connected": False, "state": "unauthorized", "message": "API Key inválida ou sem permissão de acesso."}
            else:
                return {"connected": False, "state": f"http_{r.status_code}", "message": f"Erro HTTP {r.status_code} da Evolution API: {r.text[:120]}"}
        except requests.exceptions.Timeout:
            return {"connected": False, "state": "timeout", "message": "Tempo limite esgotado ao contatar a Evolution API (servidor ocupado ou gerando QR Code)."}
        except Exception as e:
            return {"connected": False, "state": "error", "message": f"Falha de rede ao conectar na Evolution API: {str(e)}"}

    def connect_or_get_qrcode(self, api_url: str, api_key: str, instance_name: str) -> Dict[str, Any]:
        """Gera ou recupera o QR Code para conectar a instância do WhatsApp na Evolution API v2, garantindo sessão única."""
        api_url = self.resolve_api_url(api_url)
        if not api_url or not instance_name:
            return {"success": False, "connected": False, "message": "URL e Nome da Instância são obrigatórios."}
        
        base_url = api_url.rstrip("/")
        headers = {
            "apikey": api_key.strip(),
            "Content-Type": "application/json",
        }

        # 1. Garante que qualquer outra sessão órfã seja eliminada do Evolution API
        self.enforce_single_session(api_url, api_key, instance_name)

        # 2. Checa estado atual
        status_res = self.test_connection(api_url, api_key, instance_name)
        if status_res.get("connected"):
            return {
                "success": True,
                "connected": True,
                "state": "open",
                "message": "WhatsApp já está conectado e operacional!",
            }

        # 3. Se a instância não existe (404), cria com WHATSAPP-BAILEYS e qrcode=true
        if status_res.get("state") == "not_found":
            create_url = f"{base_url}/instance/create"
            payload = {
                "instanceName": instance_name.strip(),
                "integration": "WHATSAPP-BAILEYS",
                "qrcode": True,
            }
            try:
                r = requests.post(create_url, headers=headers, json=payload, timeout=7.0)
                if r.status_code in (200, 201):
                    data = r.json()
                    qr_b64 = ""
                    if isinstance(data.get("qrcode"), dict):
                        qr_b64 = data["qrcode"].get("base64") or ""
                    elif isinstance(data.get("base64"), str):
                        qr_b64 = data["base64"]

                    code = data.get("code") or (data["qrcode"].get("code") if isinstance(data.get("qrcode"), dict) else "")

                    if qr_b64:
                        return {
                            "success": True,
                            "connected": False,
                            "state": "connecting",
                            "qrcode": qr_b64,
                            "code": code,
                            "message": "Instância criada com sucesso. Escaneie o QR Code abaixo com seu WhatsApp.",
                        }
                    return {
                        "success": True,
                        "connected": False,
                        "state": "created",
                        "message": "Instância criada. Clique em 'Gerar QR Code' para conectar.",
                    }
                else:
                    return {
                        "success": False,
                        "connected": False,
                        "message": f"Erro ao criar instância na Evolution API (HTTP {r.status_code}): {r.text[:150]}"
                    }
            except Exception as e:
                return {
                    "success": False,
                    "connected": False,
                    "message": f"Falha de conexão ao criar instância: {str(e)}"
                }

        # 4. Se a instância já existe mas está desconectada, busca o QR Code atualizado em /instance/connect/{name}
        connect_url = f"{base_url}/instance/connect/{instance_name.strip()}"
        try:
            r = requests.get(connect_url, headers=headers, timeout=7.0)
            if r.status_code in (200, 201):
                data = r.json()
                qr_b64 = ""
                if isinstance(data.get("qrcode"), dict):
                    qr_b64 = data["qrcode"].get("base64") or ""
                elif isinstance(data.get("base64"), str):
                    qr_b64 = data["base64"]

                code = data.get("code") or (data.get("qrcode", {}).get("code") if isinstance(data.get("qrcode"), dict) else "")

                if qr_b64:
                    return {
                        "success": True,
                        "connected": False,
                        "state": "connecting",
                        "qrcode": qr_b64,
                        "code": code,
                        "message": "Escaneie o QR Code abaixo com seu WhatsApp (Aparelhos Conectados).",
                    }
                elif data.get("instance", {}).get("state") == "open":
                    return {
                        "success": True,
                        "connected": True,
                        "state": "open",
                        "message": "WhatsApp já conectado e operacional!",
                    }
                else:
                    return {
                        "success": True,
                        "connected": False,
                        "state": "waiting",
                        "message": "Aguardando geração do QR Code pela Evolution API. Tente novamente em alguns segundos.",
                    }
            else:
                return {
                    "success": False,
                    "connected": False,
                    "message": f"Erro HTTP {r.status_code} ao solicitar QR Code: {r.text[:150]}"
                }
        except Exception as e:
            return {
                "success": False,
                "connected": False,
                "message": f"Falha de conexão ao buscar QR Code: {str(e)}"
            }

    def send_test_message(self, api_url: str, api_key: str, instance_name: str, target_number: str) -> Dict[str, Any]:
        """Envia mensagem de texto de teste para validar entrega no WhatsApp (para todos os números informados)."""
        api_url = self.resolve_api_url(api_url)
        numbers = self.parse_numbers(target_number)
        if not numbers:
            return {"success": False, "message": "Nenhum número de telefone válido encontrado."}

        url = f"{api_url.rstrip('/')}/message/sendText/{instance_name.strip()}"
        headers = {
            "apikey": api_key.strip(),
            "Content-Type": "application/json",
        }
        text = (
            "🟢 *X9 CAM DETECTOR - TESTE DE ALERTA*\n\n"
            f"📅 Teste realizado em {get_now_str('%d/%m/%Y às %H:%M:%S')}\n"
            "✅ Sua Evolution API está conectada e configurada com sucesso!\n"
            "Quando o Modo Vigia detectar uma pessoa, você receberá aqui a foto com flash e o vídeo de flagrante."
        )

        success_count = 0
        last_error = ""
        for norm_num in numbers:
            payload = {
                "number": norm_num,
                "text": text,
                "delay": 500,
            }
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=12.0)
                if r.status_code in (200, 201):
                    success_count += 1
                else:
                    last_error = f"HTTP {r.status_code}: {r.text[:120]}"
            except Exception as e:
                last_error = str(e)

        if success_count > 0:
            recipients = ", ".join(f"+{n}" for n in numbers)
            return {"success": True, "message": f"Mensagem de teste enviada com sucesso para {success_count} número(s): {recipients}!"}
        return {"success": False, "message": f"Falha ao enviar mensagem de teste: {last_error}"}

    def send_alert_photo_async(self, snapshot_path: str, confidence: float):
        """Envia a foto de flagrante com flash de forma 100% assíncrona em background."""
        threading.Thread(
            target=self._send_photo_worker,
            args=(snapshot_path, confidence),
            daemon=True
        ).start()

    def _send_photo_worker(self, snapshot_path: str, confidence: float):
        try:
            cfg = get_whatsapp_config(decrypt_key=True)
            if not cfg.get("enabled") or not cfg.get("send_photo"):
                return
            
            api_url = self.resolve_api_url(cfg.get("api_url"))
            api_key = cfg.get("api_key")
            instance = cfg.get("instance_name")
            target_number = cfg.get("target_number")

            if not api_url or not api_key or not instance or not target_number:
                logger.warning("[WhatsApp] Configuração incompleta para envio de foto de alerta.")
                return

            numbers = self.parse_numbers(target_number)
            if not numbers:
                return

            full_path = self._resolve_full_path(snapshot_path)
            if not full_path or not os.path.exists(full_path):
                logger.warning(f"[WhatsApp] Arquivo de snapshot não encontrado: {snapshot_path}")
                return

            with open(full_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")

            hora_formatada = get_now_str("%d/%m/%Y às %H:%M:%S")
            caption = (
                "🚨 *X9 CAM DETECTOR - ALERTA DE VIGIA*\n\n"
                f"📅 *Horário:* {hora_formatada}\n"
                f"👤 *Detecção:* Pessoa ({confidence:.1%})\n"
                "💡 *Flash LED acionado e gravação em andamento!*"
            )

            url = f"{api_url.rstrip('/')}/message/sendMedia/{instance.strip()}"
            headers = {
                "apikey": api_key.strip(),
                "Content-Type": "application/json",
            }

            for norm_num in numbers:
                payload = {
                    "number": norm_num,
                    "mediatype": "image",
                    "mimetype": "image/jpeg",
                    "caption": caption,
                    "media": b64_data,
                    "fileName": os.path.basename(full_path) or "flagrante_x9.jpg",
                    "delay": 1000,
                }
                try:
                    r = requests.post(url, headers=headers, json=payload, timeout=20.0)
                    if r.status_code in (200, 201):
                        logger.info(f"[+] Foto de flagrante enviada via WhatsApp para +{norm_num}!")
                    else:
                        logger.warning(f"[-] Falha HTTP {r.status_code} ao enviar foto WhatsApp para +{norm_num}: {r.text[:150]}")
                except Exception as e:
                    logger.error(f"[-] Erro ao enviar foto no WhatsApp para +{norm_num}: {e}")
        except Exception as e:
            logger.error(f"[-] Erro geral no worker de foto WhatsApp: {e}")

    def send_alert_video_async(self, video_path: str, confidence: float):
        """Envia o clipe de vídeo (10s) de forma 100% assíncrona em background."""
        threading.Thread(
            target=self._send_video_worker,
            args=(video_path, confidence),
            daemon=True
        ).start()

    def _send_video_worker(self, video_path: str, confidence: float):
        try:
            cfg = get_whatsapp_config(decrypt_key=True)
            if not cfg.get("enabled") or not cfg.get("send_video"):
                return
            
            api_url = self.resolve_api_url(cfg.get("api_url"))
            api_key = cfg.get("api_key")
            instance = cfg.get("instance_name")
            target_number = cfg.get("target_number")

            if not api_url or not api_key or not instance or not target_number:
                return

            numbers = self.parse_numbers(target_number)
            if not numbers:
                return

            full_path = self._resolve_full_path(video_path)
            if not full_path or not os.path.exists(full_path):
                logger.warning(f"[WhatsApp] Arquivo de vídeo não encontrado: {video_path}")
                return

            with open(full_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")

            hora_formatada = get_now_str("%d/%m/%Y às %H:%M:%S")
            caption = (
                "📹 *X9 CAM DETECTOR - CLIPE DE VÍDEO (10s)*\n\n"
                f"📅 *Horário:* {hora_formatada}\n"
                f"👤 *Detecção:* Pessoa ({confidence:.1%})"
            )

            url = f"{api_url.rstrip('/')}/message/sendMedia/{instance.strip()}"
            headers = {
                "apikey": api_key.strip(),
                "Content-Type": "application/json",
            }

            for norm_num in numbers:
                payload = {
                    "number": norm_num,
                    "mediatype": "video",
                    "mimetype": "video/mp4",
                    "caption": caption,
                    "media": b64_data,
                    "fileName": os.path.basename(full_path) or "gravacao_x9.mp4",
                    "delay": 1200,
                }
                try:
                    r = requests.post(url, headers=headers, json=payload, timeout=40.0)
                    if r.status_code in (200, 201):
                        logger.info(f"[+] Vídeo de flagrante enviado via WhatsApp para +{norm_num}!")
                    else:
                        logger.warning(f"[-] Falha HTTP {r.status_code} ao enviar vídeo WhatsApp para +{norm_num}: {r.text[:150]}")
                except Exception as e:
                    logger.error(f"[-] Erro ao enviar vídeo no WhatsApp para +{norm_num}: {e}")
        except Exception as e:
            logger.error(f"[-] Erro geral no worker de vídeo WhatsApp: {e}")

evolution_service = EvolutionService()
