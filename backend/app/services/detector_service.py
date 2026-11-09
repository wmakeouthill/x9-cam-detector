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
        self.tracker = SimpleTracker(max_age_seconds=2.2, iou_threshold=0.20, min_hits=1)
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self._cached_cfg = None
        self._last_cfg_fetch = 0.0
        
        # Telemetria em tempo real
        self.detection_mode = "hybrid" # "hybrid", "camera", "yolo"
        self.yolo_awake = False
        self.hybrid_wake_duration = 20.0 # Segundos que o YOLO permanece ativo após gatilho da câmera
        self.is_active = False
        self.last_person_detected = False
        self.last_confidence = 0.0
        self.people_count = 0
        self.detections_count = 0
        self.test_mode = False # Por padrão desligado; segue rigorosamente a janela cadastrada
        self.wake_event = threading.Event()

    def _on_camera_event(self, code: str, action: str, event: dict):
        """Acorda o detector YOLO no milissegundo zero quando a câmera detecta movimento."""
        if action.lower() in ("start", "pulse"):
            self.wake_event.set()

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
        cfg = get_night_watch_config() or {}
        self.detection_mode = cfg.get("detection_mode", "hybrid")
        model_name = cfg.get("model_type", "yolov8n")
        
        # No modo 'camera', não precisa alocar YOLO na inicialização; carrega sob demanda se trocar de modo
        if self.detection_mode != "camera" and self.model is None:
            self.load_model(model_name)
            
        camera_service.on_camera_event = self._on_camera_event
        self._thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._thread.start()
        logger.info(f"Detector de Pessoas iniciado (Modo: {self.detection_mode.upper()}).")

    def stop(self):
        self._running = False
        self.wake_event.set()
        camera_service.on_camera_event = None
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

    def _enhance_frame_for_yolo(self, frame):
        """
        Normaliza o contraste local (CLAHE no canal L) na cópia enviada ao YOLO
        para destacar contornos humanos no fundo do portão (tanto em IR P&B + WDR quanto à contraluz),
        sem alterar a imagem exibida ao usuário.
        """
        try:
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l_ch, a_ch, b_ch = cv2.split(lab)
            l_ch = self._clahe.apply(l_ch)
            return cv2.cvtColor(cv2.merge((l_ch, a_ch, b_ch)), cv2.COLOR_LAB2BGR)
        except Exception:
            return frame

    def _trigger_flagrante(
        self,
        cfg: dict,
        max_conf: float,
        event_type: str = "PERSON_DETECTED",
        reason: str = "",
    ):
        """
        Dispara a sequência de flagrante de segurança com ZERO LATÊNCIA:
        1. Aciona Flash LED de Luz Branca no instante zero (t=0ms).
        2. Inicia gravação de vídeo dinâmico MP4 no instante zero (t=0ms).
        3. Foto 1 em P&B capturada instantaneamente da memória RAM (2ms, sem bloqueio de rede).
        4. Transição óptica e Foto 2 Colorida com Flash executadas em thread assíncrona,
           sem travar o loop de detecção nem atrasar a gravação.
        5. Registro no SQLite e upload automático para Google Drive e WhatsApp.
        """
        now_sec = time.time()
        self.last_detection_time = now_sec
        self.detections_count += 1
        min_duration = int(cfg.get("clip_min_duration", getattr(camera_service, "clip_min_duration", 20)))
        max_duration = int(cfg.get("clip_max_duration", getattr(camera_service, "clip_max_duration", 120)))
        configured_flash = cfg.get("flash_duration", 2.5)
        flash_duration = max(float(configured_flash), float(min_duration) + 3.5)

        motivo = reason or f"Pessoa detectada ({max_conf:.1%})"
        logger.warning(
            f"[!] FLAGRANTE INSTANTÂNEO [{event_type}]: {motivo}! Luz Branca ({flash_duration:.1f}s), Foto RAM e Vídeo Dinâmico ({min_duration}s base, até {max_duration}s)..."
        )

        # 1. ACIONA FLASH LED DE LUZ BRANCA IMEDIATAMENTE (Milissegundo Zero)
        camera_service.trigger_flash(seconds=flash_duration)

        # 2. INICIA GRAVAÇÃO DINÂMICA DE VÍDEO IMEDIATAMENTE (Milissegundo Zero)
        video_desc = f"Gravação Noturna X9 [{event_type}]: {motivo}"
        def _upload_video_when_ready(fpath, fname):
            logger.info(f"[+] Gravação finalizada, enviando vídeo ao Google Drive: {fname}")
            gdrive_service.upload_file_async(
                fname,
                description=video_desc,
                file_type="recording",
            )
            evolution_service.send_alert_video_async(fpath, max_conf)

        video_filename = camera_service.record_clip(
            seconds=min_duration,
            max_seconds=max_duration,
            on_complete=_upload_video_when_ready,
        )

        # Pré-registra na fila offline
        gdrive_service.enqueue_file(
            video_filename,
            description=video_desc,
            file_type="recording",
        )

        # 3. FOTO 1: Flagrante Instantâneo P&B gravado direto da memória RAM (2ms, sem bloqueio HTTP)
        snapshot_pb = camera_service.take_snapshot(prefix="snapshot_pb", from_memory=True)
        log_detection_event(
            confidence=max_conf,
            flash_triggered=True,
            snapshot_path=snapshot_pb,
            video_path=video_filename,
            event_type=event_type,
        )
        logger.info(f"[+] Foto 1 (Flagrante P&B instantâneo da RAM) capturada: {snapshot_pb}")

        # 4. Transição óptica e Foto 2 Colorida com Flash em THREAD ASSÍNCRONA SEPARADA
        # (O loop de detecção e o vídeo continuam rodando sem NENHUM freeze ou bloqueio!)
        def _flagrante_optical_transition_worker():
            try:
                # Aguarda comutação mecânica do filtro IR-cut, desligamento do WDR e luz plena (1.5s)
                time.sleep(1.5)

                # Foto 2: Foto com Flash (Full Color 1080p da RAM com o ambiente iluminado)
                snapshot_flash = camera_service.take_snapshot(prefix="snapshot_flash", from_memory=True)
                evolution_service.send_alert_photo_async(snapshot_flash, max_conf)

                # Atualiza no banco o registro com a foto colorida
                log_detection_event(
                    confidence=max_conf,
                    flash_triggered=True,
                    snapshot_path=snapshot_flash,
                    video_path=video_filename,
                    event_type=event_type,
                )
                logger.info(f"[+] Evento atualizado com Foto Flash ({snapshot_flash}) e vídeo ({video_filename})")

                # Backup automático para Google Drive (foto flagrante P&B e foto flash colorido)
                gdrive_service.upload_file_async(
                    snapshot_pb,
                    description=f"Detecção X9 [Flagrante P&B]: {motivo}",
                    file_type="snapshot",
                )
                gdrive_service.upload_file_async(
                    snapshot_flash,
                    description=f"Detecção X9 [Flash Colorido]: {motivo}",
                    file_type="snapshot",
                )
            except Exception as e:
                logger.error(f"Erro no worker de transição óptica do flagrante: {e}")

        t_bg = threading.Thread(target=_flagrante_optical_transition_worker, daemon=True)
        t_bg.start()

    def _detection_loop(self):
        while self._running:
            try:
                loop_t0 = time.time()
                now_sec = loop_t0
                if self._cached_cfg is None or (now_sec - self._last_cfg_fetch) > 2.0:
                    self._cached_cfg = get_night_watch_config() or {}
                    self._last_cfg_fetch = now_sec
                cfg = self._cached_cfg

                # Modo de Detecção selecionado: "hybrid" (padrão), "camera" (100% câmera) ou "yolo" (contínuo)
                detection_mode = cfg.get("detection_mode", "hybrid")
                self.detection_mode = detection_mode

                # Rotação ou carregamento sob demanda do modelo YOLO (quando não estiver em 100% câmera)
                model_type = cfg.get("model_type", "yolov8n")
                if detection_mode != "camera":
                    if self.model is None or self.current_model_type != model_type:
                        self.load_model(model_type)

                # Limpeza periódica de retenção (1 vez por dia)
                if now_sec - self.last_cleanup_time > 86400:
                    retention_days = cfg.get("retention_days", 15)
                    cleanup_retention(retention_days)
                    self.last_cleanup_time = now_sec

                # Gating do MODO VIGIA (Flash, Alarme Noturno e Snapshot de Invasão)
                vigia_enabled = bool(cfg.get("enabled", 1))
                start_t = cfg.get("start_time", "00:00")
                end_t = cfg.get("end_time", "06:00")
                in_schedule = self.is_in_schedule(start_t, end_t)
                self.is_active = vigia_enabled and in_schedule
                should_trigger_alarm = (vigia_enabled and in_schedule) or self.test_mode

                # =========================================================================
                # MODO 1: 100% CÂMERA (Zero IA / Zero CPU no Servidor)
                # =========================================================================
                if detection_mode == "camera":
                    self.yolo_awake = False
                    camera_service.update_active_tracks([])
                    self.people_count = 0
                    self.last_person_detected = False

                    cam_event_time = camera_service.last_camera_event_time
                    cam_event_recent = (now_sec - cam_event_time) < 4.0
                    cam_code = camera_service.last_camera_event_code or "SmartMotionHuman"

                    if cam_event_recent and should_trigger_alarm:
                        if camera_service.is_recording_clip:
                            camera_service.extend_clip(additional_seconds=10.0)
                            self.last_detection_time = now_sec
                        elif (now_sec - self.last_detection_time > self.cooldown_seconds):
                            self._trigger_flagrante(
                                cfg=cfg,
                                max_conf=1.0,
                                event_type="CAMERA_EVENT",
                                reason=f"Detecção Nativa Intelbras ({cam_code})"
                            )

                    time.sleep(0.3)
                    continue

                # =========================================================================
                # MODO 2: HÍBRIDO (Padrão - Câmera Sentinela dispara, YOLO acorda e valida)
                # =========================================================================
                if detection_mode == "hybrid":
                    cam_event_time = camera_service.last_camera_event_time
                    cam_event_recent = (now_sec - cam_event_time) < self.hybrid_wake_duration
                    has_active_track = len(self.tracker.tracks) > 0
                    should_wake_yolo = (
                        cam_event_recent
                        or has_active_track
                        or camera_service.is_recording_clip
                        or self.test_mode
                    )

                    if not should_wake_yolo:
                        # YOLO em repouso: CPU fica fria (acorda em 0ms quando wake_event é disparado pelo hook)
                        self.yolo_awake = False
                        camera_service.update_active_tracks([])
                        self.people_count = 0
                        self.last_person_detected = False
                        self.wake_event.wait(timeout=0.25)
                        self.wake_event.clear()
                        continue

                    self.yolo_awake = True
                else:
                    # MODO 3: YOLO CONTÍNUO (Analisa todos os quadros 24/7)
                    self.yolo_awake = True

                # =========================================================================
                # EXECUÇÃO DO YOLO (Modo Híbrido acordado OU Modo YOLO Contínuo)
                # =========================================================================
                frame = None
                with camera_service._lock:
                    if camera_service.latest_frame_raw is not None:
                        frame = camera_service.latest_frame_raw.copy()

                if frame is None or camera_service.status != "ONLINE":
                    time.sleep(0.2)
                    continue

                conf_thresh = float(cfg.get("confidence_threshold", 0.45))
                has_active_track = len(self.tracker.tracks) > 0
                query_conf = max(0.28, conf_thresh - 0.12) if has_active_track else max(0.32, conf_thresh - 0.05)

                yolo_input = self._enhance_frame_for_yolo(frame)
                results = self.model.predict(
                    source=yolo_input,
                    classes=[0],
                    conf=query_conf,
                    verbose=False,
                    imgsz=640,
                )

                detections = []
                if results and len(results) > 0 and len(results[0].boxes) > 0:
                    for b in results[0].boxes:
                        coords = b.xyxy.cpu().numpy()[0]
                        conf = float(b.conf.cpu().numpy()[0])
                        if has_active_track or conf >= min(conf_thresh, 0.38):
                            detections.append({
                                "box": [int(coords[0]), int(coords[1]), int(coords[2]), int(coords[3])],
                                "conf": conf,
                            })

                active_tracks = self.tracker.update(detections)
                camera_service.update_active_tracks(active_tracks)

                person_detected = len(active_tracks) > 0
                max_conf = max([t["conf"] for t in active_tracks], default=0.0)

                self.last_person_detected = person_detected
                self.last_confidence = max_conf
                self.people_count = len(active_tracks)

                if person_detected and should_trigger_alarm:
                    if camera_service.is_recording_clip:
                        camera_service.extend_clip(additional_seconds=10.0)
                        self.last_detection_time = now_sec
                    elif (now_sec - self.last_detection_time > self.cooldown_seconds):
                        event_type = "HYBRID_DETECTION" if detection_mode == "hybrid" else "PERSON_DETECTED"
                        self._trigger_flagrante(
                            cfg=cfg,
                            max_conf=max_conf,
                            event_type=event_type,
                            reason=f"Pessoa confirmada ({max_conf:.1%})"
                        )

                elapsed = time.time() - loop_t0
                if elapsed < 0.20:
                    time.sleep(0.20 - elapsed)

            except Exception as e:
                logger.error(f"Erro no loop do detector YOLO: {e}")
                time.sleep(1)

detector_service = DetectorService()
