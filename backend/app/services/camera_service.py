import cv2
import threading
import time
import os
import logging
import hashlib
from urllib.parse import quote
import numpy as np
import requests
from requests.auth import HTTPDigestAuth
from datetime import datetime
import socket
import concurrent.futures
from app.core.config import settings
from app.core.time import get_now, get_now_str
from app.db import get_camera_config, save_camera_config, save_image_profile_mode

# Força transporte TCP para evitar corrupção de imagem e pacotes perdidos
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

logger = logging.getLogger("x9.camera")
logging.basicConfig(level=logging.INFO)

class CameraService:
    def __init__(self):
        # Tenta carregar do banco primeiro, fallback para .env
        db_cfg = get_camera_config()
        self.ip = db_cfg.get("ip") or settings.CAMERA_IP
        self.port = db_cfg.get("port") or settings.CAMERA_PORT
        self.http_port = db_cfg.get("http_port") or settings.CAMERA_HTTP_PORT
        self.user = db_cfg.get("user") or settings.CAMERA_USER
        self.password = db_cfg.get("password") or settings.CAMERA_PASSWORD
        self.subtype = db_cfg.get("subtype", settings.CAMERA_SUBTYPE) # 0 = 1080p FHD por padrão
        self.image_profile_mode = db_cfg.get("image_profile_mode", "auto") # auto, day, night
        self.night_start_time = db_cfg.get("night_start_time", "22:00")
        self.night_end_time = db_cfg.get("night_end_time", "05:00")
        self.clip_min_duration = int(db_cfg.get("clip_min_duration", 20))
        self.clip_max_duration = int(db_cfg.get("clip_max_duration", 120))
        self.target_overlay = bool(db_cfg.get("target_overlay", 1))

        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._rpc_lock = threading.Lock()
        self._flash_active = False
        self._flash_until = 0.0
        self._continuous_light = False

        self._clip_active = False
        self._clip_lock = threading.Lock()
        self._clip_deadline = 0.0
        self._clip_start_time = 0.0
        self._current_clip_filename: str | None = None
        
        self.latest_frame_raw = None
        self.latest_frame_jpeg = None
        self._active_tracks = []
        self.status = "OFFLINE" # ONLINE, CONNECTING, OFFLINE
        self.fps = 0.0
        self.width = 0
        self.height = 0
        self.last_frame_time = 0.0

        # Eventos Nativos da Câmera (IVS, Linha/Cerca Virtual e SMD Pessoa/Veículo)
        self.camera_event_active = False
        self.last_camera_event_time = 0.0
        self.last_camera_event_code: str | None = None
        self.last_camera_event_data: dict = {}
        self._event_thread: threading.Thread | None = None

        os.makedirs("snapshots", exist_ok=True)
        self._placeholder_jpeg = self._create_placeholder("X9 MONITOR - INICIANDO...")

    @property
    def rtsp_url(self) -> str:
        # Quote essencial para senhas com @, #, !, :, etc.
        user_enc = quote(self.user, safe="")
        pass_enc = quote(self.password, safe="")
        return f"rtsp://{user_enc}:{pass_enc}@{self.ip}:{self.port}/cam/realmonitor?channel=1&subtype={self.subtype}"

    @property
    def mainstream_rtsp_url(self) -> str:
        """URL RTSP do Stream Principal (1080p FHD nativo) para gravações e evidências de segurança."""
        user_enc = quote(self.user, safe="")
        pass_enc = quote(self.password, safe="")
        return f"rtsp://{user_enc}:{pass_enc}@{self.ip}:{self.port}/cam/realmonitor?channel=1&subtype=0"

    def update_config(
        self,
        ip: str,
        user: str,
        password: str,
        port: int = 554,
        http_port: int = 80,
        subtype: int = 1,
        image_profile_mode: str = "auto",
        night_start_time: str = "22:00",
        night_end_time: str = "05:00",
        clip_min_duration: int = 20,
        clip_max_duration: int = 120,
        target_overlay: bool = True,
    ):
        with self._lock:
            self.ip = ip
            self.user = user
            self.password = password
            self.port = port
            self.http_port = http_port
            self.subtype = subtype
            self.image_profile_mode = image_profile_mode
            self.night_start_time = night_start_time
            self.night_end_time = night_end_time
            self.clip_min_duration = int(clip_min_duration)
            self.clip_max_duration = int(clip_max_duration)
            self.target_overlay = bool(target_overlay)
            self.status = "CONNECTING"
        # Persiste no banco com senha criptografada em AES-256
        save_camera_config(
            ip, port, http_port, user, password, subtype,
            image_profile_mode, night_start_time, night_end_time,
            self.clip_min_duration, self.clip_max_duration,
            self.target_overlay,
        )
        logger.info(
            f"Configuração da câmera salva e atualizada: {ip}:{port} (Subtype {subtype}, Perfil {image_profile_mode}, Janela Noturna {night_start_time}-{night_end_time}, Clips {self.clip_min_duration}-{self.clip_max_duration}s, TargetOverlay: {self.target_overlay})"
        )
        threading.Thread(target=self._ensure_optimal_camera_image_rpc, daemon=True).start()
        threading.Thread(target=self._apply_target_overlay_rpc, daemon=True).start()

    @property
    def is_recording_clip(self) -> bool:
        with self._clip_lock:
            return self._clip_active

    @property
    def continuous_light(self) -> bool:
        with self._lock:
            return self._continuous_light

    @property
    def is_flash_active(self) -> bool:
        with self._lock:
            return bool(self._flash_active or self._continuous_light)

    def extend_clip(self, additional_seconds: float = 10.0) -> bool:
        """
        Estende o tempo da gravação de alarme ativa enquanto houver movimento/pessoa detectada.
        Também mantém o LED/Flash aceso pelo mesmo período.
        """
        with self._clip_lock:
            if not self._clip_active:
                return False
            now = time.time()
            max_deadline = self._clip_start_time + self.clip_max_duration
            new_deadline = min(now + additional_seconds, max_deadline)
            if new_deadline > self._clip_deadline:
                self._clip_deadline = new_deadline
        self.trigger_flash(seconds=additional_seconds + 3.0)
        return True

    def update_active_tracks(self, tracks: list):
        """Atualiza a lista de tracks/caixas rastreadas para sobreposição no streaming."""
        with self._lock:
            self._active_tracks = tracks

    def get_active_tracks(self) -> list:
        """Retorna as caixas YOLO ativas (watchdog TTL de 3.5s para suportar yolov8m em 1080p sem piscar)."""
        now = time.time()
        with self._lock:
            tracks = [t for t in self._active_tracks if (now - t.get("last_seen", 0)) < 3.5]
            self._active_tracks = tracks
            return list(tracks)

    def draw_tracks_on_frame(self, frame: np.ndarray, tracks: list | None = None) -> np.ndarray:
        """Desenha as marcações HUD do YOLO (caixas + PESSOA #ID (%)) proporcionalmente à resolução do frame."""
        if tracks is None:
            tracks = self.get_active_tracks()
        if not tracks or frame is None:
            return frame

        h, w = frame.shape[:2]
        src_w = self.width if self.width > 0 else w
        src_h = self.height if self.height > 0 else h
        scale_x = w / float(src_w)
        scale_y = h / float(src_h)
        ui_scale = max(0.8, min(1.2, w / 1920.0))
        box_thickness = max(1, int(1 * ui_scale))
        font_scale = 0.36 * ui_scale
        font_thickness = 1

        PERSON_PALETTE = [
            (255, 200, 0),   # Ciano Neon
            (0, 165, 255),   # Laranja Vibrante
            (50, 220, 50),   # Verde Neon
            (204, 50, 204),  # Magenta
            (0, 215, 255),   # Amarelo Ouro
            (255, 120, 120), # Azul Céu
        ]
        for idx, t in enumerate(tracks):
            track_id = t.get("track_id", idx + 1)
            color = PERSON_PALETTE[(track_id - 1) % len(PERSON_PALETTE)]
            bx1, by1, bx2, by2 = t["box"]
            conf = t.get("conf", 0.0)

            x1 = max(0, int(bx1 * scale_x))
            y1 = max(0, int(by1 * scale_y))
            x2 = min(w - 1, int(bx2 * scale_x))
            y2 = min(h - 1, int(by2 * scale_y))

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, box_thickness, lineType=cv2.LINE_AA)

            label = f"PESSOA #{track_id} ({int(conf * 100)}%)"
            (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)
            pad = int(3 * ui_scale)
            label_y = y1 - pad if (y1 - th - 2 * pad) > 0 else y1 + th + pad
            badge_top = label_y - th - pad
            badge_bottom = label_y + baseline + 1
            cv2.rectangle(frame, (x1, badge_top), (x1 + tw + 2 * pad, badge_bottom), color, -1)
            cv2.putText(
                frame,
                label,
                (x1 + pad, label_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (10, 15, 20),
                font_thickness,
                cv2.LINE_AA,
            )
        return frame

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        self._event_thread = threading.Thread(target=self._event_stream_loop, daemon=True)
        self._event_thread.start()
        logger.info("Worker de captura RTSP e escuta de eventos nativos da câmera (IVS/SMD) iniciados.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._event_thread:
            self._event_thread.join(timeout=2)
        logger.info("Workers de captura RTSP e eventos de câmera encerrados.")

    def _event_stream_loop(self):
        """
        Escuta eventos nativos em tempo real direto da câmera Intelbras via CGI Event Stream:
        - CrossRegionDetection: Cerca Virtual / Invasão de Perímetro
        - CrossLineDetection: Linha Virtual
        - SmartMotionHuman: Detecção Inteligente de Pessoas (SMD)
        - SmartMotionVehicle: Detecção Inteligente de Veículos (SMD)
        - VideoMotion: Detecção de Movimento Geral
        """
        codes = "CrossRegionDetection,CrossLineDetection,SmartMotionHuman,SmartMotionVehicle,VideoMotion"
        while self._running:
            try:
                # Aguarda câmera estar online
                if self.status != "ONLINE":
                    time.sleep(2.0)
                    continue

                url = f"http://{self.ip}:{self.http_port}/cgi-bin/eventManager.cgi?action=attach&codes=[{codes}]"
                logger.info(f"[Câmera IVS/SMD] Conectando ao fluxo de eventos nativos da câmera: {self.ip}:{self.http_port}...")
                
                with requests.get(
                    url,
                    auth=HTTPDigestAuth(self.user, self.password),
                    stream=True,
                    timeout=(8.0, 60.0),
                ) as resp:
                    if resp.status_code != 200:
                        logger.warning(f"[Câmera IVS/SMD] Resposta inesperada ao conectar no eventManager: {resp.status_code}")
                        time.sleep(5.0)
                        continue

                    logger.info("[Câmera IVS/SMD] Conexão com eventos nativos estabelecida com sucesso!")
                    for raw_line in resp.iter_lines():
                        if not self._running:
                            break
                        if not raw_line:
                            continue

                        line_str = raw_line.decode("utf-8", errors="ignore").strip()
                        if "Code=" not in line_str or "action=" not in line_str:
                            continue

                        # Parseia Code=<tipo>;action=<Start|Stop>;index=0...
                        event = {}
                        for part in line_str.split(";"):
                            if "=" in part:
                                k, v = part.split("=", 1)
                                event[k.strip()] = v.strip()

                        code = event.get("Code")
                        action = event.get("action", "")

                        if not code:
                            continue

                        if action.lower() in ("start", "pulse"):
                            now = time.time()
                            self.camera_event_active = True
                            self.last_camera_event_time = now
                            self.last_camera_event_code = code
                            self.last_camera_event_data = event
                            logger.info(f"[CÂMERA IVS/SMD] Disparo de evento nativo: {code} (action={action})")
                        elif action.lower() == "stop":
                            if self.last_camera_event_code == code:
                                self.camera_event_active = False

            except Exception as e:
                if self._running:
                    logger.debug(f"[Câmera IVS/SMD] Conexão com fluxo de eventos interrompida ({e}). Reconectando em 5s...")
                    time.sleep(5.0)

    def _create_placeholder(self, text: str) -> bytes:
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        # Gradiente sutil de fundo estilo segurança
        img[:] = (18, 20, 24)
        
        cv2.rectangle(img, (40, 40), (1240, 680), (35, 40, 50), 2)
        cv2.putText(img, "X9 CAM SECURITY MONITOR", (70, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)
        
        # Status
        cv2.putText(img, text, (70, 360), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (220, 220, 220), 2)
        cv2.putText(img, f"Alvo: rtsp://{self.ip}:{self.port} (Subtype {self.subtype})", (70, 420), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 130, 150), 1)
        
        timestamp = get_now_str("%Y-%m-%d %H:%M:%S")
        cv2.putText(img, timestamp, (70, 640), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 100, 120), 1)

        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    def _capture_loop(self):
        frame_count = 0
        fps_timer = time.time()

        while self._running:
            url = self.rtsp_url
            self.status = "CONNECTING"
            logger.info(f"Tentando conectar ao RTSP: {self.ip}:{self.port}...")

            timeout_ms = 4000
            cap = cv2.VideoCapture(
                url,
                cv2.CAP_FFMPEG,
                params=[
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms,
                ]
            )
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                self.status = "OFFLINE"
                self._placeholder_jpeg = self._create_placeholder(f"SEM SINAL / 403 - VERIFIQUE USUARIO/SENHA EM {self.ip}")
                # Espera 5 segundos para não disparar bloqueio por tentativas na Intelbras
                time.sleep(5)
                continue

            self.status = "ONLINE"
            logger.info(f"[+] Conexão RTSP estabelecida com sucesso: {self.ip}")
            last_is_night = self._is_night_window()
            threading.Thread(target=self._ensure_optimal_camera_hardware_rpc, daemon=True).start()

            while self._running:
                # Se a URL mudar em tempo de execução, reconecta
                if url != self.rtsp_url:
                    break

                try:
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        logger.warning("Falha ao ler frame do RTSP. Reconectando...")
                        break

                    now = time.time()
                    self.last_frame_time = now
                    h, w, _ = frame.shape
                    self.width = w
                    self.height = h

                    # Salva cópia limpa para inferência de IA e snapshots
                    raw_frame = frame.copy()

                    # Renderiza anotações de tracking no frame se houver detecções ativas (watchdog TTL de 3.5s)
                    with self._lock:
                        self.latest_frame_raw = raw_frame
                        tracks = [t for t in self._active_tracks if (now - t.get("last_seen", 0)) < 3.5]
                        self._active_tracks = tracks

                    if tracks:
                        self.draw_tracks_on_frame(frame, tracks)

                    # Codifica para JPEG mantendo alta fidelidade visual (qualidade 82, leve para CPU)
                    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
                    
                    with self._lock:
                        self.latest_frame_jpeg = jpeg.tobytes()

                    frame_count += 1
                    if now - fps_timer >= 1.0:
                        self.fps = round(frame_count / (now - fps_timer), 1)
                        frame_count = 0
                        fps_timer = now
                        current_is_night = self._is_night_window()
                        if current_is_night != last_is_night:
                            last_is_night = current_is_night
                            logger.info(
                                f"[Imagem Hardware] Virada de perfil detectada ({'NOITE IR+WDR38' if current_is_night else 'DIA Color+WDR60'}): recalibrando perfil..."
                            )
                            threading.Thread(target=self._ensure_optimal_camera_image_rpc, daemon=True).start()
                except Exception as err:
                    logger.warning(f"Erro momentâneo no processamento de frame: {err}")
                    time.sleep(0.05)

            cap.release()
            self.status = "OFFLINE"
            time.sleep(1)

    def get_latest_jpeg(self) -> bytes:
        with self._lock:
            if self.latest_frame_jpeg is not None and (time.time() - self.last_frame_time) < 3.0:
                return self.latest_frame_jpeg
            return self._placeholder_jpeg

    def is_in_schedule(self, start_time_str: str, end_time_str: str) -> bool:
        """Verifica se o horário atual de Brasília está dentro da janela configurada."""
        try:
            now = get_now().time()
            start = datetime.strptime(start_time_str, "%H:%M").time()
            end = datetime.strptime(end_time_str, "%H:%M").time()
            if start <= end:
                return start <= now <= end
            else: # Cruza a meia-noite (ex: 22:00 às 05:00)
                return now >= start or now <= end
        except Exception:
            return False

    @property
    def is_night_active(self) -> bool:
        """
        Retorna True se o modo noturno (IR + WDR 38) deve estar ativo:
        - Se image_profile_mode == 'night': Sempre True (Forçar Noite IR).
        - Se image_profile_mode == 'day': Sempre False (Forçar Dia / Colorido).
        - Se image_profile_mode == 'auto': Segue a janela configurada (padrão 22:00 às 05:00).
        """
        if self.image_profile_mode == "night":
            return True
        if self.image_profile_mode == "day":
            return False
        return self.is_in_schedule(self.night_start_time, self.night_end_time)

    def _is_night_window(self) -> bool:
        """Avalia se o perfil de hardware deve ser o noturno (IR+WDR38) ou diurno (Color+WDR60)."""
        return self.is_night_active

    def set_image_profile(self, mode: str, start_time: str | None = None, end_time: str | None = None) -> dict:
        """
        Atualiza o perfil de imagem da câmera (auto, day, night) e opcionalmente os horários.
        Executa a calibração RPC2 imediatamente para troca instantânea de modo na câmera.
        """
        if mode not in ("auto", "day", "night"):
            raise ValueError(f"Modo inválido: {mode}. Opções aceitas: 'auto', 'day', 'night'.")

        with self._lock:
            self.image_profile_mode = mode
            if start_time:
                self.night_start_time = start_time
            if end_time:
                self.night_end_time = end_time

        save_image_profile_mode(mode, self.night_start_time, self.night_end_time)
        logger.info(
            f"[Perfil de Imagem] Modo atualizado para '{mode}' (Janela Noturna: {self.night_start_time} - {self.night_end_time}, Ativo: {'NOITE' if self.is_night_active else 'DIA'})."
        )
        threading.Thread(target=self._ensure_optimal_camera_image_rpc, daemon=True).start()

        return {
            "status": "updated",
            "image_profile_mode": self.image_profile_mode,
            "night_start_time": self.night_start_time,
            "night_end_time": self.night_end_time,
            "is_night_active": self.is_night_active,
        }

    def _rpc_login(self, timeout: float = 5.0):
        """Autentica na API JSON-RPC 2.0 (/RPC2_Login) da Intelbras com desafio duplo-MD5."""
        s = requests.Session()
        r1 = s.post(
            f"http://{self.ip}:{self.http_port}/RPC2_Login",
            json={
                "method": "global.login",
                "params": {"userName": self.user, "password": "", "clientType": "Web3.0", "loginType": "Direct"},
                "id": 1,
                "session": 0,
            },
            timeout=timeout,
        )
        d1 = r1.json()
        sid = d1.get("session")
        params = d1.get("params", {})
        realm = params.get("realm")
        random_nonce = params.get("random")
        if not realm or not random_nonce:
            return None, None, None

        inner = hashlib.md5(f"{self.user}:{realm}:{self.password}".encode("utf-8")).hexdigest().upper()
        final_pwd = hashlib.md5(f"{self.user}:{random_nonce}:{inner}".encode("utf-8")).hexdigest().upper()

        r2 = s.post(
            f"http://{self.ip}:{self.http_port}/RPC2_Login",
            json={
                "method": "global.login",
                "params": {
                    "userName": self.user,
                    "password": final_pwd,
                    "realm": realm,
                    "random": random_nonce,
                    "clientType": "Web3.0",
                    "loginType": "Direct",
                },
                "id": 2,
                "session": sid,
            },
            timeout=timeout,
        )
        if not r2.json().get("result"):
            return None, None, None

        def _rpc(method, rpc_params=None):
            return s.post(
                f"http://{self.ip}:{self.http_port}/RPC2",
                json={"method": method, "params": rpc_params or {}, "id": 10, "session": sid},
                timeout=timeout,
            ).json()

        return s, sid, _rpc

    def _rpc_logout(self, s, sid, timeout: float = 2.0):
        """Encerra explicitamente a sessão JSON-RPC 2.0 na câmera para nunca esgotar o limite de logins simultâneos."""
        if not s:
            return
        try:
            if sid:
                s.post(
                    f"http://{self.ip}:{self.http_port}/RPC2",
                    json={"method": "global.logout", "params": None, "id": 99, "session": sid},
                    timeout=timeout,
                )
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass

    def _ensure_optimal_camera_hardware_rpc(self, timeout: float = 5.0) -> bool:
        """Executa a calibração completa de áudio (AAC 16kHz), imagem (WDR Dia/Noite + Smart IR + Relógio) e Sobreposição Nativa (IVS)."""
        ok_audio = self._ensure_optimal_camera_audio_rpc(timeout=timeout)
        ok_image = self._ensure_optimal_camera_image_rpc(timeout=timeout)
        ok_overlay = self._apply_target_overlay_rpc(timeout=timeout)
        return bool(ok_audio and ok_image)

    def _apply_target_overlay_rpc(self, timeout: float = 5.0) -> bool:
        """
        Ativa ou desativa a sobreposição nativa de alvo e regras (Target Overlay / IVS)
        diretamente no hardware da câmera Intelbras via CGI e JSON-RPC 2.0.
        Isso faz a câmera desenhar as caixas de detecção no próprio fluxo de vídeo (0% CPU).
        """
        val_str = "true" if self.target_overlay else "false"
        ok = False

        # 1. Tentativa via CGI configManager (stateless HTTP Digest)
        try:
            url = (
                f"http://{self.ip}:{self.http_port}/cgi-bin/configManager.cgi?"
                f"action=setConfig&VideoAnalyseRule[0][0].ShowTrack={val_str}"
                f"&VideoAnalyseRule[0][0].ShowRule={val_str}"
                f"&VideoInAnalyse[0].ShowTrack={val_str}"
            )
            resp = requests.get(url, auth=HTTPDigestAuth(self.user, self.password), timeout=timeout)
            if resp.status_code == 200 and "OK" in resp.text.upper():
                ok = True
                logger.info(f"[Target Overlay] CGI configurado na câmera: ShowTrack={val_str}")
        except Exception as e:
            logger.debug(f"[Target Overlay] Tentativa CGI falhou: {e}")

        # 2. Tentativa via JSON-RPC 2.0 (VideoAnalyseRule)
        s, sid = None, None
        try:
            with self._rpc_lock:
                s, sid, _rpc = self._rpc_login(timeout=timeout)
                if _rpc:
                    cfg_res = _rpc("configManager.getConfig", {"name": "VideoAnalyseRule"})
                    rules = cfg_res.get("params", {}).get("table", {})
                    if rules and isinstance(rules, dict):
                        changed = False
                        for ch, ch_rules in rules.items():
                            if isinstance(ch_rules, list):
                                for r in ch_rules:
                                    if isinstance(r, dict):
                                        r["ShowTrack"] = self.target_overlay
                                        r["ShowRule"] = self.target_overlay
                                        changed = True
                        if changed:
                            set_res = _rpc("configManager.setConfig", {"name": "VideoAnalyseRule", "table": rules})
                            if set_res.get("result"):
                                ok = True
                                logger.info(f"[Target Overlay] RPC2 VideoAnalyseRule salvo: ShowTrack={self.target_overlay}")
        except Exception as e:
            logger.debug(f"[Target Overlay] Tentativa RPC2 falhou: {e}")
        finally:
            self._rpc_logout(s, sid)

        return ok

    def _ensure_optimal_camera_audio_rpc(self, timeout: float = 5.0) -> bool:
        """
        Garante que o hardware da câmera Intelbras capture áudio em AAC 16000Hz (16-bit)
        com volume de microfone em 100%, além de elevar o bitrate de vídeo 1080p para 3584 kbps.
        """
        s, sid = None, None
        try:
            with self._rpc_lock:
                s, sid, _rpc = self._rpc_login(timeout=timeout)
                if not _rpc:
                    return False

                # 1. Garante ganho do microfone em 100%
                vol_cfg = _rpc("configManager.getConfig", {"name": "AudioInputVolume"})
                if vol_cfg.get("result") and vol_cfg.get("params", {}).get("table") != [100]:
                    _rpc("configManager.setConfig", {"name": "AudioInputVolume", "table": [100]})

                # 2. Garante codec AAC 16000Hz (16-bit) e bitrate ótimo de vídeo no MainFormat (1080p) e ExtraFormat (480p)
                enc_cfg = _rpc("configManager.getConfig", {"name": "Encode"})
                if enc_cfg.get("result"):
                    table = enc_cfg["params"]["table"]
                    changed = False
                    target_video_bitrates = {"MainFormat": (3584, 6), "ExtraFormat": (768, 5)}
                    for stream_group, (target_br, target_q) in target_video_bitrates.items():
                        formats = table[0].get(stream_group, [])
                        for fmt in formats:
                            if isinstance(fmt.get("Audio"), dict):
                                audio = fmt["Audio"]
                                if (
                                    audio.get("Compression") != "AAC"
                                    or audio.get("Frequency") != 16000
                                    or not fmt.get("AudioEnable")
                                ):
                                    audio["Compression"] = "AAC"
                                    audio["Frequency"] = 16000
                                    fmt["AudioEnable"] = True
                                    changed = True
                            if isinstance(fmt.get("Video"), dict):
                                video = fmt["Video"]
                                if (
                                    video.get("BitRate") != target_br
                                    or video.get("Quality") != target_q
                                    or video.get("Compression") != "H.264"
                                    or video.get("FPS") != 20
                                ):
                                    video["BitRate"] = target_br
                                    video["Quality"] = target_q
                                    video["Compression"] = "H.264"
                                    video["Profile"] = "High"
                                    video["FPS"] = 20
                                    video["GOP"] = 40
                                    changed = True
                    if changed:
                        _rpc("configManager.setConfig", {"name": "Encode", "table": table})
                        logger.info("[Áudio/Vídeo Hardware] Câmera configurada para AAC 16kHz + 1080p 3584kbps.")
                return True
        except Exception as e:
            logger.debug(f"[Áudio Hardware] Não foi possível verificar/ajustar AAC 16kHz via RPC2: {e}")
            return False
        finally:
            self._rpc_logout(s, sid)

    def _ensure_optimal_camera_image_rpc(self, timeout: float = 5.0) -> bool:
        """
        Calibra o ISP da Intelbras VIP 1230 FC+ para máxima qualidade de imagem e detecção YOLO
        em cenário de corredor/garagem coberta apontando para o portão:
        - Sincroniza relógio interno (America/Sao_Paulo) e ativa NTP (a.ntp.br) para eliminar data 01-01-2000.
        - Regra DIA (05:00 às 19:00 - Perfil 0): Color + WDR 65 + Compensação 48 + Nitidez 58.
        - Regra NOITE (19:00 às 05:00 - Perfil 1): Smart IR Auto P&B + WDR 52 + Compensação 42 + Contraste 58 / Gamma 44
          (impede que a luz fraca do poste da rua estoure o portão em branco e ilumina a garagem por igual).
        - 3D-DNR em 42 para evitar rastro fantasma (motion blur) quando uma pessoa caminha.
        """
        s, sid = None, None
        try:
            with self._rpc_lock:
                s, sid, _rpc = self._rpc_login(timeout=timeout)
                if not _rpc:
                    return False

            # 1. Sincroniza relógio da câmera com horário de Brasília e habilita NTP
            now_str = get_now_str("%Y-%m-%d %H:%M:%S")
            _rpc("global.setCurrentTime", {"time": now_str})
            ntp_cfg = _rpc("configManager.getConfig", {"name": "NTP"})
            if ntp_cfg.get("result") and isinstance(ntp_cfg.get("params", {}).get("table"), dict):
                ntp_table = ntp_cfg["params"]["table"]
                ntp_table["Enable"] = True
                ntp_table["Address"] = "a.ntp.br"
                ntp_table["TimeZone"] = 22
                ntp_table["UpdatePeriod"] = 60
                _rpc("configManager.setConfig", {"name": "NTP", "table": ntp_table})

            # 2. No firmware da VIP 1230 FC+ (SupportProfile=false), o ISP lê sempre o slot [0] ativo.
            # Por isso aplicamos a regra vigente (NOITE 19h-05h = 4_ir_with_wdr38 | DIA 05h-19h = Color+WDR60)
            # diretamente em todos os slots [0..2] e o _capture_loop alterna automaticamente às 05:00 e às 19:00.
            is_night = self._is_night_window()
            active_wdr = 38 if is_night else 60
            active_comp = 40 if is_night else 46
            active_dn_mode = "BlackWhite" if is_night else "Color"
            active_dn_color = 2 if is_night else 0
            active_brightness = 45 if is_night else 48
            active_contrast = 62 if is_night else 56
            active_gamma = 40 if is_night else 46
            active_saturation = 50 if is_night else 55

            bl_cfg = _rpc("configManager.getConfig", {"name": "VideoInBacklight"})
            if bl_cfg.get("result"):
                bl_table = bl_cfg["params"]["table"]
                for p in range(len(bl_table[0])):
                    bl_table[0][p]["Mode"] = "WideDynamic"
                    bl_table[0][p]["WideDynamicRange"] = active_wdr
                _rpc("configManager.setConfig", {"name": "VideoInBacklight", "table": bl_table})

            # 3. Exposição anti-estouro (Noite: Comp=40, WDR=38 | Dia: Comp=46, WDR=60; GainMax=38)
            exp_cfg = _rpc("configManager.getConfig", {"name": "VideoInExposure"})
            if exp_cfg.get("result"):
                exp_table = exp_cfg["params"]["table"]
                for p in range(len(exp_table[0])):
                    exp_table[0][p]["Compensation"] = active_comp
                    exp_table[0][p]["GainMax"] = 38
                    exp_table[0][p]["Mode"] = 0
                    exp_table[0][p]["Value1"] = 0
                    exp_table[0][p]["Value2"] = 33.33
                    exp_table[0][p]["WideDynamicRangeMode"] = 1
                    exp_table[0][p]["WideDynamicRange"] = active_wdr
                _rpc("configManager.setConfig", {"name": "VideoInExposure", "table": exp_table})

            # 4. Modo Dia/Noite vigente em todos os slots (Noite 19h-05h = BlackWhite IR | Dia 05h-19h = Color)
            dn_cfg = _rpc("configManager.getConfig", {"name": "VideoInDayNight"})
            if dn_cfg.get("result"):
                dn_table = dn_cfg["params"]["table"]
                for p in range(len(dn_table[0])):
                    dn_table[0][p]["Mode"] = active_dn_mode
                    dn_table[0][p]["Sensitivity"] = 2
                    dn_table[0][p]["Delay"] = 3
                _rpc("configManager.setConfig", {"name": "VideoInDayNight", "table": dn_table})

            # 5. Smart IR em modo Auto à noite (ou Off de dia) e Luz Branca em Off no repouso
            l2_cfg = _rpc("configManager.getConfig", {"name": "Lighting_V2"})
            if l2_cfg.get("result"):
                l2_table = l2_cfg["params"]["table"]
                for p in range(len(l2_table[0])):
                    l2_table[0][p][0]["Mode"] = "Auto" if is_night else "Off"
                    l2_table[0][p][0]["Correction"] = 50
                    l2_table[0][p][0]["MiddleLight"][0]["Light"] = 100 if is_night else 0
                    l2_table[0][p][1]["Mode"] = "Off"
                    l2_table[0][p][1]["NearLight"][0]["Light"] = 0
                _rpc("configManager.setConfig", {"name": "Lighting_V2", "table": l2_table})

            # 6. VideoColor, Nitidez (58) e 3D-DNR (42 contra motion blur)
            vc_cfg = _rpc("configManager.getConfig", {"name": "VideoColor"})
            if vc_cfg.get("result"):
                vc_table = vc_cfg["params"]["table"]
                for p in range(len(vc_table[0])):
                    vc_table[0][p].update({
                        "Brightness": active_brightness,
                        "Contrast": active_contrast,
                        "Saturation": active_saturation,
                        "Gamma": active_gamma,
                    })
                _rpc("configManager.setConfig", {"name": "VideoColor", "table": vc_table})

            sh_cfg = _rpc("configManager.getConfig", {"name": "VideoInSharpness"})
            if sh_cfg.get("result"):
                sh_table = sh_cfg["params"]["table"]
                for p in range(len(sh_table[0])):
                    sh_table[0][p]["Sharpness"] = 58
                    sh_table[0][p]["Level"] = 58
                _rpc("configManager.setConfig", {"name": "VideoInSharpness", "table": sh_table})

            den_cfg = _rpc("configManager.getConfig", {"name": "VideoInDenoise"})
            if den_cfg.get("result"):
                den_table = den_cfg["params"]["table"]
                for p in range(len(den_table[0])):
                    den_table[0][p]["2DEnable"] = True
                    den_table[0][p]["2DLevel"] = 3
                    den_table[0][p]["3DType"] = "Auto"
                    if isinstance(den_table[0][p].get("3DAutoType"), dict):
                        den_table[0][p]["3DAutoType"]["AutoLevel"] = 42
                _rpc("configManager.setConfig", {"name": "VideoInDenoise", "table": den_table})

            # 7. VideoInOptions (aplica regra vigente em root, NightOptions e NormalOptions)
            opt_cfg = _rpc("configManager.getConfig", {"name": "VideoInOptions"})
            if opt_cfg.get("result"):
                opt_table = opt_cfg["params"]["table"]
                root = opt_table[0]
                for node in [root, root.get("NightOptions"), root.get("NormalOptions")]:
                    if isinstance(node, dict):
                        node.update({
                            "SunriseHour": 5,
                            "SunriseMinute": 0,
                            "SunsetHour": 19,
                            "SunsetMinute": 0,
                            "WideDynamicRangeMode": 1,
                            "WideDynamicRange": active_wdr,
                            "ExposureCompensation": active_comp,
                            "ExposureMode": 0,
                            "ExposureValue1": 0,
                            "ExposureValue2": 33.33,
                            "ReferenceLevel": 50,
                            "GainMax": 38,
                            "DayNightColor": active_dn_color,
                        })
                _rpc("configManager.setConfig", {"name": "VideoInOptions", "table": opt_table})

            # 8. Agenda automática de perfis em VideoInMode
            vm_cfg = _rpc("configManager.getConfig", {"name": "VideoInMode"})
            if vm_cfg.get("result"):
                vm_table = vm_cfg["params"]["table"]
                vm_table[0]["Mode"] = 0
                vm_table[0]["Config"] = [1 if is_night else 0]
                day_schedule = [
                    "1 00:00:00-04:59:59",
                    "0 05:00:00-18:59:59",
                    "1 19:00:00-24:00:00",
                    "0 00:00:00-00:00:00",
                    "0 00:00:00-00:00:00",
                    "0 00:00:00-00:00:00",
                ]
                vm_table[0]["TimeSection"] = [list(day_schedule) for _ in range(7)]
                _rpc("configManager.setConfig", {"name": "VideoInMode", "table": vm_table})

            logger.info(
                f"[Imagem Hardware] VIP 1230 FC+ calibrada (Perfil ativo: {'NOITE 19h-05h IR+WDR38' if is_night else 'DIA 05h-19h Color+WDR60'})."
            )
            return True
        except Exception as e:
            logger.debug(f"[Imagem Hardware] Não foi possível calibrar imagem via RPC2: {e}")
            return False
        finally:
            self._rpc_logout(s, sid)

    def _set_camera_lighting_rpc(self, on: bool = True, intensity: int = 100, timeout: float = 5.0) -> bool:
        """
        Controla o LED de Luz Branca (Full Color / Flash) da Intelbras VIP 1230 FC+
        utilizando a API nativa JSON-RPC 2.0 (/RPC2_Login e /RPC2) com autenticação Digest duplo-MD5.
        - on=True: Aplica exatamente o perfil 6_flash_wdr_off (LED Branco 100%, Color, WDR Off, Comp 48, Contraste 56, Gamma 46, Sat 56).
        - on=False: Restaura exatamente o perfil vigente de repouso (Noite 19h-05h = 4_ir_with_wdr38 | Dia 05h-19h = Color + WDR 60)
          em todos os slots [0..2], garantindo que o WDR e o Smart IR nunca se percam após o flash!
        """
        s, sid = None, None
        try:
            with self._rpc_lock:
                s, sid, _rpc = self._rpc_login(timeout=timeout)
                if not _rpc:
                    logger.error("Autenticação RPC2 falhou ao controlar iluminação da câmera.")
                    return False

                is_night = self._is_night_window()
                rest_wdr = 38 if is_night else 60
                rest_comp = 40 if is_night else 46
                rest_dn_mode = "BlackWhite" if is_night else "Color"
                rest_dn_color = 2 if is_night else 0
                rest_brightness = 45 if is_night else 48
                rest_contrast = 62 if is_night else 56
                rest_gamma = 40 if is_night else 46
                rest_saturation = 50 if is_night else 55

                mode = "WhiteMode" if on else "InfraredMode"

                # 1. LightingScheme
                scheme_cfg = _rpc("configManager.getConfig", {"name": "LightingScheme"})
                if scheme_cfg.get("result"):
                    scheme = scheme_cfg["params"]["table"]
                    for p in range(len(scheme[0])):
                        scheme[0][p]["LightingMode"] = mode
                    _rpc("configManager.setConfig", {"name": "LightingScheme", "table": scheme})

                # 2. VideoInDayNight: durante o flash (on=True) força Color; ao desligar (on=False)
                # restaura o modo vigente em todos os slots [0..2] (BlackWhite à noite, Color de dia)
                daynight_cfg = _rpc("configManager.getConfig", {"name": "VideoInDayNight"})
                if daynight_cfg.get("result"):
                    daynight = daynight_cfg["params"]["table"]
                    for p in range(len(daynight[0])):
                        daynight[0][p]["Mode"] = "Color" if on else rest_dn_mode
                    _rpc("configManager.setConfig", {"name": "VideoInDayNight", "table": daynight})

                # 3. Lighting_V2
                lighting_v2_cfg = _rpc("configManager.getConfig", {"name": "Lighting_V2"})
                if lighting_v2_cfg.get("result"):
                    lighting_v2 = lighting_v2_cfg["params"]["table"]
                    for p in range(len(lighting_v2[0])):
                        # Index 1 = WhiteLight (Luz Branca / Flash)
                        lighting_v2[0][p][1]["Mode"] = "Manual" if on else "Off"
                        lighting_v2[0][p][1]["PercentOfMaxBrightness"] = 100
                        lighting_v2[0][p][1]["NearLight"][0]["Light"] = intensity if on else 0
                        lighting_v2[0][p][1]["NearLight"][0]["Angle"] = 50
                        if on:
                            # Desliga o IR durante o flash para fotos e vídeos em cores puras
                            lighting_v2[0][p][0]["MiddleLight"][0]["Light"] = 0
                            lighting_v2[0][p][0]["Mode"] = "Off"
                        else:
                            # Restaura o Smart IR automático (Mode="Auto") no modo de espera noturno
                            lighting_v2[0][p][0]["Mode"] = "Auto" if is_night else "Off"
                            lighting_v2[0][p][0]["Correction"] = 50
                            lighting_v2[0][p][0]["MiddleLight"][0]["Light"] = 100 if is_night else 0
                    _rpc("configManager.setConfig", {"name": "Lighting_V2", "table": lighting_v2})

                # 4. WDR, Exposição e Cor dinâmicos no Flash (6_flash_wdr_off quando on=True -> 4_ir_with_wdr38 quando on=False)
                bl_cfg = _rpc("configManager.getConfig", {"name": "VideoInBacklight"})
                if bl_cfg.get("result"):
                    bl_table = bl_cfg["params"]["table"]
                    for p in range(len(bl_table[0])):
                        bl_table[0][p]["Mode"] = "Off" if on else "WideDynamic"
                        if not on:
                            bl_table[0][p]["WideDynamicRange"] = rest_wdr
                    _rpc("configManager.setConfig", {"name": "VideoInBacklight", "table": bl_table})

                exp_cfg = _rpc("configManager.getConfig", {"name": "VideoInExposure"})
                if exp_cfg.get("result"):
                    exp_table = exp_cfg["params"]["table"]
                    for p in range(len(exp_table[0])):
                        exp_table[0][p]["WideDynamicRangeMode"] = 0 if on else 1
                        exp_table[0][p]["WideDynamicRange"] = 0 if on else rest_wdr
                        exp_table[0][p]["Compensation"] = 48 if on else rest_comp
                        exp_table[0][p]["GainMax"] = 38
                    _rpc("configManager.setConfig", {"name": "VideoInExposure", "table": exp_table})

                opt_cfg = _rpc("configManager.getConfig", {"name": "VideoInOptions"})
                if opt_cfg.get("result"):
                    opt_table = opt_cfg["params"]["table"]
                    root = opt_table[0]
                    for node in [root, root.get("NightOptions"), root.get("NormalOptions")]:
                        if isinstance(node, dict):
                            node["WideDynamicRangeMode"] = 0 if on else 1
                            node["WideDynamicRange"] = 0 if on else rest_wdr
                            node["ExposureCompensation"] = 48 if on else rest_comp
                            node["GainMax"] = 38
                            node["DayNightColor"] = 0 if on else rest_dn_color
                    _rpc("configManager.setConfig", {"name": "VideoInOptions", "table": opt_table})

                vc_cfg = _rpc("configManager.getConfig", {"name": "VideoColor"})
                if vc_cfg.get("result"):
                    vc_table = vc_cfg["params"]["table"]
                    for p in range(len(vc_table[0])):
                        vc_table[0][p].update({
                            "Brightness": 48 if on else rest_brightness,
                            "Contrast": 56 if on else rest_contrast,
                            "Saturation": 56 if on else rest_saturation,
                            "Gamma": 46 if on else rest_gamma,
                        })
                    _rpc("configManager.setConfig", {"name": "VideoColor", "table": vc_table})

                return True
        except Exception as e:
            logger.error(f"Erro ao controlar iluminação via RPC: {e}")
            return False
        finally:
            self._rpc_logout(s, sid)

    def toggle_light(self, on: bool | None = None) -> dict:
        """
        Liga ou desliga o LED de Luz Branca (100% contínuo) da câmera Intelbras VIP 1230 FC+.
        Ao ligar contínuo, a luz permanece 100% acesa indefinidamente até novo clique.
        Ao desligar, restaura o perfil ótimo vigente (Smart IR + WDR à noite ou Color de dia).
        """
        with self._lock:
            if on is None:
                target_state = not self._continuous_light
            else:
                target_state = bool(on)

            self._continuous_light = target_state
            if target_state:
                self._flash_active = True
                self._flash_until = 0.0
            else:
                self._flash_active = False
                self._flash_until = 0.0

        def _light_worker():
            try:
                action_str = "LIGADO (100% CONTÍNUO)" if target_state else "DESLIGADO (RESTAURANDO VIGENTE)"
                logger.info(f"[LUZ CONTÍNUA] Configurando LED de luz branca para {action_str}...")
                ok = self._set_camera_lighting_rpc(on=target_state, intensity=100)
                if not ok:
                    time.sleep(0.3)
                    self._set_camera_lighting_rpc(on=target_state, intensity=100)
            except Exception as e:
                logger.error(f"[LUZ CONTÍNUA] Erro ao alternar iluminação: {e}")

        threading.Thread(target=_light_worker, daemon=True).start()
        return {"status": "ok", "continuous_light": target_state}

    def trigger_flash(self, seconds: float = 2.0) -> dict:
        """
        Dispara o LED de luz branca da câmera Intelbras VIP 1230 FC+ por N segundos.
        Deduplica cliques múltiplos estendendo o timer sem criar threads concorrentes.
        Se a luz contínua estiver ativa, não desliga após o timer.
        """
        now = time.time()
        target_until = now + max(0.05, float(seconds))
        with self._lock:
            if self._continuous_light:
                return {"status": "triggered", "duration": seconds, "continuous_light": True}
            if target_until > self._flash_until:
                self._flash_until = target_until
            if self._flash_active:
                return {"status": "triggered", "duration": seconds, "extended": True}
            self._flash_active = True

        def _flash_worker():
            try:
                logger.info(f"[FLASH] Ligando LED de luz branca por {seconds}s...")
                success = self._set_camera_lighting_rpc(on=True, intensity=100)
                if not success:
                    logger.warning("[FLASH] Falha no acionamento RPC do flash, tentando novamente...")
                    time.sleep(0.3)
                    self._set_camera_lighting_rpc(on=True, intensity=100)

                while True:
                    with self._lock:
                        if self._continuous_light:
                            return
                        remaining = self._flash_until - time.time()
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 0.25))

                with self._lock:
                    if self._continuous_light:
                        return

                logger.info("[FLASH] Desligando LED de luz branca (restaurando perfil WDR vigente)...")
                if not self._set_camera_lighting_rpc(on=False):
                    time.sleep(0.4)
                    self._set_camera_lighting_rpc(on=False)
            except Exception as e:
                logger.error(f"[FLASH] Erro no worker de flash: {e}")
            finally:
                with self._lock:
                    if not self._continuous_light:
                        self._flash_active = False

        t = threading.Thread(target=_flash_worker, daemon=True)
        t.start()
        return {"status": "triggered", "duration": seconds}

    def reboot_camera(self, timeout: float = 5.0) -> dict:
        """
        Reinicia o hardware da câmera Intelbras VIP 1230 FC+ remotamente.
        1. Tenta via CGI HTTP Digest stateless (/cgi-bin/magicBox.cgi?action=reboot), que funciona
           mesmo quando o pool de sessões RPC2 está travado/cheio.
        2. Fallback via JSON-RPC 2.0 (magicBox.reboot).
        Após o reboot (~35s), o _capture_loop reconecta sozinho e recalibra áudio e WDR.
        """
        rebooted = False
        method_used = None

        # 1. Tentativa via CGI HTTP Digest stateless (independente de sessão RPC2)
        try:
            url = f"http://{self.ip}:{self.http_port}/cgi-bin/magicBox.cgi?action=reboot"
            resp = requests.get(url, auth=HTTPDigestAuth(self.user, self.password), timeout=timeout)
            if resp.status_code == 200 and ("OK" in resp.text.upper() or resp.text.strip() == ""):
                rebooted = True
                method_used = "CGI magicBox.reboot"
        except Exception as e:
            logger.debug(f"[Reboot] Tentativa CGI magicBox falhou: {e}")

        # 2. Tentativa via JSON-RPC 2.0 (magicBox.reboot)
        if not rebooted:
            s, sid = None, None
            try:
                with self._rpc_lock:
                    s, sid, _rpc = self._rpc_login(timeout=timeout)
                    if _rpc:
                        res = _rpc("magicBox.reboot")
                        if res.get("result"):
                            rebooted = True
                            method_used = "RPC2 magicBox.reboot"
            except Exception as e:
                logger.warning(f"[Reboot] Tentativa RPC2 magicBox.reboot falhou: {e}")
            finally:
                self._rpc_logout(s, sid)

        if rebooted:
            with self._lock:
                self._flash_active = False
                self._flash_until = 0.0
                self.status = "CONNECTING"
            logger.warning(f"[Reboot] Câmera {self.ip} reiniciada remotamente via {method_used}!")
            return {
                "status": "rebooting",
                "method": method_used,
                "message": "Comando de reinicialização enviado! A câmera voltará online em ~35 segundos com WDR e áudio calibrados.",
            }

        return {
            "status": "error",
            "message": "Não foi possível enviar o comando de reboot à câmera. Verifique se ela está acessível na rede.",
        }

    def take_snapshot(self, prefix: str = "snapshot") -> str:
        """
        Tira um snapshot em alta resolução:
        Tenta via CGI snapshot da câmera (1080p nativo direto do sensor).
        Se falhar, usa o último frame recebido do RTSP.
        """
        timestamp = get_now_str("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{timestamp}.jpg"
        snapshots_dir = "backend/snapshots" if os.path.exists("backend/snapshots") else "snapshots"
        os.makedirs(snapshots_dir, exist_ok=True)
        filepath = os.path.join(snapshots_dir, filename)

        try:
            url = f"http://{self.ip}:{self.http_port}/cgi-bin/snapshot.cgi?channel=1"
            resp = requests.get(url, auth=HTTPDigestAuth(self.user, self.password), timeout=4)
            if resp.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                logger.info(f"Snapshot CGI salvo: {filepath}")
                return filename
        except Exception as e:
            logger.warning(f"Snapshot CGI falhou ({e}), usando frame RTSP...")

        with self._lock:
            if self.latest_frame_raw is not None:
                cv2.imwrite(filepath, self.latest_frame_raw, [cv2.IMWRITE_JPEG_QUALITY, 95])
                logger.info(f"Snapshot RTSP salvo: {filepath}")
                return filename

        raise RuntimeError("Nenhum sinal de câmera disponível para snapshot.")

    def record_clip(self, seconds: int = 20, max_seconds: int = 120, on_complete=None) -> str:
        """
        Grava um clipe de vídeo MP4 com áudio sincronizado e marcações YOLO.
        Lógica dinâmica (Post-Roll Buffer):
        - Inicia com duração base de `seconds` (padrão 20s).
        - Se o detector continuar detectando movimento/pessoa, `extend_clip()` estende a gravação.
        - Quando o movimento cessa, a gravação grava o pós-movimento e encerra um único vídeo contínuo.
        - Possui teto máximo de segurança `max_seconds` (padrão 120s / 2 min).
        """
        with self._clip_lock:
            if self._clip_active:
                # Já está gravando: apenas estende a gravação atual
                now = time.time()
                max_deadline = self._clip_start_time + self.clip_max_duration
                self._clip_deadline = min(now + 10.0, max_deadline)
                return self._current_clip_filename or "active_clip.mp4"

            import subprocess
            from app.routers.stream import resolve_ffmpeg_bin, CLEAN_AUDIO_FILTER
            timestamp = get_now_str("%Y%m%d_%H%M%S")
            filename = f"video_{timestamp}.mp4"
            rec_dir = "backend/recordings" if os.path.exists("backend/recordings") else "recordings"
            os.makedirs(rec_dir, exist_ok=True)
            filepath = os.path.join(rec_dir, filename)

            self._clip_active = True
            self._clip_start_time = time.time()
            self._clip_deadline = self._clip_start_time + max(5, int(seconds))
            self.clip_max_duration = max(seconds, int(max_seconds))
            self._current_clip_filename = filename

        rtsp_target = self.mainstream_rtsp_url
        ffmpeg_bin = resolve_ffmpeg_bin()
        direct_cmd = [
            ffmpeg_bin,
            "-nostats",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", rtsp_target,
            "-t", str(self.clip_max_duration),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "64k",
            "-af", CLEAN_AUDIO_FILTER,
            "-movflags", "+faststart",
            "-y",
            filepath
        ]

        def _worker():
            try:
                logger.info(f"Iniciando gravação dinâmica de vídeo MP4 1080p ({seconds}s base, até {max_seconds}s): {filename}")
                recorded_annotated = False
                if self.status == "ONLINE" and self.latest_frame_jpeg is not None:
                    fps = 12
                    annotated_cmd = [
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
                        "-i", self.rtsp_url,
                        "-map", "0:v:0",
                        "-map", "1:a:0?",
                        "-shortest",
                        "-t", str(self.clip_max_duration),
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-crf", "23",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac",
                        "-b:a", "64k",
                        "-af", CLEAN_AUDIO_FILTER,
                        "-movflags", "+faststart",
                        "-y",
                        filepath,
                    ]
                    try:
                        proc = subprocess.Popen(
                            annotated_cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        interval = 1.0 / fps
                        while True:
                            with self._clip_lock:
                                now = time.time()
                                if now >= self._clip_deadline or (now - self._clip_start_time) >= self.clip_max_duration:
                                    break
                            if proc.poll() is not None:
                                break
                            jpg = self.get_latest_jpeg()
                            if jpg and proc.stdin:
                                try:
                                    proc.stdin.write(jpg)
                                    proc.stdin.flush()
                                except Exception:
                                    break
                            time.sleep(interval)

                        if proc.stdin:
                            try:
                                proc.stdin.close()
                            except Exception:
                                pass
                        proc.wait(timeout=10)
                        if os.path.exists(filepath) and os.path.getsize(filepath) > 1024:
                            recorded_annotated = True
                    except Exception as pipe_err:
                        logger.warning(f"Fallback gravação direta RTSP ({pipe_err})...")

                if not recorded_annotated:
                    try:
                        subprocess.run(direct_cmd, timeout=self.clip_max_duration + 10, check=True)
                    except subprocess.CalledProcessError:
                        logger.warning("[!] Falha no stream 1080p para gravação, usando fallback para stream ativo...")
                        fallback_cmd = list(direct_cmd)
                        fallback_cmd[fallback_cmd.index(rtsp_target)] = self.rtsp_url
                        subprocess.run(fallback_cmd, timeout=self.clip_max_duration + 10, check=True)

                logger.info(f"[+] Vídeo MP4 dinâmico gravado com áudio sincronizado: {filepath}")
                if on_complete:
                    try:
                        on_complete(filepath, filename)
                    except Exception as err:
                        logger.error(f"Erro no callback pós-gravação de vídeo: {err}")
            except Exception as e:
                logger.error(f"Erro ao gravar clipe de vídeo: {e}")
            finally:
                with self._clip_lock:
                    self._clip_active = False
                    self._current_clip_filename = None

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return filename

    @staticmethod
    def _parse_subnets(subnets_input: str | list[str] | None) -> list[str]:
        """
        Normaliza entradas de sub-redes como '192.168.1, 192.168.2.1, 192.168.0.0/24'
        para ['192.168.1', '192.168.2', '192.168.0'].
        """
        raw_items: list[str] = []
        if isinstance(subnets_input, list):
            raw_items = subnets_input
        elif isinstance(subnets_input, str):
            import re
            raw_items = [part.strip() for part in re.split(r"[,;\s]+", subnets_input) if part.strip()]

        normalized = set()
        for item in raw_items:
            clean = item.split("/")[0].strip()
            parts = clean.split(".")
            if len(parts) >= 3:
                try:
                    octets = [int(p) for p in parts[:3]]
                    if all(0 <= o <= 255 for o in octets):
                        normalized.add(f"{octets[0]}.{octets[1]}.{octets[2]}")
                except ValueError:
                    continue

        if not normalized:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                parts = local_ip.split(".")
                normalized.add(f"{parts[0]}.{parts[1]}.{parts[2]}")
            except Exception:
                normalized.add("192.168.1")

            # Se a sub-rede principal for 192.168.1, inclui também 192.168.2 (segundo modem/roteador comum)
            if "192.168.1" in normalized:
                normalized.add("192.168.2")

        return sorted(list(normalized))

    @classmethod
    def scan_network_cameras(cls, subnets: str | list[str] | None = None) -> dict:
        """
        Varre uma ou mais sub-redes em paralelo procurando câmeras IP e dispositivos.
        Portas avaliadas:
          - 554: RTSP (streaming de vídeo universal)
          - 80: HTTP (web da câmera / snapshot CGI)
          - 37777: Dahua / Intelbras RPC e vídeo nativo
          - 8000: Hikvision
          - 8899: ONVIF Device Service
        """
        target_subnets = cls._parse_subnets(subnets)
        all_ips = []
        for subnet in target_subnets:
            all_ips.extend([f"{subnet}.{i}" for i in range(1, 255)])

        ports = [37777, 554, 80, 8000, 8899]

        def _check_ip(ip):
            found = []
            for port in ports:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.25)
                try:
                    if s.connect_ex((ip, port)) == 0:
                        found.append(port)
                except Exception:
                    pass
                finally:
                    s.close()

            if found:
                if 37777 in found:
                    name = "Intelbras / Dahua"
                    is_camera = True
                elif 8000 in found:
                    name = "Hikvision"
                    is_camera = True
                elif 554 in found:
                    name = "Câmera IP (RTSP)"
                    is_camera = True
                elif 8899 in found:
                    name = "Câmera ONVIF"
                    is_camera = True
                else:
                    name = "Dispositivo Web (HTTP)"
                    is_camera = False

                parts = ip.split(".")
                return {
                    "ip": ip,
                    "subnet": f"{parts[0]}.{parts[1]}.{parts[2]}.0/24",
                    "ports": found,
                    "name": name,
                    "is_camera": is_camera,
                }
            return None

        results = []
        workers = min(120, max(25, len(all_ips) // 4))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_check_ip, ip) for ip in all_ips]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res:
                    results.append(res)

        sorted_results = sorted(
            results,
            key=lambda x: (
                0 if x["is_camera"] else 1,
                [int(p) for p in x["ip"].split(".")]
            )
        )
        return {
            "cameras": sorted_results,
            "scanned_subnets": [f"{s}.0/24" for s in target_subnets]
        }

camera_service = CameraService()
