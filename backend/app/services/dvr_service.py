import os
import re
import time
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


class DvrService:
    """
    Serviço de Gravação Contínua 24h (DVR em nuvem com retenção de 15 dias).
    - Grava em blocos de 5 minutos (300s) em 1080p com áudio e marcações YOLO (PESSOA #ID).
    - Usa MP4 fragmentado (frag_keyframe+empty_moov+default_base_moof) para permitir
      seek instantâneo ("Voltar 30s / 1m / 5m") mesmo no arquivo que ainda está sendo gravado.
    - Sincroniza cada bloco fechado para a subpasta exclusiva 'Gravacoes_24h' no Google Drive,
      mantendo total isolamento da galeria de fotos e clipes de 10s.
    """

    def __init__(self):
        self.recordings_dir = "backend/recordings_24h" if os.path.exists("backend") else "recordings_24h"
        os.makedirs(self.recordings_dir, exist_ok=True)

        self._running = False
        self._recorder_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

        self.enabled = True
        self.segment_seconds = 60
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
            self.segment_seconds = int(cfg.get("segment_seconds", 60))
            self.retention_days = int(cfg.get("retention_days", 15))
            self.subtype = int(cfg.get("subtype", 0))
        except Exception as e:
            logger.warning(f"[DVR 24h] Falha ao carregar dvr_config, usando padrão: {e}")

    def start(self):
        if self._running:
            return
        os.makedirs(self.recordings_dir, exist_ok=True)
        self._load_config()

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
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-crf", "23",
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
        """Varre o diretório recordings_24h e atualiza a tabela dvr_segments."""
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

            if size_bytes == 0:
                continue

            start_dt = self._parse_filename_start(fname) or mtime_dt
            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            is_newest = (idx == total - 1)
            recently_modified = (now_dt - mtime_dt).total_seconds() < 15.0

            if is_newest and (self._is_recording or recently_modified):
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

    def _sync_closed_segments_to_drive(self):
        if not gdrive_service.is_configured:
            return

        segments = list_dvr_segments(limit=50)
        for seg in segments:
            if seg.get("status") != "local":
                continue
            fname = seg["filename"]
            fpath = os.path.join(self.recordings_dir, fname)
            if not os.path.exists(fpath) or os.path.getsize(fpath) == 0:
                continue
            try:
                res = gdrive_service.upload_dvr_segment(fpath)
                if res and res.get("status") == "success":
                    mark_dvr_segment_synced(fname, drive_file_id=res.get("file_id"))
            except Exception as e:
                logger.warning(f"[DVR 24h] Falha ao sincronizar '{fname}' com Google Drive: {e}")
                break

    def _watcher_loop(self):
        time.sleep(2)
        while self._running:
            try:
                self.scan_local_segments()
                self._sync_closed_segments_to_drive()

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
