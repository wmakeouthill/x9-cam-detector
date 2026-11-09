import os
import re
import shutil
import time
import tempfile
import threading
import subprocess
import logging
from datetime import datetime, timedelta
from app.core.config import settings
from app.core.time import get_now
from app.db import (
    get_dvr_config,
    save_dvr_config,
    upsert_dvr_segment,
    mark_dvr_segment_synced,
    list_dvr_segments,
    count_dvr_segments,
    cleanup_dvr_retention,
)
from app.services.camera_service import camera_service
from app.services.gdrive_service import gdrive_service
from app.routers.stream import resolve_ffmpeg_bin, CLEAN_AUDIO_FILTER

logger = logging.getLogger("x9.dvr")

# Tamanho mínimo para considerar um MP4 DVR fechado como válido (evita stubs corrompidos de 28 bytes ftyp)
MIN_VALID_SEGMENT_BYTES = 16 * 1024
# Janela máxima de segmentos recentes mantidos em buffer RAM (/tmp/x9_dvr_buffer) após upload para o Drive
RAM_CUSHION_MAX_SEGMENTS = 6
# Bloco máximo: ~3,7 Mbps => 5 min ≈ 140 MB. Acima disso o tmpfs enche e o gravador entra em loop de ffmpeg vazio.
MAX_SEGMENT_SECONDS = 300
# Abaixo disso de espaço livre no buffer, libera blocos já sincronizados (mais antigos primeiro)
MIN_FREE_BUFFER_BYTES = 200 * 1024 * 1024


def _resolve_dvr_buffer_dir() -> str:
    """
    Usa buffer efêmero em RAM (/tmp/x9_dvr_buffer no container Linux) para que
    as gravações 24h NUNCA ocupem nem desgastem o Disco D: do host.
    """
    env_dir = os.getenv("DVR_BUFFER_DIR", "").strip()
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return env_dir
    if os.name != "nt":
        ram_dir = "/tmp/x9_dvr_buffer"
        os.makedirs(ram_dir, exist_ok=True)
        return ram_dir
    fallback = os.path.join(tempfile.gettempdir(), "x9_dvr_buffer")
    os.makedirs(fallback, exist_ok=True)
    return fallback


