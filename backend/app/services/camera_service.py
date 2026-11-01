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
from app.core.time import get_now_str
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
        self.subtype = db_cfg.get("subtype", 1) # 1 = Substream por padrão (leve)

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

            while self._running:
                # Se a URL mudar em tempo de execução, reconecta
                if url != self.rtsp_url:
                    break

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

                # Renderiza anotações de tracking no frame se houver detecções ativas (TTL de 0.8s)
                with self._lock:
                    self.latest_frame_raw = raw_frame
                    tracks = [t for t in self._active_tracks if (now - t.get("last_seen", 0)) < 0.8]
                    self._active_tracks = tracks

                if tracks:
                    for t in tracks:
                        x1, y1, x2, y2 = t["box"]
                        track_id = t.get("track_id", 1)
                        conf = t.get("conf", 0.0)

                        # Garante limites válidos dentro do frame
                        x1, y1 = max(0, int(x1)), max(0, int(y1))
                        x2, y2 = min(w - 1, int(x2)), min(h - 1, int(y2))

                        # Bounding box estilo segurança HUD (Ciano Neon BGR: 255, 200, 0)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 200, 0), 2)

                        # Label com fundo preenchido e ID da pessoa
                        label = f"PESSOA #{track_id} ({int(conf * 100)}%)"
                        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                        label_y = max(y1, th + 6)
                        cv2.rectangle(frame, (x1, label_y - th - 6), (x1 + tw + 8, label_y + baseline), (255, 200, 0), -1)
                        cv2.putText(frame, label, (x1 + 4, label_y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (10, 15, 20), 1, cv2.LINE_AA)

                # Codifica para JPEG mantendo alta fidelidade visual (qualidade 85)
                # Otimizado para CPU com compressão direta
                _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                
                with self._lock:
                    self.latest_frame_jpeg = jpeg.tobytes()

                frame_count += 1
                if now - fps_timer >= 1.0:
                    self.fps = round(frame_count / (now - fps_timer), 1)
                    frame_count = 0
                    fps_timer = now

            cap.release()
            self.status = "OFFLINE"
            time.sleep(1)

    def get_latest_jpeg(self) -> bytes:
        with self._lock:
            if self.latest_frame_jpeg is not None and (time.time() - self.last_frame_time) < 3.0:
                return self.latest_frame_jpeg
            return self._placeholder_jpeg

    def _set_camera_lighting_rpc(self, on: bool = True, intensity: int = 100, timeout: float = 5.0) -> bool:
        """
        Controla o LED de Luz Branca (Full Color / Flash) da Intelbras VIP 1230 FC+
        utilizando a API nativa JSON-RPC 2.0 (/RPC2_Login e /RPC2) com autenticação Digest duplo-MD5.
        """
        try:
            s = requests.Session()
            # 1. Challenge inicial
            r1 = s.post(f"http://{self.ip}:{self.http_port}/RPC2_Login", json={
                "method": "global.login",
                "params": {"userName": self.user, "password": "", "clientType": "Web3.0", "loginType": "Direct"},
                "id": 1,
                "session": 0
            }, timeout=timeout)
            d1 = r1.json()
            sid = d1.get("session")
            params = d1.get("params", {})
            realm = params.get("realm")
            random_nonce = params.get("random")

            if not realm or not random_nonce:
                logger.error("Falha ao obter challenge RPC da câmera.")
                return False

            # 2. Resposta com duplo MD5
            inner = hashlib.md5(f"{self.user}:{realm}:{self.password}".encode("utf-8")).hexdigest().upper()
            final_pwd = hashlib.md5(f"{self.user}:{random_nonce}:{inner}".encode("utf-8")).hexdigest().upper()

            r2 = s.post(f"http://{self.ip}:{self.http_port}/RPC2_Login", json={
                "method": "global.login",
                "params": {
                    "userName": self.user,
                    "password": final_pwd,
                    "realm": realm,
                    "random": random_nonce,
                    "clientType": "Web3.0",
                    "loginType": "Direct"
                },
                "id": 2,
                "session": sid
            }, timeout=timeout)

            if not r2.json().get("result"):
                logger.error("Autenticação RPC2 falhou com as credenciais da câmera.")
                return False

            def _rpc(method, rpc_params=None):
                return s.post(
                    f"http://{self.ip}:{self.http_port}/RPC2",
                    json={"method": method, "params": rpc_params or {}, "id": 10, "session": sid},
                    timeout=timeout
                ).json()

            mode = "WhiteMode" if on else "InfraredMode"
            color_mode = "Color" if on else "Night"

            # 1. LightingScheme
            scheme_cfg = _rpc("configManager.getConfig", {"name": "LightingScheme"})
            if scheme_cfg.get("result"):
                scheme = scheme_cfg["params"]["table"]
                for p in range(len(scheme[0])):
                    scheme[0][p]["LightingMode"] = mode
                _rpc("configManager.setConfig", {"name": "LightingScheme", "table": scheme})

            # 2. VideoInDayNight
            daynight_cfg = _rpc("configManager.getConfig", {"name": "VideoInDayNight"})
            if daynight_cfg.get("result"):
                daynight = daynight_cfg["params"]["table"]
                for p in range(len(daynight[0])):
                    daynight[0][p]["Mode"] = color_mode
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
                        # Mantém a luz vermelha (IR) 100% acesa durante o modo de espera noturno
                        lighting_v2[0][p][0]["Mode"] = "Manual"
                        lighting_v2[0][p][0]["PercentOfMaxBrightness"] = 100
                        lighting_v2[0][p][0]["MiddleLight"][0]["Light"] = 100
                        lighting_v2[0][p][0]["MiddleLight"][0]["Angle"] = 50
                _rpc("configManager.setConfig", {"name": "Lighting_V2", "table": lighting_v2})

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
        Grava um clipe de vídeo MP4 com áudio sincronizado a partir do RTSP.
        Prioriza sempre o Stream Principal (1080p nativo) para gerar evidências de máxima definição,
        independentemente de o monitor ao vivo estar operando no Substream leve.
        Usa -c:v copy (zero overhead de CPU, qualidade 1080p original do sensor) e -c:a aac.
        Os fluxos de áudio e vídeo compartilham a sincronização de tempo de hardware da câmera.
        """
        import subprocess
        import shutil
        timestamp = get_now_str("%Y%m%d_%H%M%S")
        filename = f"video_{timestamp}.mp4"
        rec_dir = "backend/recordings" if os.path.exists("backend/recordings") else "recordings"
        os.makedirs(rec_dir, exist_ok=True)
        filepath = os.path.join(rec_dir, filename)

        rtsp_target = self.mainstream_rtsp_url
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        cmd = [
            ffmpeg_bin,
            "-nostats",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", rtsp_target,
            "-t", str(seconds),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "64k",
            "-movflags", "+faststart",
            "-y",
            filepath
        ]

        def _worker():
            try:
                logger.info(f"Iniciando gravação de vídeo MP4 1080p sincronizado ({seconds}s): {filename}")
                try:
                    subprocess.run(cmd, timeout=seconds + 10, check=True)
                except subprocess.CalledProcessError:
                    logger.warning("[!] Falha no stream 1080p para gravação, usando fallback para stream ativo...")
                    fallback_cmd = list(cmd)
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
    def scan_network_cameras(base_subnet: str = None) -> list:
        """Varre a sub-rede local nas portas RTSP (554), HTTP (80) e Dahua/Intelbras (37777)."""
        if not base_subnet:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                parts = local_ip.split(".")
                base_subnet = f"{parts[0]}.{parts[1]}.{parts[2]}"
            except Exception:
                base_subnet = "192.168.1"

        def _check_ip(ip):
            ports = [37777, 80, 554]
            found = []
            for port in ports:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.3)
                try:
                    if s.connect_ex((ip, port)) == 0:
                        found.append(port)
                except Exception:
                    pass
                finally:
                    s.close()
            if found:
                name = "Intelbras / Dahua" if (37777 in found or 554 in found) else "Dispositivo IP"
                return {"ip": ip, "ports": found, "name": name}
            return None

        ips = [f"{base_subnet}.{i}" for i in range(1, 255)]
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(_check_ip, ip) for ip in ips]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res:
                    results.append(res)
        return sorted(results, key=lambda x: [int(p) for p in x["ip"].split(".")])

camera_service = CameraService()
