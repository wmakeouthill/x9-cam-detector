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
from app.db import get_camera_config, save_camera_config

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

        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        
        self.latest_frame_raw = None
        self.latest_frame_jpeg = None
        self._active_tracks = []
        self.status = "OFFLINE" # ONLINE, CONNECTING, OFFLINE
        self.fps = 0.0
        self.width = 0
        self.height = 0
        self.last_frame_time = 0.0

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

    def update_config(self, ip: str, user: str, password: str, port: int = 554, http_port: int = 80, subtype: int = 1):
        with self._lock:
            self.ip = ip
            self.user = user
            self.password = password
            self.port = port
            self.http_port = http_port
            self.subtype = subtype
            self.status = "CONNECTING"
        # Persiste no banco com senha criptografada em AES-256
        save_camera_config(ip, port, http_port, user, password, subtype)
        logger.info(f"Configuração da câmera salva e atualizada: {ip}:{port} (Subtype {subtype})")

    def update_active_tracks(self, tracks: list):
        """Atualiza a lista de tracks/caixas rastreadas para sobreposição no streaming."""
        with self._lock:
            self._active_tracks = tracks

    def get_active_tracks(self) -> list:
        """Retorna as caixas YOLO ativas (com TTL de 1.0s)."""
        now = time.time()
        with self._lock:
            tracks = [t for t in self._active_tracks if (now - t.get("last_seen", 0)) < 1.0]
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
        ui_scale = max(1.0, w / 960.0)
        box_thickness = max(2, int(2 * ui_scale))
        font_scale = 0.45 * ui_scale
        font_thickness = max(1, int(1 * ui_scale))

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

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, box_thickness)

            label = f"PESSOA #{track_id} ({int(conf * 100)}%)"
            (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)
            pad = int(6 * ui_scale)
            label_y = max(y1, th + pad)
            cv2.rectangle(frame, (x1, label_y - th - pad), (x1 + tw + pad + 2, label_y + baseline), color, -1)
            cv2.putText(
                frame,
                label,
                (x1 + int(4 * ui_scale), label_y - int(2 * ui_scale)),
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
        logger.info("Worker de captura RTSP iniciado.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("Worker de captura RTSP encerrado.")

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

                    # Renderiza anotações de tracking no frame se houver detecções ativas (TTL de 1.0s)
                    with self._lock:
                        self.latest_frame_raw = raw_frame
                        tracks = [t for t in self._active_tracks if (now - t.get("last_seen", 0)) < 1.0]
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
                                f"[Imagem Hardware] Virada de horário detectada ({'NOITE 19h-05h' if current_is_night else 'DIA 05h-19h'}): recalibrando perfil..."
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

    @staticmethod
    def _is_night_window() -> bool:
        """Retorna True no período noturno (19:00 às 05:00 BRT) e False no diurno (05:00 às 19:00 BRT)."""
        hour = get_now().hour
        return hour >= 19 or hour < 5

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

    def _ensure_optimal_camera_hardware_rpc(self, timeout: float = 5.0) -> bool:
        """Executa a calibração completa de áudio (AAC 16kHz) e imagem (WDR Dia/Noite + Smart IR + Relógio)."""
        ok_audio = self._ensure_optimal_camera_audio_rpc(timeout=timeout)
        ok_image = self._ensure_optimal_camera_image_rpc(timeout=timeout)
        return bool(ok_audio and ok_image)

    def _ensure_optimal_camera_audio_rpc(self, timeout: float = 5.0) -> bool:
        """
        Garante que o hardware da câmera Intelbras capture áudio em AAC 16000Hz (16-bit)
        com volume de microfone em 100%, além de elevar o bitrate de vídeo 1080p para 3584 kbps.
        """
        try:
            _, _, _rpc = self._rpc_login(timeout=timeout)
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
                            if video.get("BitRate") != target_br or video.get("Quality") != target_q:
                                video["BitRate"] = target_br
                                video["Quality"] = target_q
                                changed = True
                if changed:
                    _rpc("configManager.setConfig", {"name": "Encode", "table": table})
                    logger.info("[Áudio/Vídeo Hardware] Câmera configurada para AAC 16kHz + 1080p 3584kbps.")
            return True
        except Exception as e:
            logger.debug(f"[Áudio Hardware] Não foi possível verificar/ajustar AAC 16kHz via RPC2: {e}")
            return False

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
        try:
            _, _, _rpc = self._rpc_login(timeout=timeout)
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

    def _set_camera_lighting_rpc(self, on: bool = True, intensity: int = 100, timeout: float = 5.0) -> bool:
        """
        Controla o LED de Luz Branca (Full Color / Flash) da Intelbras VIP 1230 FC+
        utilizando a API nativa JSON-RPC 2.0 (/RPC2_Login e /RPC2) com autenticação Digest duplo-MD5.
        - on=True: Aplica exatamente o perfil 6_flash_wdr_off (LED Branco 100%, Color, WDR Off, Comp 48, Contraste 56, Gamma 46, Sat 56).
        - on=False: Restaura exatamente o perfil vigente de repouso (Noite 19h-05h = 4_ir_with_wdr38 | Dia 05h-19h = Color + WDR 60)
          em todos os slots [0..2], garantindo que o WDR e o Smart IR nunca se percam após o flash!
        """
        try:
            _, _, _rpc = self._rpc_login(timeout=timeout)
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

    def trigger_flash(self, seconds: float = 2.0) -> dict:
        """
        Dispara o LED de luz branca da câmera Intelbras VIP 1230 FC+ por N segundos.
        Executa em thread assíncrona para não bloquear a API nem o loop do detector.
        """
        def _flash_worker():
            try:
                logger.info(f"[FLASH] Ligando LED de luz branca por {seconds}s...")
                success = self._set_camera_lighting_rpc(on=True, intensity=100)
                if not success:
                    logger.warning("[FLASH] Falha no acionamento RPC do flash.")
                
                time.sleep(seconds)

                logger.info("[FLASH] Desligando LED de luz branca (restaurando AIMode)...")
                self._set_camera_lighting_rpc(on=False)
            except Exception as e:
                logger.error(f"[FLASH] Erro no worker de flash: {e}")

        t = threading.Thread(target=_flash_worker, daemon=True)
        t.start()
        return {"status": "triggered", "duration": seconds}

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

    def record_clip(self, seconds: int = 10, on_complete=None) -> str:
        """
        Grava um clipe de vídeo MP4 com áudio sincronizado e marcações YOLO.
        Prioriza os quadros anotados pelo YOLO em tempo real + áudio RTSP da câmera,
        com fallback automático para cópia direta do Stream Principal (1080p nativo).
        """
        import subprocess
        from app.routers.stream import resolve_ffmpeg_bin, CLEAN_AUDIO_FILTER
        timestamp = get_now_str("%Y%m%d_%H%M%S")
        filename = f"video_{timestamp}.mp4"
        rec_dir = "backend/recordings" if os.path.exists("backend/recordings") else "recordings"
        os.makedirs(rec_dir, exist_ok=True)
        filepath = os.path.join(rec_dir, filename)

        rtsp_target = self.mainstream_rtsp_url
        ffmpeg_bin = resolve_ffmpeg_bin()
        direct_cmd = [
            ffmpeg_bin,
            "-nostats",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", rtsp_target,
            "-t", str(seconds),
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
                logger.info(f"Iniciando gravação de vídeo MP4 1080p sincronizado com YOLO ({seconds}s): {filename}")
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
                        "-t", str(seconds),
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
                        total_frames = int(seconds * fps)
                        interval = 1.0 / fps
                        for _ in range(total_frames):
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
                        subprocess.run(direct_cmd, timeout=seconds + 10, check=True)
                    except subprocess.CalledProcessError:
                        logger.warning("[!] Falha no stream 1080p para gravação, usando fallback para stream ativo...")
                        fallback_cmd = list(direct_cmd)
                        fallback_cmd[fallback_cmd.index(rtsp_target)] = self.rtsp_url
                        subprocess.run(fallback_cmd, timeout=seconds + 10, check=True)

                logger.info(f"[+] Vídeo MP4 gravado com áudio sincronizado: {filepath}")
                if on_complete:
                    try:
                        on_complete(filepath, filename)
                    except Exception as err:
                        logger.error(f"Erro no callback pós-gravação de vídeo: {err}")
            except Exception as e:
                logger.error(f"Erro ao gravar clipe de vídeo: {e}")

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
