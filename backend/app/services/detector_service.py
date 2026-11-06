import threading
import time
import cv2
import logging
import os
from datetime import datetime
from ultralytics import YOLO
from app.core.time import get_now
from app.db import get_night_watch_config, log_detection_event, cleanup_retention
from app.services.camera_service import camera_service
from app.services.gdrive_service import gdrive_service
from app.services.evolution_service import evolution_service

logger = logging.getLogger("x9.detector")

def calculate_iou(box1, box2) -> float:
    """Calcula IoU (Intersection over Union) entre duas caixas [x1, y1, x2, y2]."""
    xA = max(box1[0], box2[0])
    yA = max(box1[1], box2[1])
    xB = min(box1[2], box2[2])
    yB = min(box1[3], box2[3])

    inter_w = max(0, xB - xA)
    inter_h = max(0, yB - yA)
    inter_area = inter_w * inter_h

    area1 = max(0, box1[2] - box1[0]) * max(0, box1[3] - box1[1])
    area2 = max(0, box2[2] - box2[0]) * max(0, box2[3] - box2[1])

    union_area = area1 + area2 - inter_area
    if union_area <= 0:
        return 0.0
    return inter_area / union_area

class SimpleTracker:
    """
    Rastreador leve baseado em IoU e persistência temporal (grace period).
    Garante ID consistente para cada pessoa e evita que a caixa pisque quando a
    confiança oscila por alguns quadros.
    """
    def __init__(self, max_age_seconds: float = 1.0, iou_threshold: float = 0.20, min_hits: int = 1):
        self.max_age_seconds = max_age_seconds
        self.iou_threshold = iou_threshold
        self.min_hits = min_hits
        self.next_id = 1
        self.tracks = {}  # tid: {"box": [x1, y1, x2, y2], "conf": float, "last_seen": float, "hits": int}

    def update(self, detections: list) -> list:
        now = time.time()
        
        # 1. Remove tracks inativos além do tempo de persistência (grace period)
        self.tracks = {
            tid: trk for tid, trk in self.tracks.items()
            if (now - trk["last_seen"]) < self.max_age_seconds
        }

        # Quando não há ninguém na cena, reseta contador para #1 (evita IDs infinitos)
        if not self.tracks:
            self.next_id = 1

        matched_tracks = set()
        matched_detections = set()

        # 2. Pareia detecções com tracks existentes usando IoU
        if self.tracks and detections:
            matches = []
            for tid, trk in self.tracks.items():
                for det_idx, det in enumerate(detections):
                    iou = calculate_iou(trk["box"], det["box"])
                    if iou >= self.iou_threshold:
                        matches.append((iou, tid, det_idx))

            matches.sort(key=lambda x: x[0], reverse=True)
            for iou, tid, det_idx in matches:
                if tid not in matched_tracks and det_idx not in matched_detections:
                    matched_tracks.add(tid)
                    matched_detections.add(det_idx)

                    det_box = detections[det_idx]["box"]
                    old_box = self.tracks[tid]["box"]
                    # Suavização espacial rápida contra jitter
                    self.tracks[tid]["box"] = [
                        int(0.70 * det_box[i] + 0.30 * old_box[i])
                        for i in range(4)
                    ]
                    self.tracks[tid]["conf"] = detections[det_idx]["conf"]
                    self.tracks[tid]["last_seen"] = now
                    self.tracks[tid]["hits"] += 1

        # 3. Registra novos tracks imediatamente (plot instantâneo no frame 1)
        for det_idx, det in enumerate(detections):
            if det_idx not in matched_detections:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = {
                    "box": [int(v) for v in det["box"]],
                    "conf": det["conf"],
                    "last_seen": now,
                    "hits": 1,
                }

        return [
            {
                "box": tuple(trk["box"]),
                "track_id": tid,
                "conf": trk["conf"],
                "last_seen": trk["last_seen"],
                "hits": trk["hits"],
            }
            for tid, trk in self.tracks.items()
            if trk["hits"] >= self.min_hits
        ]

