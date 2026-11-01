import os
import time
import queue
import threading
import logging
from datetime import datetime, timedelta, timezone
from app.core.config import settings
from app.db import (
    enqueue_sync_file,
    mark_file_synced,
    increment_sync_retry,
    get_pending_sync_files,
)

logger = logging.getLogger("x9.gdrive")

class GoogleDriveService:
    def __init__(self):
        self._queue = queue.Queue()
        self._worker_thread = None
        self._reconcile_thread = None
        self._running = True
        self.last_cloud_cleanup_time = 0.0
        self._start_workers()
        self._enqueue_existing_pending()

    def _start_workers(self):
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()
        self._reconcile_thread = threading.Thread(target=self._reconciliation_loop, daemon=True)
        self._reconcile_thread.start()

    def _enqueue_existing_pending(self):
        """Na inicialização do backend, enfileira arquivos pendentes salvos no SQLite."""
        try:
            pending = get_pending_sync_files(limit=50)
            for item in pending:
                full_path = self._resolve_full_path(item["filepath"])
                if full_path and os.path.exists(full_path):
                    self._queue.put({
                        "filepath": full_path,
                        "filename": item["filepath"],
                        "description": item.get("description", "")
                    })
        except Exception as e:
            logger.warning(f"[Google Drive] Erro ao recuperar fila pendente no startup: {e}")

    def _resolve_full_path(self, filepath: str) -> str:
        if os.path.isabs(filepath) and os.path.exists(filepath):
            return filepath
        filename = os.path.basename(filepath)
        for base in ["snapshots", "backend/snapshots", "recordings", "backend/recordings", ""]:
            cand = os.path.join(base, filename) if base else filepath
            if os.path.exists(cand):
                return cand
        return ""

    @property
    def is_configured(self) -> bool:
        if not settings.GOOGLE_DRIVE_ENABLED:
            return False
        has_token = os.path.exists("token.json") or os.path.exists(os.path.join("..", "token.json"))
        creds_file = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE
        has_sa = os.path.exists(creds_file) or os.path.exists(os.path.join("..", creds_file))
        return bool((has_token or has_sa) and settings.GOOGLE_DRIVE_FOLDER_ID)

    def upload_file_async(self, filepath: str, description: str = "", file_type: str = "snapshot"):
        """Enfileira um arquivo (snapshot ou vídeo) para upload em segundo plano e persiste no SQLite."""
        if not settings.GOOGLE_DRIVE_ENABLED:
            logger.debug("[Google Drive] Desativado no .env (GOOGLE_DRIVE_ENABLED=false).")
            return

        filename = os.path.basename(filepath)
        full_path = self._resolve_full_path(filepath) or filepath

        # 1. Registra no SQLite para garantir resiliência offline
        try:
            enqueue_sync_file(filename, file_type=file_type, description=description)
        except Exception as e:
            logger.warning(f"[Google Drive] Falha ao registrar na fila SQLite: {e}")

        # 2. Enfileira na memória para tentativa imediata
        self._queue.put({"filepath": full_path, "filename": filename, "description": description})

    def _process_queue(self):
        while self._running:
            try:
                task = self._queue.get(timeout=2)
            except queue.Empty:
                continue

            try:
                result = self._upload_file(task["filepath"], task.get("description", ""))
                if result and result.get("status") == "success":
                    mark_file_synced(task["filename"])
                else:
                    increment_sync_retry(task["filename"])
            except Exception as e:
                increment_sync_retry(task.get("filename", os.path.basename(task.get("filepath", ""))))
                logger.warning(f"[Google Drive Offline] Sem conexão ao enviar {task['filepath']}: {e}. Salvo localmente para sincronização automática.")
            finally:
                self._queue.task_done()

    def _reconciliation_loop(self):
        """Worker em segundo plano: reconcilia pendências a cada 3 minutos e limpa nuvem a cada 24 horas."""
        while self._running:
            time.sleep(180)
            if not self._running:
                break

            if self.is_configured:
                try:
                    self._sync_offline_pending()
                except Exception as e:
                    logger.debug(f"[Google Drive Sync] Ciclo offline pausado (rede indisponível): {e}")

            # Limpeza automática na nuvem (arquivos com mais de 90 dias / 3 meses)
            now_sec = time.time()
            if self.is_configured and (now_sec - self.last_cloud_cleanup_time > 86400):
                try:
                    self.cleanup_old_drive_files(retention_days=90)
                    self.last_cloud_cleanup_time = now_sec
                except Exception as e:
                    logger.warning(f"[Google Drive] Erro na limpeza da nuvem: {e}")

    def _sync_offline_pending(self):
        pending = get_pending_sync_files(limit=20)
        if not pending:
            return

        logger.info(f"[Google Drive Sync] Encontrados {len(pending)} arquivo(s) pendente(s) de envio offline. Iniciando sincronização...")
        service = self._get_service()
        if not service:
            return

        for item in pending:
            filename = item["filepath"]
            full_path = self._resolve_full_path(filename)
            if not full_path or not os.path.exists(full_path):
                logger.warning(f"[Google Drive Sync] Arquivo local '{filename}' não encontrado no disco. Marcando como finalizado.")
                mark_file_synced(filename)
                continue

            try:
                res = self._upload_file(full_path, item.get("description", ""))
                if res and res.get("status") == "success":
                    mark_file_synced(filename)
                    logger.info(f"[Google Drive Sync] [+] Sincronizado com sucesso na nuvem: {filename}")
                else:
                    increment_sync_retry(filename)
            except Exception as err:
                increment_sync_retry(filename)
                logger.warning(f"[Google Drive Sync] Conexão falhou ao subir {filename} ({err}). Interrompendo lote até o próximo ciclo.")
                break

    def _get_service(self):
        """
        Retorna o serviço autenticado da Google Drive API.
        Prioridade 1: OAuth 2.0 (token.json) com a cota pessoal da sua conta @gmail.com.
        Prioridade 2: Service Account (service_account.json).
        """
        scopes = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive.metadata.readonly"
        ]

        # 1. OAuth pessoal (token.json)
        token_file = "token.json"
        if not os.path.exists(token_file) and os.path.exists(os.path.join("..", token_file)):
            token_file = os.path.join("..", token_file)

        if os.path.exists(token_file):
            try:
                from google.oauth2.credentials import Credentials
                from google.auth.transport.requests import Request
                from googleapiclient.discovery import build

                creds = Credentials.from_authorized_user_file(token_file, scopes)
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    with open(token_file, "w", encoding="utf-8") as f:
                        f.write(creds.to_json())
                return build("drive", "v3", credentials=creds)
            except Exception as e:
                logger.warning(f"[Google Drive] Falha ao carregar token.json OAuth: {e}")

        # 2. Service Account
        creds_file = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE
        if not os.path.exists(creds_file) and os.path.exists(os.path.join("..", creds_file)):
            creds_file = os.path.join("..", creds_file)

        if os.path.exists(creds_file):
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
                creds = service_account.Credentials.from_service_account_file(creds_file, scopes=scopes)
                return build("drive", "v3", credentials=creds)
            except Exception as e:
                logger.warning(f"[Google Drive] Falha ao carregar service_account.json: {e}")

        return None

    def _upload_file(self, filepath: str, description: str = "") -> dict:
        # Se o arquivo estiver sendo gerado no momento (ex: MP4 terminando ffmpeg), aguarda até 10s
        for _ in range(10):
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                break
            time.sleep(1)

        if not os.path.exists(filepath):
            logger.warning(f"[Google Drive] Arquivo não encontrado para upload: {filepath}")
            return {"status": "file_not_found"}

        if not settings.GOOGLE_DRIVE_FOLDER_ID:
            logger.info("[Google Drive] GOOGLE_DRIVE_FOLDER_ID não configurado no .env.")
            return {"status": "missing_folder_id"}

        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            logger.warning("[Google Drive] Dependências não instaladas. Execute: pip install google-api-python-client google-auth")
            return {"status": "dependencies_missing"}

        service = self._get_service()
        if not service:
            logger.warning("[Google Drive] Nenhuma credencial válida encontrada (token.json ou service_account.json).")
            return {"status": "missing_credentials"}

        filename = os.path.basename(filepath)
        mime_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"

        # Anti-duplicação: verifica se arquivo com mesmo nome já existe na pasta
        try:
            q_exist = f"name = '{filename}' and '{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
            check_exist = service.files().list(q=q_exist, fields="files(id, webViewLink)").execute()
            existing_files = check_exist.get("files", [])
            if existing_files:
                logger.info(f"[Google Drive] Arquivo '{filename}' já existe na nuvem. ID: {existing_files[0]['id']}")
                return {"status": "success", "file_id": existing_files[0]["id"], "url": existing_files[0].get("webViewLink")}
        except Exception:
            pass

        file_metadata = {
            "name": filename,
            "parents": [settings.GOOGLE_DRIVE_FOLDER_ID],
            "description": description or "X9 Cam Detector - Registro Automático",
        }

        media = MediaFileUpload(filepath, mimetype=mime_type, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()

        logger.info(f"[Google Drive] Sucesso! Arquivo '{filename}' enviado. ID: {file.get('id')}")
        return {"status": "success", "file_id": file.get("id"), "url": file.get("webViewLink")}

    def cleanup_old_drive_files(self, retention_days: int = 90) -> int:
        """
        Exclui arquivos na pasta do Google Drive com mais de retention_days (padrão 90 dias / 3 meses).
        Mantém a nuvem limpa e sustentável.
        """
        if not settings.GOOGLE_DRIVE_ENABLED or not settings.GOOGLE_DRIVE_FOLDER_ID:
            return 0
        service = self._get_service()
        if not service:
            return 0

        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        q = f"createdTime < '{cutoff}' and '{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
        deleted_count = 0

        try:
            res = service.files().list(q=q, fields="files(id, name, createdTime)", pageSize=100).execute()
            files = res.get("files", [])
            for f in files:
                try:
                    service.files().delete(fileId=f["id"]).execute()
                    deleted_count += 1
                    logger.info(f"[Google Drive Nuvem] Arquivo com mais de {retention_days} dias excluído: {f.get('name')} (ID: {f.get('id')})")
                except Exception as del_err:
                    logger.warning(f"[Google Drive Nuvem] Falha ao deletar arquivo {f.get('id')}: {del_err}")
        except Exception as e:
            logger.warning(f"[Google Drive Nuvem] Erro ao consultar arquivos antigos para limpeza: {e}")

        if deleted_count > 0:
            logger.info(f"[Google Drive Nuvem] Limpeza concluída: {deleted_count} arquivo(s) com mais de {retention_days} dias removidos da nuvem.")
        return deleted_count

    def delete_file_by_name(self, filename: str) -> bool:
        """Exclui um arquivo da pasta do Google Drive pelo nome."""
        if not settings.GOOGLE_DRIVE_ENABLED or not settings.GOOGLE_DRIVE_FOLDER_ID:
            return False
        try:
            service = self._get_service()
            if not service:
                return False
            q = f"name = '{filename}' and '{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
            res = service.files().list(q=q, fields="files(id, name)").execute()
            files = res.get("files", [])
            for f in files:
                service.files().delete(fileId=f["id"]).execute()
                logger.info(f"[Google Drive] Arquivo '{filename}' excluído da nuvem (ID: {f['id']})")
            return len(files) > 0
        except Exception as e:
            logger.warning(f"[Google Drive] Falha ao excluir '{filename}' da nuvem: {e}")
            return False

    def test_connection(self) -> dict:
        if not settings.GOOGLE_DRIVE_FOLDER_ID:
            return {
                "ok": False,
                "error": "GOOGLE_DRIVE_FOLDER_ID não configurado no .env.",
                "hint": "Crie uma pasta no Google Drive e coloque o ID no arquivo .env."
            }

        service = self._get_service()
        if not service:
            return {
                "ok": False,
                "error": "Credenciais não encontradas.",
                "hint": "Gere 'token.json' (via python backend/app/auth_gdrive.py) ou salve 'service_account.json'."
            }

        try:
            folder = service.files().get(fileId=settings.GOOGLE_DRIVE_FOLDER_ID, fields="id, name").execute()
            return {
                "ok": True,
                "folder_name": folder.get("name"),
                "message": f"Conectado com sucesso! Pasta '{folder.get('name')}' acessível e pronta para gravações."
            }
        except Exception as e:
            return {
                "ok": False,
                "error": f"Erro de acesso: {e}",
                "hint": "Verifique as permissões de acesso à pasta do Google Drive."
            }

gdrive_service = GoogleDriveService()
