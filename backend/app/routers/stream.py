import asyncio
import subprocess
import shutil
import logging
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.core.security import get_current_user
from app.services.camera_service import camera_service

logger = logging.getLogger("x9.stream")

router = APIRouter(prefix="/api/stream", tags=["stream"])

# Cadeia de tratamento de áudio CFTV de longo alcance e alta clareza vocal:
# - aresample=async=1000: corrige jitter de pacotes RTSP sem cortes
# - highpass=f=180,lowpass=f=3800: isola a faixa da fala humana, removendo vento/grave (<180Hz) e chiado agudo (>3800Hz)
# - afftdn=nr=18:nf=-30:tn=1: denoiser FFT com rastreamento contínuo de ruído (tn=1) para apagar o chiado de fundo sem abafar a voz
# - equalizer=f=2400:t=q:w=1.2:g=5: realça consoantes da fala humana (+5dB em 2.4kHz) para inteligibilidade à distância
# - acompressor=threshold=-28dB:ratio=3.5:attack=15:release=250:makeup=4: amplifica vozes distantes em até ~3.5x sem subir o ruído de fundo
# - alimiter=limit=0.88: protege contra estalos/clipping quando alguém fala próximo à câmera
CLEAN_AUDIO_FILTER = (
    "aresample=async=1000:min_hard_comp=0.100000:first_pts=0,"
    "highpass=f=180,lowpass=f=3800,"
    "afftdn=nr=18:nf=-30:tn=1,"
    "equalizer=f=2400:t=q:w=1.2:g=5,"
    "acompressor=threshold=-28dB:ratio=3.5:attack=15:release=250:makeup=4,"
    "alimiter=limit=0.88"
)

async def frame_generator():
    """
    Gera o multipart MJPEG para visualização contínua com latência mínima no navegador.
    """
    while True:
        frame_bytes = camera_service.get_latest_jpeg()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
        )
        # ~25 fps de taxa de atualização
        await asyncio.sleep(0.04)

@router.get("/live")
def live_stream(user: str = Depends(get_current_user)):
    """
    Endpoint de streaming ao vivo protegido por JWT.
    Compatível com tag <img src="/api/stream/live?token=...">
    """
    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
        }
    )

def resolve_ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    common_paths = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    return "ffmpeg"

@router.get("/audio")
def live_audio_stream(user: str = Depends(get_current_user)):
    """
    Endpoint de streaming de áudio ao vivo do microfone embutido da Intelbras VIP 1230 FC+.
    Extrai o áudio do RTSP e converte para MP3 em tempo real para reprodução nativa no navegador.
    """
    ffmpeg_bin = resolve_ffmpeg_bin()
    if not shutil.which(ffmpeg_bin) and not os.path.exists(ffmpeg_bin):
        logger.error(f"Binário do FFmpeg não encontrado no sistema: {ffmpeg_bin}")
        raise HTTPException(status_code=500, detail="FFmpeg não instalado no ambiente do servidor.")

    rtsp_url = camera_service.rtsp_url

    # Comando otimizado para áudio contínuo e estável com sincronização de timestamps:
    # -aresample=async=1:first_pts=0: corrige jitter de pacotes RTSP da Intelbras sem erro de DTS
    # -af volume=2.5: amplifica o som do microfone embutido de CFTV da câmera
    # -write_xing 0 e -id3v2_version 0: remove cabeçalhos que requerem seek em pipes
    # -flush_packets 1: envia pacotes de áudio continuamente no stdout
    cmd = [
        ffmpeg_bin,
        "-nostats",
        "-loglevel", "warning",
        "-rtsp_transport", "tcp",
        "-probesize", "32",
        "-analyzeduration", "0",
        "-i", rtsp_url,
        "-vn",
        "-af", CLEAN_AUDIO_FILTER,
        "-acodec", "libmp3lame",
        "-write_xing", "0",
        "-id3v2_version", "0",
        "-b:a", "64k",
        "-ar", "16000",
        "-ac", "1",
        "-flush_packets", "1",
        "-f", "mp3",
        "pipe:1"
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except Exception as e:
        logger.error(f"Erro ao iniciar FFmpeg de áudio: {e}")
        raise HTTPException(status_code=500, detail=f"Falha ao iniciar áudio: {e}")

    def audio_generator():
        try:
            while True:
                chunk = proc.stdout.read(2048)
                if not chunk:
                    break
                yield chunk
        except Exception:
            pass
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    return StreamingResponse(
        audio_generator(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
        }
    )

@router.get("/audio-raw")
def live_audio_raw_stream(user: str = Depends(get_current_user)):
    """
    Streaming de áudio PCM cru (s16le 16kHz mono) de latência ultrabaixa (<100ms).
    Alimenta diretamente a Web Audio API (AudioContext) do navegador sem passar
    pelo buffer obrigatório de 3 segundos do elemento <audio> HTML5.
    """
    ffmpeg_bin = resolve_ffmpeg_bin()
    if not shutil.which(ffmpeg_bin) and not os.path.exists(ffmpeg_bin):
        raise HTTPException(status_code=500, detail="FFmpeg não instalado no ambiente do servidor.")

    rtsp_url = camera_service.rtsp_url
    cmd = [
        ffmpeg_bin,
        "-nostats",
        "-loglevel", "warning",
        "-rtsp_transport", "tcp",
        "-probesize", "32",
        "-analyzeduration", "0",
        "-i", rtsp_url,
        "-vn",
        "-af", CLEAN_AUDIO_FILTER,
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "pipe:1"
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except Exception as e:
        logger.error(f"Erro ao iniciar FFmpeg PCM: {e}")
        raise HTTPException(status_code=500, detail=f"Falha ao iniciar áudio PCM: {e}")

    def raw_generator():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        except Exception:
            pass
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    return StreamingResponse(
        raw_generator(),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
        }
    )