class DetectorService:
    def __init__(self):
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        
        self.model = None
        self.current_model_type = "yolov8n"
        self.last_detection_time = 0.0
        self.last_cleanup_time = 0.0
        self.cooldown_seconds = 20.0
        self.tracker = SimpleTracker(max_age_seconds=1.0, iou_threshold=0.20, min_hits=1)
        self._cached_cfg = None
        self._last_cfg_fetch = 0.0
        
        # Telemetria em tempo real
        self.is_active = False
        self.last_person_detected = False
        self.last_confidence = 0.0
        self.people_count = 0
        self.detections_count = 0
        self.test_mode = False # Por padrão desligado; segue rigorosamente a janela cadastrada

    def load_model(self, model_name: str = "yolov8n"):
        try:
            logger.info(f"Carregando modelo de IA: {model_name}.pt...")
            # Baixa e carrega o modelo YOLO leve (yolov8n.pt ~6MB)
            self.model = YOLO(f"{model_name}.pt")
            self.current_model_type = model_name
            logger.info(f"Modelo {model_name} carregado com sucesso!")
        except Exception as e:
            logger.error(f"Erro ao carregar modelo YOLO {model_name}: {e}")

    def start(self):
        if self._running:
            return
        self._running = True
        if self.model is None:
            self.load_model("yolov8n")
        self._thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._thread.start()
        logger.info("Detector YOLO de Pessoas iniciado.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("Detector YOLO encerrado.")

    def is_in_schedule(self, start_time_str: str, end_time_str: str) -> bool:
        """Verifica se o horário atual de Brasília está dentro da janela configurada."""
        try:
            now = get_now().time()
            start = datetime.strptime(start_time_str, "%H:%M").time()
            end = datetime.strptime(end_time_str, "%H:%M").time()

            if start <= end:
                return start <= now <= end
            else: # Cruza a meia-noite (ex: 22:00 às 06:00)
                return now >= start or now <= end
        except Exception as e:
            logger.warning(f"Erro ao avaliar janela de horário ({start_time_str} - {end_time_str}): {e}")
            return False

    def _detection_loop(self):
        while self._running:
            try:
                # 1. Ciclo de inferência a cada 350ms (~3 FPS leve para CPU, liberando recursos para RTSP)
                time.sleep(0.35)

                now_sec = time.time()
                if self._cached_cfg is None or (now_sec - self._last_cfg_fetch) > 2.0:
                    self._cached_cfg = get_night_watch_config() or {}
                    self._last_cfg_fetch = now_sec
                cfg = self._cached_cfg

                # Rotação de modelo se configurado outro
                model_type = cfg.get("model_type", "yolov8n")
                if self.model is None or self.current_model_type != model_type:
                    self.load_model(model_type)

                # Limpeza periódica de retenção (1 vez por dia)
                if now_sec - self.last_cleanup_time > 86400:
                    retention_days = cfg.get("retention_days", 15)
                    cleanup_retention(retention_days)
                    self.last_cleanup_time = now_sec

                # 2. Pega último frame da câmera (Substream) para análise contínua 24/7
                frame = None
                with camera_service._lock:
                    if camera_service.latest_frame_raw is not None:
                        frame = camera_service.latest_frame_raw.copy()

                if frame is None or camera_service.status != "ONLINE":
                    continue

                conf_thresh = cfg.get("confidence_threshold", 0.45)
                # Classe 0 = Person: roda SEMPRE para desenhar bounding box no monitor ao vivo 24/7
                results = self.model.predict(
                    source=frame,
                    classes=[0],
                    conf=conf_thresh,
                    verbose=False,
                    imgsz=640, # Padrão nativo do YOLOv8 (640x640)
                )

                detections = []
                if results and len(results) > 0 and len(results[0].boxes) > 0:
                    for b in results[0].boxes:
                        coords = b.xyxy.cpu().numpy()[0]
                        conf = float(b.conf.cpu().numpy()[0])
                        detections.append({
                            "box": [int(coords[0]), int(coords[1]), int(coords[2]), int(coords[3])],
                            "conf": conf,
                        })

                # Atualiza rastreador e propaga caixas ativas para o streaming da câmera (24/7)
                active_tracks = self.tracker.update(detections)
                camera_service.update_active_tracks(active_tracks)

                person_detected = len(active_tracks) > 0
                max_conf = max([t["conf"] for t in active_tracks], default=0.0)

                self.last_person_detected = person_detected
                self.last_confidence = max_conf
                self.people_count = len(active_tracks)

                # 3. Gating do MODO VIGIA (Flash, Alarme Noturno e Snapshot de Invasão)
                vigia_enabled = bool(cfg.get("enabled", 1))
                start_t = cfg.get("start_time", "00:00")
                end_t = cfg.get("end_time", "06:00")
                in_schedule = self.is_in_schedule(start_t, end_t)
                
                # Telemetria: Modo Vigia ativo se habilitado e no horário
                self.is_active = vigia_enabled and in_schedule

                # Apenas dispara alarme/flash se o Modo Vigia estiver armado no horário (ou em test_mode)
                should_trigger_alarm = (vigia_enabled and in_schedule) or self.test_mode

                if person_detected and should_trigger_alarm and (now_sec - self.last_detection_time > self.cooldown_seconds):
                    self.last_detection_time = now_sec
                    self.detections_count += 1
                    video_duration = 10
                    # Flash cobre a transição óptica (1.8s), o snapshot colorido e os 10s inteiros de vídeo no modo 6_flash_wdr_off
                    configured_flash = cfg.get("flash_duration", 2.5)
                    flash_duration = max(float(configured_flash), float(video_duration) + 3.5)

                    logger.warning(
                        f"[!] MODO VIGIA: PESSOA DETECTADA ({max_conf:.1%})! Luz Branca ({flash_duration:.1f}s), Foto 1080p e Vídeo ({video_duration}s)..."
                    )

                    # 1. Foto 1: Flagrante Instantâneo (Preto e Branco / IR no milissegundo zero, antes do flash)
                    snapshot_pb = camera_service.take_snapshot(prefix="snapshot_pb")
                    log_detection_event(
                        confidence=max_conf,
                        flash_triggered=False,
                        snapshot_path=snapshot_pb,
                        video_path=None,
                    )
                    logger.info(f"[+] Foto 1 (Flagrante P&B) capturada: {snapshot_pb}")

                    # 2. Aciona Flash de Luz Branca (WhiteMode + WDR Off que cobrirá foto colorida e gravação)
                    camera_service.trigger_flash(seconds=flash_duration)
                    
                    # 3. Aguarda 1.8s para comutação mecânica do filtro IR-cut, desligamento do WDR e luz plena
                    time.sleep(1.8)
                    
                    # 4. Foto 2: Foto com Flash (Full Color 1080p nítida com o ambiente 100% iluminado)
                    snapshot_flash = camera_service.take_snapshot(prefix="snapshot_flash")
                    # Envio imediato da foto de flagrante no WhatsApp (assíncrono)
                    evolution_service.send_alert_photo_async(snapshot_flash, max_conf)

                    # 5. Grava clipe de vídeo MP4 em alta definição com áudio sincronizado (10 segundos)
                    # O upload para o Google Drive e WhatsApp é agendado para quando o ffmpeg terminar de renderizar o arquivo completo
                    video_desc = f"Gravação Noturna X9: Vídeo de Invasão ({max_conf:.1%})"
                    def _upload_video_when_ready(fpath, fname):
                        logger.info(f"[+] Gravação finalizada, enviando vídeo ao Google Drive: {fname}")
                        gdrive_service.upload_file_async(
                            fname,
                            description=video_desc,
                            file_type="recording"
                        )
                        # Envio do vídeo de 10s no WhatsApp (assíncrono)
                        evolution_service.send_alert_video_async(fpath, max_conf)

                    video_filename = camera_service.record_clip(seconds=video_duration, on_complete=_upload_video_when_ready)

                    # Pré-registra preventivamente o vídeo no SQLite: se o backend cair ou reiniciar
                    # durante os 10s de gravação do ffmpeg, o vídeo já está garantido na fila de sincronização
                    gdrive_service.enqueue_file(
                        video_filename,
                        description=video_desc,
                        file_type="recording"
                    )

                    # 6. Salva no SQLite o evento principal com a foto colorida com flash e o vídeo
                    log_detection_event(
                        confidence=max_conf,
                        flash_triggered=True,
                        snapshot_path=snapshot_flash,
                        video_path=video_filename,
                    )
                    logger.info(f"[+] Evento gravado no banco com Foto Flash ({snapshot_flash}) e vídeo ({video_filename})")

                    # 7. Backup automático para Google Drive (foto flagrante P&B e foto flash colorido)
                    gdrive_service.upload_file_async(
                        snapshot_pb,
                        description=f"Detecção Noturna X9 [Flagrante P&B]: Pessoa detectada ({max_conf:.1%})",
                        file_type="snapshot"
                    )
                    gdrive_service.upload_file_async(
                        snapshot_flash,
                        description=f"Detecção Noturna X9 [Flash Colorido]: Pessoa detectada ({max_conf:.1%})",
                        file_type="snapshot"
                    )

            except Exception as e:
                logger.error(f"Erro no loop do detector YOLO: {e}")
                time.sleep(1)

detector_service = DetectorService()