class DvrService:
    """
    Serviço de Gravação Contínua 24h (100% no Google Drive com retenção de 15 dias).
    - Grava em blocos de 1 minuto (60s) em 1080p com áudio limpo e marcações YOLO (PESSOA #ID).
    - Utiliza buffer efêmero em RAM (/tmp/x9_dvr_buffer) para nunca encher o disco local (D:).
    - Assim que cada bloco fecha, aplica +faststart, envia para a subpasta 'Gravacoes_24h' no Google Drive
      e remove os arquivos antigos do buffer temporário.
    """

    def __init__(self):
        self.recordings_dir = _resolve_dvr_buffer_dir()
        os.makedirs(self.recordings_dir, exist_ok=True)

        self._running = False
        self._recorder_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

        self.enabled = True
        self.segment_seconds = 900
        self.retention_days = 15
        self.subtype = 0
        self.current_segment: str | None = None
        self._is_recording = False
        self._last_cleanup_time = 0.0
        self._faststart_done: set[str] = set()

    def _load_config(self):
        try:
            cfg = get_dvr_config()
            self.enabled = bool(cfg.get("enabled", True))
            self.segment_seconds = min(MAX_SEGMENT_SECONDS, max(60, int(cfg.get("segment_seconds", 60))))
            self.retention_days = int(cfg.get("retention_days", 15))
            self.subtype = int(cfg.get("subtype", 0))
        except Exception as e:
            logger.warning(f"[DVR 24h] Falha ao carregar dvr_config, usando padrão: {e}")

    def start(self):
        if self._running:
            return
        os.makedirs(self.recordings_dir, exist_ok=True)
        self._load_config()

        # Limpa eventuais segmentos órfãos que ficaram com status 'recording' após reinício
        try:
            from app.db import get_connection
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE dvr_segments 
                SET status = 'failed' 
                WHERE status = 'recording' AND drive_file_id IS NULL
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[DVR 24h] Aviso ao limpar status 'recording' órfãos na inicialização: {e}")

        # Garante captura em 1080p (subtype 0) quando DVR 24h está ativo
        if self.enabled and self.subtype == 0 and camera_service.subtype != 0:
            camera_service.subtype = 0

        self._running = True
        self._recorder_thread = threading.Thread(target=self._recorder_loop, daemon=True)
        self._watcher_thread = threading.Thread(target=self._watcher_loop, daemon=True)
        self._recorder_thread.start()
        self._watcher_thread.start()
        logger.info(
            f"[DVR 24h] Serviço iniciado (enabled={self.enabled}, bloco={self.segment_seconds}s, retenção={self.retention_days}d)"
        )

    def stop(self):
        self._running = False
        self._terminate_ffmpeg()
        if self._recorder_thread:
            self._recorder_thread.join(timeout=2)
        if self._watcher_thread:
            self._watcher_thread.join(timeout=2)
        logger.info("[DVR 24h] Serviço encerrado.")

    def _terminate_ffmpeg(self):
        with self._lock:
            proc = self._proc
            self._proc = None
            self._is_recording = False

        if proc:
            try:
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def set_enabled(self, enabled: bool) -> dict:
        self.enabled = bool(enabled)
        save_dvr_config(
            enabled=self.enabled,
            segment_seconds=self.segment_seconds,
            retention_days=self.retention_days,
            subtype=self.subtype,
        )
        if not self.enabled:
            self._terminate_ffmpeg()
            self.current_segment = None
        return self.get_status()

    def update_config(
        self,
        segment_seconds: int | None = None,
        retention_days: int | None = None,
        subtype: int | None = None,
    ) -> dict:
        if segment_seconds is not None:
            self.segment_seconds = min(MAX_SEGMENT_SECONDS, max(60, int(segment_seconds)))
        if retention_days is not None:
            self.retention_days = max(1, int(retention_days))
        if subtype is not None:
            self.subtype = int(subtype)
        save_dvr_config(
            enabled=self.enabled,
            segment_seconds=self.segment_seconds,
            retention_days=self.retention_days,
            subtype=self.subtype,
        )
        logger.info(
            f"[DVR 24h] Configuração atualizada: bloco={self.segment_seconds}s, retenção={self.retention_days}d, subtype={self.subtype}"
        )
        if self._is_recording:
            # Reinicia suavemente o processo de gravação para adotar o novo tamanho de bloco
            self._terminate_ffmpeg()
        return self.get_status()

    def get_status(self) -> dict:
        total = count_dvr_segments()
        return {
            "enabled": self.enabled,
            "recording": bool(self._is_recording and self.enabled),
            "segment_duration": self.segment_seconds,
            "retention_days": self.retention_days,
            "current_segment": self.current_segment if self._is_recording else None,
            "total_segments": total,
            "gdrive_enabled": gdrive_service.is_configured,
            "gdrive_folder_name": gdrive_service.DVR_FOLDER_NAME,
        }

    @staticmethod
    def _parse_filename_start(filename: str) -> datetime | None:
        m = re.search(r"dvr_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", filename)
        if not m:
            return None
        y, mo, d, h, mn, s = (int(v) for v in m.groups())
        try:
            return datetime(y, mo, d, h, mn, s)
        except ValueError:
            return None

    def _build_annotated_ffmpeg_cmd(self, ffmpeg_bin: str, rtsp_url: str, fps: int = 12) -> list[str]:
        output_pattern = os.path.join(self.recordings_dir, "dvr_%Y%m%d_%H%M%S.mp4")
        return [
            ffmpeg_bin,
            "-nostats",
            "-loglevel", "error",
            "-thread_queue_size", "512",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-framerate", str(fps),
            "-i", "pipe:0",
            "-thread_queue_size", "512",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-map", "0:v:0",
            "-map", "1:a:0?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-crf", "27",
            "-maxrate", "1800k",
            "-bufsize", "3600k",
            "-pix_fmt", "yuv420p",
            "-g", str(fps * 2),
            "-keyint_min", str(fps),
            "-sc_threshold", "0",
            "-c:a", "aac",
            "-b:a", "64k",
            "-af", CLEAN_AUDIO_FILTER,
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-reset_timestamps", "1",
            "-strftime", "1",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
            output_pattern,
        ]

    def _build_direct_ffmpeg_cmd(self, ffmpeg_bin: str, rtsp_url: str) -> list[str]:
        output_pattern = os.path.join(self.recordings_dir, "dvr_%Y%m%d_%H%M%S.mp4")
        return [
            ffmpeg_bin,
            "-nostats",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "64k",
            "-af", CLEAN_AUDIO_FILTER,
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-reset_timestamps", "1",
            "-strftime", "1",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
            output_pattern,
        ]

    def _recorder_loop(self):
        use_direct_fallback = False
        while self._running:
            if not self.enabled or camera_service.status != "ONLINE":
                self._terminate_ffmpeg()
                time.sleep(2)
                continue

            os.makedirs(self.recordings_dir, exist_ok=True)
            ffmpeg_bin = resolve_ffmpeg_bin()
            rtsp_url = camera_service.mainstream_rtsp_url if self.subtype == 0 else camera_service.rtsp_url

            fps = 12
            if not use_direct_fallback and camera_service.latest_frame_jpeg is not None:
                cmd = self._build_annotated_ffmpeg_cmd(ffmpeg_bin, camera_service.rtsp_url, fps=fps)
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        bufsize=0,
                    )
                    with self._lock:
                        self._proc = proc
                        self._is_recording = True

                    logger.info(f"[DVR 24h] Gravação contínua 1080p + YOLO + Áudio iniciada em {self.recordings_dir}")
                    started_at = time.time()
                    interval = 1.0 / fps

                    while self._running and self.enabled and camera_service.status == "ONLINE":
                        if proc.poll() is not None:
                            break
                        jpg = camera_service.get_latest_jpeg()
                        if jpg and proc.stdin:
                            try:
                                proc.stdin.write(jpg)
                                proc.stdin.flush()
                            except Exception:
                                break
                        time.sleep(interval)

                    self._terminate_ffmpeg()
                    if (time.time() - started_at) < 3.0 and self.enabled and camera_service.status == "ONLINE":
                        logger.warning("[DVR 24h] Pipeline pipe+YOLO encerrou rápido; alternando para stream RTSP direto.")
                        use_direct_fallback = True
                except Exception as e:
                    logger.warning(f"[DVR 24h] Erro no gravador anotado ({e}), usando fallback RTSP direto.")
                    use_direct_fallback = True
                    self._terminate_ffmpeg()
                    time.sleep(2)
            else:
                cmd = self._build_direct_ffmpeg_cmd(ffmpeg_bin, rtsp_url)
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    with self._lock:
                        self._proc = proc
                        self._is_recording = True

                    logger.info(f"[DVR 24h] Gravação contínua RTSP 1080p iniciada em {self.recordings_dir}")
                    while self._running and self.enabled and camera_service.status == "ONLINE":
                        if proc.poll() is not None:
                            break
                        time.sleep(1)
                    self._terminate_ffmpeg()
                except Exception as e:
                    logger.warning(f"[DVR 24h] Erro ao iniciar FFmpeg DVR: {e}")
                    self._terminate_ffmpeg()
                    time.sleep(3)

            time.sleep(1)

    def scan_local_segments(self):
        """Varre o buffer temporário do DVR e atualiza a tabela dvr_segments."""
        if not os.path.exists(self.recordings_dir):
            return

        try:
            entries = sorted([
                f for f in os.listdir(self.recordings_dir)
                if f.startswith("dvr_") and f.lower().endswith(".mp4")
            ])
        except Exception as e:
            logger.warning(f"[DVR 24h] Erro ao listar {self.recordings_dir}: {e}")
            return

        if not entries:
            self.current_segment = None
            return

        now_dt = get_now().replace(tzinfo=None)
        total = len(entries)

        for idx, fname in enumerate(entries):
            fpath = os.path.join(self.recordings_dir, fname)
            try:
                stat = os.stat(fpath)
                size_bytes = stat.st_size
                mtime_dt = datetime.fromtimestamp(stat.st_mtime)
            except OSError:
                continue

            start_dt = self._parse_filename_start(fname) or mtime_dt
            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            is_newest = (idx == total - 1)
            recently_modified = (now_dt - mtime_dt).total_seconds() < 15.0

            if is_newest and (self._is_recording or recently_modified):
                if size_bytes == 0:
                    continue
                self.current_segment = fname
                elapsed = max(1, int((now_dt - start_dt).total_seconds()))
                upsert_dvr_segment(
                    filename=fname,
                    start_time=start_str,
                    end_time=None,
                    duration_seconds=min(elapsed, self.segment_seconds + 30),
                    size_bytes=size_bytes,
                    status="recording",
                )
            else:
                # Se um segmento fechado ficou com menos de 16 KB (ex: stub ftyp de 28 bytes), remove imediatamente
                if size_bytes < MIN_VALID_SEGMENT_BYTES:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
                    continue

                # Segmento já fechado: garante índice +faststart no cabeçalho para seek/play instantâneo no mobile
                self._remux_faststart_if_needed(fpath, fname)
                try:
                    size_bytes = os.path.getsize(fpath)
                except OSError:
                    pass

                if idx + 1 < total:
                    next_start = self._parse_filename_start(entries[idx + 1])
                else:
                    next_start = None

                if next_start and next_start > start_dt:
                    dur = int((next_start - start_dt).total_seconds())
                else:
                    dur = int(max(1, (mtime_dt - start_dt).total_seconds()))

                if dur <= 0 or dur > self.segment_seconds + 60:
                    dur = self.segment_seconds

                end_dt = start_dt + timedelta(seconds=dur)
                end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
                upsert_dvr_segment(
                    filename=fname,
                    start_time=start_str,
                    end_time=end_str,
                    duration_seconds=dur,
                    size_bytes=size_bytes,
                    status="local",
                )

    def _remux_faststart_if_needed(self, filepath: str, filename: str):
        """
        Reorganiza o átomo moov para o início do MP4 (+faststart) assim que o bloco fecha.
        Faz com que o seek e o play no celular (e no streaming do Google Drive) sejam instantâneos.
        """
        if filename in self._faststart_done:
            return
        self._faststart_done.add(filename)
        try:
            # O remux grava uma cópia temporária: sem folga no tmpfs, é melhor pular que encher o buffer
            if shutil.disk_usage(self.recordings_dir).free < os.path.getsize(filepath) * 2:
                return
        except OSError:
            return
        tmp_path = filepath + ".faststart.mp4"
        try:
            ffmpeg_bin = resolve_ffmpeg_bin()
            res = subprocess.run(
                [
                    ffmpeg_bin,
                    "-nostats",
                    "-loglevel", "error",
                    "-i", filepath,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    "-y",
                    tmp_path,
                ],
                timeout=10,
                capture_output=True,
            )
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                os.replace(tmp_path, filepath)
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _prune_ram_buffer(self):
        """
        Mantém apenas os últimos RAM_CUSHION_MAX_SEGMENTS blocos já sincronizados no buffer RAM
        para permitir o 'Voltar 15s / 30s / 1m / 5m' instantâneo sem consumir armazenamento local.
        """
        if not os.path.exists(self.recordings_dir):
            return
        try:
            files = sorted([
                f for f in os.listdir(self.recordings_dir)
                if f.startswith("dvr_") and f.lower().endswith(".mp4")
            ])
        except Exception:
            return

        try:
            low_space = shutil.disk_usage(self.recordings_dir).free < MIN_FREE_BUFFER_BYTES
        except OSError:
            low_space = False

        if len(files) <= RAM_CUSHION_MAX_SEGMENTS and not low_space:
            return

        # Consulta status no SQLite para só apagar do buffer o que já subiu para o Google Drive (ou se Drive não estiver ativo)
        seg_map = {s["filename"]: s.get("status") for s in list_dvr_segments(limit=200)}
        # Com pouco espaço livre, qualquer bloco sincronizado (menos o atual) pode sair, do mais antigo ao mais novo
        candidates_to_prune = files if low_space else files[:-RAM_CUSHION_MAX_SEGMENTS]
        # 1. Primeira passagem: descarta blocos já sincronizados (ou se Drive desativado)
        for fname in candidates_to_prune:
            if fname == self.current_segment:
                continue
            status = seg_map.get(fname)
            if status == "synced" or not gdrive_service.is_configured:
                fpath = os.path.join(self.recordings_dir, fname)
                try:
                    os.remove(fpath)
                    self._faststart_done.discard(fname)
                except OSError:
                    pass
                if low_space:
                    try:
                        if shutil.disk_usage(self.recordings_dir).free >= MIN_FREE_BUFFER_BYTES * 2:
                            return
                    except OSError:
                        return

        # 2. Segunda passagem de emergência (Buffer Crítico):
        # Se o Drive falhou (token expirado/sem conexão) e o buffer RAM está esgotando (< MIN_FREE_BUFFER_BYTES),
        # aplica FIFO descartando os blocos mais antigos (exceto o bloco em gravação).
        # Isso garante que a gravação 24h NUNCA trave ou derrube o FFmpeg por falta de espaço no tmpfs!
        try:
            critically_low = shutil.disk_usage(self.recordings_dir).free < MIN_FREE_BUFFER_BYTES
        except OSError:
            critically_low = False

        if critically_low:
            remaining = sorted([
                f for f in os.listdir(self.recordings_dir)
                if f.startswith("dvr_") and f.lower().endswith(".mp4") and f != self.current_segment
            ])
            for fname in remaining:
                fpath = os.path.join(self.recordings_dir, fname)
                try:
                    os.remove(fpath)
                    self._faststart_done.discard(fname)
                    logger.warning(
                        f"[DVR 24h Buffer de Emergência] Espaço livre crítico (<{MIN_FREE_BUFFER_BYTES // (1024*1024)}MB). "
                        f"Descartado bloco antigo não sincronizado '{fname}' para manter gravação contínua ativa!"
                    )
                    if shutil.disk_usage(self.recordings_dir).free >= MIN_FREE_BUFFER_BYTES * 1.5:
                        break
                except OSError:
                    pass

    def _migrate_legacy_disk_segments(self):
        """
        Se houver arquivos antigos na pasta legada recordings_24h do disco D:,
        faz upload imediato para a subpasta Gravacoes_24h do Google Drive e apaga do disco local!
        """
        legacy_dirs = ["recordings_24h", os.path.join("backend", "recordings_24h"), os.path.join("..", "recordings_24h")]
        for ldir in legacy_dirs:
            if not os.path.exists(ldir):
                continue
            try:
                if os.path.abspath(ldir) == os.path.abspath(self.recordings_dir):
                    continue
                entries = sorted([
                    f for f in os.listdir(ldir)
                    if f.startswith("dvr_") and f.lower().endswith(".mp4")
                ])
            except Exception:
                continue

            for fname in entries:
                fpath = os.path.join(ldir, fname)
                try:
                    sz = os.path.getsize(fpath)
                except OSError:
                    continue
                if sz < MIN_VALID_SEGMENT_BYTES:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
                    continue
                if gdrive_service.is_configured:
                    try:
                        res = gdrive_service.upload_dvr_segment(fpath)
                        if res and res.get("status") == "success":
                            mark_dvr_segment_synced(fname, drive_file_id=res.get("file_id"))
                            os.remove(fpath)
                            logger.info(f"[DVR 24h] Bloco legado '{fname}' migrado para o Drive e removido do disco D:.")
                    except Exception as e:
                        logger.warning(f"[DVR 24h] Erro ao migrar bloco legado '{fname}': {e}")
                        break

    def _sync_closed_segments_to_drive(self):
        if not gdrive_service.is_configured:
            self._prune_ram_buffer()
            return

        segments = list_dvr_segments(limit=60)
        for seg in segments:
            if seg.get("status") != "local":
                continue
            fname = seg["filename"]
            fpath = os.path.join(self.recordings_dir, fname)
            if not os.path.exists(fpath) or os.path.getsize(fpath) < MIN_VALID_SEGMENT_BYTES:
                continue
            try:
                res = gdrive_service.upload_dvr_segment(fpath)
                if res and res.get("status") == "success":
                    mark_dvr_segment_synced(fname, drive_file_id=res.get("file_id"))
            except Exception as e:
                logger.warning(f"[DVR 24h] Falha ao sincronizar '{fname}' com Google Drive: {e}")
                break

        self._prune_ram_buffer()

    def _watcher_loop(self):
        time.sleep(2)
        if gdrive_service.is_configured:
            try:
                gdrive_service.reconcile_dvr_from_drive(force=True)
            except Exception:
                pass
        while self._running:
            try:
                self.scan_local_segments()
                self._sync_closed_segments_to_drive()
                self._migrate_legacy_disk_segments()

                now_sec = time.time()
                if now_sec - self._last_cleanup_time > 3600:
                    cleanup_dvr_retention(retention_days=self.retention_days)
                    if gdrive_service.is_configured:
                        gdrive_service.cleanup_old_dvr_drive_files(retention_days=self.retention_days)
                    self._last_cleanup_time = now_sec
            except Exception as e:
                logger.warning(f"[DVR 24h] Erro no watcher loop: {e}")

            for _ in range(4):
                if not self._running:
                    break
                time.sleep(1)


dvr_service = DvrService()
