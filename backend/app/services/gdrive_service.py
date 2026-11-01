import os
import time
import queue
import threading
import logging
from app.core.config import settings

logger = logging.getLogger("x9.gdrive")

class GoogleDriveService:
    def __init__(self):
        self._queue = queue.Queue()
        self._worker_thread = None
        self._running = True
        self._start_worker()

    def _start_worker(self):
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()

    @property
    def is_configured(self) -> bool:
        if not settings.GOOGLE_DRIVE_ENABLED:
            return False
        has_token = os.path.exists("token.json") or os.path.exists(os.path.join("..", "token.json"))
        creds_file = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE
        has_sa = os.path.exists(creds_file) or os.path.exists(os.path.join("..", creds_file))
        return bool((has_token or has_sa) and settings.GOOGLE_DRIVE_FOLDER_ID)

    def upload_file_async(self, filepath: str, description: str = ""):
        """Enfileira um arquivo (snapshot ou vídeo) para upload em segundo plano."""
        if not settings.GOOGLE_DRIVE_ENABLED:
            logger.debug("[Google Drive] Desativado no .env (GOOGLE_DRIVE_ENABLED=false).")
            return

        full_path = filepath
        if not os.path.isabs(filepath):
            if os.path.exists(filepath):
                full_path = filepath
            elif os.path.exists(os.path.join("snapshots", filepath)):
                full_path = os.path.join("snapshots", filepath)
            elif os.path.exists(os.path.join("recordings", filepath)):
                full_path = os.path.join("recordings", filepath)

        self._queue.put({"filepath": full_path, "description": description})

    def _process_queue(self):
        while self._running:
            try:
                task = self._queue.get(timeout=2)
            except queue.Empty:
                continue

            try:
                self._upload_file(task["filepath"], task.get("description", ""))
            except Exception as e:
                logger.error(f"[Google Drive] Erro ao enviar {task['filepath']}: {e}")
            finally:
                self._queue.task_done()

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

        file_metadata = {
            "name": filename,
            "parents": [settings.GOOGLE_DRIVE_FOLDER_ID],
            "description": description or "X9 Cam Detector - Registro Automático",
        }

        media = MediaFileUpload(filepath, mimetype=mime_type, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()

        logger.info(f"[Google Drive] Sucesso! Arquivo '{filename}' enviado. ID: {file.get('id')}")
        return {"status": "success", "file_id": file.get("id"), "url": file.get("webViewLink")}

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
