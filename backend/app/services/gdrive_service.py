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
    delete_sync_file,
    get_pending_sync_files,
    get_all_synced_filepaths,
    get_unsynced_detection_events,
)

logger = logging.getLogger("x9.gdrive")

class GoogleDriveService:
    DVR_FOLDER_NAME = "Gravacoes_24h"

    def __init__(self):
        self._queue = queue.Queue()
        self._worker_thread = None
        self._reconcile_thread = None
        self._running = True
        self.last_cloud_cleanup_time = 0.0
        self._last_reconcile_time = 0.0
        self._last_dvr_reconcile_time = 0.0
        self._dvr_folder_id: str | None = None
        self._start_workers()
        self._enqueue_existing_pending()

    def start(self):
        """Inicializa ou reativa workers e processa pendências do SQLite após o boot do banco."""
        self._running = True
        self._start_workers()
        self._reconcile_orphaned_files()
        self._enqueue_existing_pending()
        self.reconcile_events_from_drive(force=True)

    def stop(self):
        """Interrompe graciosamente as threads de sincronização."""
        self._running = False

    def sync_now(self) -> dict:
        """Força varredura imediata de órfãos e dispara processamento da fila de envio."""
        if not settings.GOOGLE_DRIVE_ENABLED:
            return {
                "ok": False,
                "message": "Google Drive está desativado no .env (GOOGLE_DRIVE_ENABLED=false)."
            }

        if not self.is_configured:
            return {
                "ok": False,
                "message": "Google Drive não está totalmente configurado (verifique Folder ID ou credenciais)."
            }

        # 1. Resgata qualquer arquivo órfão no banco ou disco
        self._reconcile_orphaned_files()

        # 2. Reconcilia galeria diretamente com o Google Drive como fonte de verdade
        self.reconcile_events_from_drive(force=True)

        # 3. Enfileira pendentes em memória
        self._enqueue_existing_pending()

        # 4. Dispara sincronização offline imediata em thread background
        threading.Thread(target=self._sync_offline_pending, daemon=True).start()

        pending = get_pending_sync_files(limit=100)
        return {
            "ok": True,
            "message": f"Sincronização iniciada com o Google Drive! {len(pending)} item(ns) na fila de envio.",
            "pending_count": len(pending),
        }

    def _start_workers(self):
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
            self._worker_thread.start()
        if self._reconcile_thread is None or not self._reconcile_thread.is_alive():
            self._reconcile_thread = threading.Thread(target=self._reconciliation_loop, daemon=True)
            self._reconcile_thread.start()

    def _enqueue_existing_pending(self):
        """Na inicialização do backend, enfileira arquivos pendentes salvos no SQLite."""
        try:
            pending = get_pending_sync_files(limit=100)
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
        candidates = [
            "snapshots",
            "backend/snapshots",
            os.path.join("..", "snapshots"),
            "recordings",
            "backend/recordings",
            os.path.join("..", "recordings"),
            "",
        ]
        for base in candidates:
            cand = os.path.join(base, filename) if base else filepath
            if os.path.exists(cand):
                return os.path.abspath(cand)
        return ""

    @property
    def is_configured(self) -> bool:
        if not settings.GOOGLE_DRIVE_ENABLED:
            return False
        has_token = os.path.exists("token.json") or os.path.exists(os.path.join("..", "token.json"))
        creds_file = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE
        has_sa = os.path.exists(creds_file) or os.path.exists(os.path.join("..", creds_file))
        return bool((has_token or has_sa) and settings.GOOGLE_DRIVE_FOLDER_ID)

    def enqueue_file(self, filepath: str, description: str = "", file_type: str = "snapshot"):
        """
        Registra preventivamente um arquivo no SQLite sem enfileirar em memória.
        Garante que gravações em andamento resistam a crash do backend.
        """
        if not settings.GOOGLE_DRIVE_ENABLED:
            return
        filename = os.path.basename(filepath)
        try:
            enqueue_sync_file(filename, file_type=file_type, description=description)
        except Exception as e:
            logger.warning(f"[Google Drive] Falha ao pré-registrar arquivo no SQLite: {e}")

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

    def _reconcile_orphaned_files(self):
        """
        Varredura de Auto-Cura: resgata arquivos no disco ou em detection_events
        que não foram sincronizados (ex: queda do backend durante a gravação/disparo).
        Garante que nada fique 'apenas local'.
        """
        if not settings.GOOGLE_DRIVE_ENABLED:
            return

        try:
            synced_files = get_all_synced_filepaths()

            # 1. Resgata do banco de eventos itens não sincronizados
            unsynced_events = get_unsynced_detection_events()
            for ev in unsynced_events:
                snap = ev.get("snapshot_path")
                vid = ev.get("video_path")
                if snap and snap not in synced_files:
                    full_snap = self._resolve_full_path(snap)
                    if full_snap and os.path.exists(full_snap):
                        enqueue_sync_file(snap, file_type="snapshot", description="Snapshot recuperado por auto-cura")
                if vid and vid not in synced_files:
                    full_vid = self._resolve_full_path(vid)
                    if full_vid and os.path.exists(full_vid):
                        enqueue_sync_file(vid, file_type="recording", description="Vídeo recuperado por auto-cura")

            # 2. Resgata arquivos físicos existentes nos diretórios de snapshots e gravações
            scan_dirs = [
                ("snapshots", "snapshot", "Snapshot local recuperado"),
                ("backend/snapshots", "snapshot", "Snapshot local recuperado"),
                ("recordings", "recording", "Gravação local recuperada"),
                ("backend/recordings", "recording", "Gravação local recuperada"),
            ]
            for folder, ftype, desc in scan_dirs:
                if not os.path.exists(folder):
                    continue
                for entry in os.listdir(folder):
                    if entry.lower().endswith((".jpg", ".jpeg", ".mp4")):
                        if entry not in synced_files:
                            enqueue_sync_file(entry, file_type=ftype, description=desc)
        except Exception as e:
            logger.warning(f"[Google Drive Auto-Cura] Erro ao reconciliar arquivos locais órfãos: {e}")

    def _reconciliation_loop(self):
        """Worker em segundo plano: reconcilia pendências e órfãos no startup e a cada 3 minutos."""
        # Pequena pausa inicial (2s) para boot suave
        time.sleep(2)
        while self._running:
            if self.is_configured:
                try:
                    self._reconcile_orphaned_files()
                    self._sync_offline_pending()
                except Exception as e:
                    logger.debug(f"[Google Drive Sync] Ciclo offline pausado (rede indisponível): {e}")

            # Limpeza automática na nuvem (arquivos com mais de 90 dias / 3 meses e DVR 24h > 15 dias)
            now_sec = time.time()
            if self.is_configured and (now_sec - self.last_cloud_cleanup_time > 86400):
                try:
                    self.cleanup_old_drive_files(retention_days=90)
                    self.cleanup_old_dvr_drive_files(retention_days=15)
                    self.last_cloud_cleanup_time = now_sec
                except Exception as e:
                    logger.warning(f"[Google Drive] Erro na limpeza da nuvem: {e}")

            # Aguarda 180s ou até shutdown
            for _ in range(180):
                if not self._running:
                    break
                time.sleep(1)

    def _is_file_in_drive(self, filename: str) -> bool:
        """Verifica se o arquivo já existe na pasta do Google Drive."""
        try:
            service = self._get_service()
            if not service:
                return False
            q = f"name = '{filename}' and '{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
            res = service.files().list(q=q, fields="files(id)").execute()
            return len(res.get("files", [])) > 0
        except Exception:
            return False

    def _sync_offline_pending(self):
        pending = get_pending_sync_files(limit=50)
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
                # 1. Se o arquivo já está no Drive, confirma como sincronizado
                if self._is_file_in_drive(filename):
                    mark_file_synced(filename)
                    logger.info(f"[Google Drive Sync] Arquivo '{filename}' já confirmado no Drive. Marcando como sincronizado.")
                    continue

                # 2. Se não está no Drive e não existe no disco após retentativas (ex: apagado pelo usuário)
                if item.get("retry_count", 0) >= 3:
                    delete_sync_file(filename)
                    logger.info(f"[Google Drive Sync] Arquivo descartado da fila (não existe no disco nem no Drive): {filename}")
                    continue

                increment_sync_retry(filename)
                logger.warning(f"[Google Drive Sync] Arquivo local '{filename}' ainda indisponível no disco (tentativa {item.get('retry_count', 0) + 1}).")
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
        q = (
            f"createdTime < '{cutoff}' and '{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents "
            f"and mimeType != 'application/vnd.google-apps.folder' and trashed = false"
        )
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

    def _extract_timestamp(self, filename: str, fallback_iso: str | None = None) -> str:
        import re
        match = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", filename)
        if match:
            y, m, d, h, mn, s = match.groups()
            return f"{y}-{m}-{d} {h}:{mn}:{s}"
        if fallback_iso:
            try:
                return fallback_iso.replace("Z", "").split(".")[0].replace("T", " ")
            except Exception:
                pass
        from app.core.time import get_now_str
        return get_now_str()

    def list_drive_files(self) -> list[dict]:
        """Lista todos os arquivos na pasta do Google Drive (ignorando subpastas como Gravacoes_24h)."""
        if not self.is_configured:
            return []
        try:
            service = self._get_service()
            if not service:
                return []
            q = (
                f"'{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents "
                f"and mimeType != 'application/vnd.google-apps.folder' and trashed = false"
            )
            files = []
            page_token = None
            while True:
                res = service.files().list(
                    q=q,
                    fields="nextPageToken, files(id, name, createdTime, size, mimeType, webViewLink)",
                    pageSize=100,
                    pageToken=page_token
                ).execute()
                files.extend(res.get("files", []))
                page_token = res.get("nextPageToken")
                if not page_token:
                    break
            return files
        except Exception as e:
            logger.warning(f"[Google Drive] Erro ao listar arquivos do Drive: {e}")
            return []

    def get_file_bytes(self, filename: str) -> bytes | None:
        """Busca e retorna o conteúdo binário de um arquivo armazenado no Google Drive."""
        if not self.is_configured:
            return None
        try:
            service = self._get_service()
            if not service:
                return None
            q = f"name = '{filename}' and '{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
            res = service.files().list(q=q, fields="files(id)").execute()
            files = res.get("files", [])
            if not files:
                return None
            file_id = files[0]["id"]
            return service.files().get_media(fileId=file_id).execute()
        except Exception as e:
            logger.warning(f"[Google Drive] Erro ao baixar '{filename}': {e}")
            return None

    def reconcile_events_from_drive(self, force: bool = False):
        """
        Sincroniza os eventos da galeria com o Google Drive como FONTE DE VERDADE.
        1. Resgata do Drive qualquer gravação ou foto para detection_events.
        2. Remove de detection_events itens já sincronizados que foram apagados no Drive.
        3. Preserva gravações/fotos locais pendentes (buffer offline).
        """
        if not self.is_configured:
            return

        now = time.time()
        if not force and (now - self._last_reconcile_time) < 10.0:
            return
        self._last_reconcile_time = now

        try:
            drive_files = self.list_drive_files()
            drive_file_names = {f["name"]: f for f in drive_files}

            from app.db import get_connection, mark_file_synced

            conn = get_connection()
            cursor = conn.cursor()

            # Eventos atuais no SQLite
            cursor.execute("SELECT id, snapshot_path, video_path FROM detection_events")
            db_events = [dict(r) for r in cursor.fetchall()]

            known_snapshots = {e["snapshot_path"]: e["id"] for e in db_events if e["snapshot_path"]}
            known_videos = {e["video_path"]: e["id"] for e in db_events if e["video_path"]}

            # 1. Adiciona itens do Drive que ainda não estão em detection_events
            for fname, finfo in drive_file_names.items():
                if finfo.get("mimeType") == "application/vnd.google-apps.folder" or fname.startswith("dvr_"):
                    continue
                ftype = "recording" if fname.lower().endswith(".mp4") else "snapshot"
                if ftype == "snapshot":
                    if fname not in known_snapshots:
                        ts = self._extract_timestamp(fname, finfo.get("createdTime"))
                        flash = 1 if "flash" in fname.lower() else 0
                        cursor.execute("""
                            INSERT INTO detection_events (timestamp, confidence, flash_triggered, snapshot_path, video_path)
                            VALUES (?, 1.0, ?, ?, NULL)
                        """, (ts, flash, fname))
                        known_snapshots[fname] = cursor.lastrowid
                else:
                    if fname not in known_videos:
                        ts = self._extract_timestamp(fname, finfo.get("createdTime"))
                        cursor.execute("""
                            INSERT INTO detection_events (timestamp, confidence, flash_triggered, snapshot_path, video_path)
                            VALUES (?, 1.0, 0, NULL, ?)
                        """, (ts, fname))
                        known_videos[fname] = cursor.lastrowid

                # Marca como sincronizado no mesmo cursor (evita deadlock de conexões SQLite)
                cursor.execute("""
                    INSERT INTO gdrive_sync_queue (filepath, file_type, description, status, synced_at)
                    VALUES (?, ?, 'Sincronizado no Google Drive', 'synced', CURRENT_TIMESTAMP)
                    ON CONFLICT(filepath) DO UPDATE SET status = 'synced', synced_at = CURRENT_TIMESTAMP
                """, (fname, ftype))

            # 2. Remove do banco itens marcados como 'synced' que não existem mais no Drive (foi apagado na nuvem)
            cursor.execute("SELECT filepath FROM gdrive_sync_queue WHERE status = 'synced'")
            synced_paths = {row["filepath"] for row in cursor.fetchall()}

            for ev in db_events:
                snap = ev["snapshot_path"]
                vid = ev["video_path"]
                snap_gone = snap and (snap in synced_paths) and (snap not in drive_file_names)
                vid_gone = vid and (vid in synced_paths) and (vid not in drive_file_names)
                if snap_gone or vid_gone:
                    cursor.execute("DELETE FROM detection_events WHERE id = ?", (ev["id"],))
                    if snap:
                        cursor.execute("DELETE FROM gdrive_sync_queue WHERE filepath = ?", (snap,))
                    if vid:
                        cursor.execute("DELETE FROM gdrive_sync_queue WHERE filepath = ?", (vid,))

            conn.commit()
            conn.close()
            logger.info(f"[Google Drive] Galeria reconciliada com o Drive ({len(drive_files)} arquivo(s) na nuvem).")
        except Exception as e:
            logger.warning(f"[Google Drive] Erro ao reconciliar galeria com Drive: {e}")

    # ==========================================================================
    # Subpasta Exclusiva: Gravacoes_24h (DVR Contínuo 15 dias)
    # ==========================================================================

    def get_or_create_dvr_folder_id(self) -> str | None:
        """Retorna o ID da subpasta 'Gravacoes_24h' dentro de GOOGLE_DRIVE_FOLDER_ID, criando-a se não existir."""
        if not self.is_configured:
            return None
        if self._dvr_folder_id:
            return self._dvr_folder_id

        try:
            service = self._get_service()
            if not service:
                return None

            q = (
                f"name = '{self.DVR_FOLDER_NAME}' "
                f"and mimeType = 'application/vnd.google-apps.folder' "
                f"and '{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents "
                f"and trashed = false"
            )
            res = service.files().list(q=q, fields="files(id, name)").execute()
            files = res.get("files", [])
            if files:
                self._dvr_folder_id = files[0]["id"]
                return self._dvr_folder_id

            folder_meta = {
                "name": self.DVR_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [settings.GOOGLE_DRIVE_FOLDER_ID],
                "description": "X9 Cam Detector - Gravações Contínuas 24h (Retenção 15 dias)",
            }
            created = service.files().create(body=folder_meta, fields="id").execute()
            self._dvr_folder_id = created.get("id")
            logger.info(f"[Google Drive DVR] Subpasta '{self.DVR_FOLDER_NAME}' criada com ID: {self._dvr_folder_id}")
            return self._dvr_folder_id
        except Exception as e:
            logger.warning(f"[Google Drive DVR] Erro ao obter/criar subpasta '{self.DVR_FOLDER_NAME}': {e}")
            return None

    def upload_dvr_segment(self, filepath: str) -> dict:
        """Faz upload resumable de um segmento .mp4 de gravação 24h para a subpasta Gravacoes_24h."""
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return {"status": "file_not_found"}

        if not self.is_configured:
            return {"status": "not_configured"}

        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            return {"status": "dependencies_missing"}

        service = self._get_service()
        if not service:
            return {"status": "missing_credentials"}

        dvr_folder_id = self.get_or_create_dvr_folder_id()
        if not dvr_folder_id:
            return {"status": "missing_dvr_folder"}

        filename = os.path.basename(filepath)

        try:
            q_exist = f"name = '{filename}' and '{dvr_folder_id}' in parents and trashed = false"
            check_exist = service.files().list(q=q_exist, fields="files(id, webViewLink)").execute()
            existing_files = check_exist.get("files", [])
            if existing_files:
                return {
                    "status": "success",
                    "file_id": existing_files[0]["id"],
                    "url": existing_files[0].get("webViewLink"),
                }
        except Exception:
            pass

        file_metadata = {
            "name": filename,
            "parents": [dvr_folder_id],
            "description": "X9 Cam Detector - Gravação Contínua 24h (1080p + YOLO + Áudio)",
        }
        media = MediaFileUpload(filepath, mimetype="video/mp4", resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()
        logger.info(f"[Google Drive DVR] Segmento 24h enviado para '{self.DVR_FOLDER_NAME}': {filename} (ID: {file.get('id')})")
        return {"status": "success", "file_id": file.get("id"), "url": file.get("webViewLink")}

    def list_dvr_drive_files(self) -> list[dict]:
        """Lista todos os vídeos MP4 armazenados na subpasta Gravacoes_24h."""
        if not self.is_configured:
            return []
        try:
            service = self._get_service()
            if not service:
                return []
            dvr_folder_id = self.get_or_create_dvr_folder_id()
            if not dvr_folder_id:
                return []
            q = (
                f"'{dvr_folder_id}' in parents "
                f"and mimeType != 'application/vnd.google-apps.folder' "
                f"and trashed = false"
            )
            files = []
            page_token = None
            while True:
                res = service.files().list(
                    q=q,
                    fields="nextPageToken, files(id, name, createdTime, size, mimeType, webViewLink)",
                    pageSize=100,
                    pageToken=page_token,
                ).execute()
                files.extend(res.get("files", []))
                page_token = res.get("nextPageToken")
                if not page_token:
                    break
            return files
        except Exception as e:
            logger.warning(f"[Google Drive DVR] Erro ao listar segmentos na subpasta '{self.DVR_FOLDER_NAME}': {e}")
            return []

    def get_dvr_file_bytes(self, filename: str, drive_file_id: str | None = None) -> bytes | None:
        """Baixa os bytes de um segmento DVR na subpasta Gravacoes_24h."""
        if not self.is_configured:
            return None
        try:
            service = self._get_service()
            if not service:
                return None

            target_id = drive_file_id
            if not target_id:
                dvr_folder_id = self.get_or_create_dvr_folder_id()
                if not dvr_folder_id:
                    return None
                q = f"name = '{filename}' and '{dvr_folder_id}' in parents and trashed = false"
                res = service.files().list(q=q, fields="files(id)").execute()
                files = res.get("files", [])
                if not files:
                    return None
                target_id = files[0]["id"]

            return service.files().get_media(fileId=target_id).execute()
        except Exception as e:
            logger.warning(f"[Google Drive DVR] Erro ao baixar segmento '{filename}': {e}")
            return None

    def delete_dvr_file_by_name(self, filename: str, drive_file_id: str | None = None) -> bool:
        """Exclui um segmento DVR da subpasta Gravacoes_24h."""
        if not self.is_configured:
            return False
        try:
            service = self._get_service()
            if not service:
                return False

            deleted = False
            if drive_file_id:
                try:
                    service.files().delete(fileId=drive_file_id).execute()
                    deleted = True
                except Exception:
                    pass

            dvr_folder_id = self.get_or_create_dvr_folder_id()
            if dvr_folder_id:
                q = f"name = '{filename}' and '{dvr_folder_id}' in parents and trashed = false"
                res = service.files().list(q=q, fields="files(id, name)").execute()
                for f in res.get("files", []):
                    try:
                        service.files().delete(fileId=f["id"]).execute()
                        deleted = True
                    except Exception:
                        pass
            return deleted
        except Exception as e:
            logger.warning(f"[Google Drive DVR] Falha ao excluir '{filename}' da nuvem: {e}")
            return False

    def cleanup_old_dvr_drive_files(self, retention_days: int = 15) -> int:
        """Exclui arquivos na subpasta Gravacoes_24h com mais de retention_days (padrão 15 dias)."""
        if not self.is_configured:
            return 0
        service = self._get_service()
        if not service:
            return 0
        dvr_folder_id = self.get_or_create_dvr_folder_id()
        if not dvr_folder_id:
            return 0

        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        q = f"createdTime < '{cutoff}' and '{dvr_folder_id}' in parents and trashed = false"
        deleted_count = 0

        try:
            res = service.files().list(q=q, fields="files(id, name, createdTime)", pageSize=100).execute()
            for f in res.get("files", []):
                try:
                    service.files().delete(fileId=f["id"]).execute()
                    deleted_count += 1
                    logger.info(f"[Google Drive DVR] Segmento > {retention_days} dias excluído: {f.get('name')}")
                except Exception as del_err:
                    logger.warning(f"[Google Drive DVR] Falha ao deletar {f.get('id')}: {del_err}")
        except Exception as e:
            logger.warning(f"[Google Drive DVR] Erro ao limpar segmentos antigos na nuvem: {e}")

        return deleted_count

    def reconcile_dvr_from_drive(self, force: bool = False):
        """Sincroniza a tabela dvr_segments com os arquivos presentes na subpasta Gravacoes_24h do Google Drive."""
        if not self.is_configured:
            return

        now = time.time()
        if not force and (now - self._last_dvr_reconcile_time) < 15.0:
            return
        self._last_dvr_reconcile_time = now

        try:
            from app.db import upsert_dvr_segment
            drive_files = self.list_dvr_drive_files()
            for finfo in drive_files:
                fname = finfo.get("name", "")
                if not fname.lower().endswith(".mp4"):
                    continue
                start_ts = self._extract_timestamp(fname, finfo.get("createdTime"))
                try:
                    start_dt = datetime.strptime(start_ts, "%Y-%m-%d %H:%M:%S")
                    end_ts = (start_dt + timedelta(seconds=300)).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    end_ts = None
                size_bytes = int(finfo.get("size") or 0)
                upsert_dvr_segment(
                    filename=fname,
                    start_time=start_ts,
                    end_time=end_ts,
                    duration_seconds=300,
                    size_bytes=size_bytes,
                    status="synced",
                    drive_file_id=finfo.get("id"),
                )
        except Exception as e:
            logger.warning(f"[Google Drive DVR] Erro ao reconciliar segmentos DVR do Drive: {e}")

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
