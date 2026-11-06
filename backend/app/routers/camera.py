import os
import time
import threading
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from app.core.security import get_current_user
from app.services.camera_service import camera_service
from app.services.detector_service import detector_service
from app.services.gdrive_service import gdrive_service
from app.services.dvr_service import dvr_service
from app.services.evolution_service import evolution_service
from app.core.config import settings
from app.db import (
    get_camera_config,
    get_night_watch_config,
    save_night_watch_config,
    list_detection_events,
    log_detection_event,
    count_detection_events,
    delete_detection_event,
    delete_detection_events_batch,
    get_whatsapp_config,
    save_whatsapp_config,
    list_dvr_segments,
    count_dvr_segments,
    list_dvr_available_dates,
    find_dvr_segment_for_seek,
    delete_dvr_segment,
)

router = APIRouter(prefix="/api/camera", tags=["camera"])

class FlashRequest(BaseModel):
    seconds: float = 2.0

class SnapshotRequest(BaseModel):
    with_flash: bool = False

class RecordRequest(BaseModel):
    seconds: int = 10

class DeleteBatchRequest(BaseModel):
    ids: list[int]

class CameraConfigRequest(BaseModel):
    ip: str
    port: int = 554
    http_port: int = 80
    user: str = "admin"
    password: str = "" # Vazio para manter a senha já salva
    subtype: int = 1 # 1 = Substream (leve), 0 = Principal

class NightWatchRequest(BaseModel):
    enabled: bool = True
    start_time: str = "00:00"
    end_time: str = "06:00"
    flash_duration: float = 2.5
    confidence_threshold: float = 0.45
    model_type: str = "yolov8s"
    retention_days: int = 15

@router.get("/status")
def get_camera_status(user: str = Depends(get_current_user)):
    return {
        "status": camera_service.status,
        "fps": camera_service.fps,
        "resolution": f"{camera_service.width}x{camera_service.height}" if camera_service.width else "N/A",
        "ip": camera_service.ip,
        "port": camera_service.port,
        "http_port": camera_service.http_port,
        "subtype": camera_service.subtype,
        "user": camera_service.user,
        "detector_active": detector_service.is_active,
        "person_detected": detector_service.last_person_detected,
        "people_count": detector_service.people_count,
        "last_confidence": round(detector_service.last_confidence * 100, 1),
        "detections_count": detector_service.detections_count,
        "test_mode": detector_service.test_mode,
        "model_type": detector_service.current_model_type,
    }

class TestModeRequest(BaseModel):
    enabled: bool

@router.post("/test-mode")
def toggle_test_mode(req: TestModeRequest, user: str = Depends(get_current_user)):
    detector_service.test_mode = req.enabled
    return {"status": "ok", "test_mode": detector_service.test_mode}

@router.post("/flash")
def trigger_flash(req: FlashRequest, user: str = Depends(get_current_user)):
    return camera_service.trigger_flash(seconds=req.seconds)

@router.post("/reboot")
def reboot_camera(user: str = Depends(get_current_user)):
    res = camera_service.reboot_camera()
    if res.get("status") == "error":
        raise HTTPException(status_code=502, detail=res.get("message", "Falha ao reiniciar câmera."))
    return res

@router.post("/snapshot")
def take_snapshot(req: SnapshotRequest = SnapshotRequest(), user: str = Depends(get_current_user)):
    try:
        if req.with_flash:
            # Captura dupla: Foto 1 Flagrante Instantâneo (P&B/IR no milissegundo 0)
            snapshot_pb = camera_service.take_snapshot(prefix="snapshot_pb")
            log_detection_event(confidence=1.0, flash_triggered=False, snapshot_path=snapshot_pb)
            gdrive_service.upload_file_async(snapshot_pb, description="Snapshot manual [Flagrante P&B]")

            # Aciona Flash de Luz Branca (3.5s) e aguarda 1.8s para transição óptica completa (6_flash_wdr_off)
            camera_service.trigger_flash(seconds=3.5)
            time.sleep(1.8)

            # Foto 2: Foto com Flash 1080p colorida com ambiente iluminado
            snapshot_flash = camera_service.take_snapshot(prefix="snapshot_flash")
            log_detection_event(confidence=1.0, flash_triggered=True, snapshot_path=snapshot_flash)
            gdrive_service.upload_file_async(snapshot_flash, description="Snapshot manual [Flash Colorido]")

            return {
                "status": "ok",
                "filename": snapshot_flash,
                "url": f"/api/camera/snapshots/{snapshot_flash}",
                "snapshot_pb": snapshot_pb,
                "snapshot_flash": snapshot_flash,
            }
        else:
            filename = camera_service.take_snapshot()
            # Registra evento de captura no banco
            log_detection_event(confidence=1.0, flash_triggered=False, snapshot_path=filename)
            # Envia cópia para o Google Drive em background
            gdrive_service.upload_file_async(filename, description="Snapshot manual capturado pelo usuário")
            return {"status": "ok", "filename": filename, "url": f"/api/camera/snapshots/{filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/record")
def record_clip(req: RecordRequest = RecordRequest(), user: str = Depends(get_current_user)):
    try:
        def _on_done(filepath, filename):
            log_detection_event(confidence=1.0, flash_triggered=False, snapshot_path=None, video_path=filename)
            gdrive_service.upload_file_async(filename, description="Gravação manual capturada pelo usuário")

        filename = camera_service.record_clip(seconds=req.seconds, on_complete=_on_done)
        # Pré-registra na fila SQLite para resistir a quedas do processo durante a gravação
        gdrive_service.enqueue_file(filename, description="Gravação manual capturada pelo usuário", file_type="recording")
        return {"status": "recording_started", "filename": filename, "duration": req.seconds}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/snapshots/{filename}")
def get_snapshot(filename: str, token: str | None = Query(None)):
    filepath = os.path.join("snapshots", filename)
    if not os.path.exists(filepath):
        filepath = os.path.join("backend", "snapshots", filename)
    if not os.path.exists(filepath):
        filepath = os.path.join("..", "snapshots", filename)

    if os.path.exists(filepath):
        return FileResponse(filepath, media_type="image/jpeg")

    # Fonte de verdade: se não está local, busca diretamente no Google Drive
    if gdrive_service.is_configured:
        data = gdrive_service.get_file_bytes(filename)
        if data:
            return Response(content=data, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="Snapshot não encontrado")

@router.get("/recordings/{filename}")
def get_recording(filename: str, user: str = Depends(get_current_user)):
    filepath = os.path.join("recordings", filename)
    if not os.path.exists(filepath):
        filepath = os.path.join("backend", "recordings", filename)
    if not os.path.exists(filepath):
        filepath = os.path.join("..", "recordings", filename)

    if os.path.exists(filepath):
        return FileResponse(filepath, media_type="video/mp4")

    # Fonte de verdade: se não está local, busca diretamente no Google Drive
    if gdrive_service.is_configured:
        data = gdrive_service.get_file_bytes(filename)
        if data:
            return Response(content=data, media_type="video/mp4")

    raise HTTPException(status_code=404, detail="Gravação de vídeo não encontrada")

@router.get("/nightwatch")
def get_nightwatch_settings(user: str = Depends(get_current_user)):
    return get_night_watch_config()

@router.post("/nightwatch")
def update_nightwatch_settings(req: NightWatchRequest, user: str = Depends(get_current_user)):
    save_night_watch_config(
        enabled=req.enabled,
        start_time=req.start_time,
        end_time=req.end_time,
        flash_duration=req.flash_duration,
        confidence_threshold=req.confidence_threshold,
        model_type=req.model_type,
        retention_days=req.retention_days,
    )
    return {"status": "updated", "config": req.dict()}

@router.get("/events")
def get_events(limit: int = 12, offset: int = 0, user: str = Depends(get_current_user)):
    # Google Drive como Fonte de Verdade: reconcilia eventos do Drive com a galeria
    if gdrive_service.is_configured:
        gdrive_service.reconcile_events_from_drive()

    items = list_detection_events(limit=limit, offset=offset)
    total = count_detection_events()
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }

@router.delete("/events/{event_id}")
def delete_event(event_id: int, user: str = Depends(get_current_user)):
    success = delete_detection_event(event_id)
    if not success:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    return {"status": "ok", "deleted_id": event_id}

@router.post("/events/delete-batch")
def delete_events_batch(req: DeleteBatchRequest, user: str = Depends(get_current_user)):
    deleted = delete_detection_events_batch(req.ids)
    return {"status": "ok", "deleted_count": deleted}

@router.get("/scan")
def scan_network_cameras(
    subnets: str | None = Query(None, description="Sub-redes separadas por vírgula (ex: 192.168.1, 192.168.2)"),
    subnet: str | None = Query(None, description="Sub-rede única (legado)"),
    user: str = Depends(get_current_user)
):
    target = subnets if subnets is not None else subnet
    result = camera_service.scan_network_cameras(target)
    if isinstance(result, dict):
        return result
    return {"cameras": result, "scanned_subnets": []}

@router.get("/gdrive/status")
def get_gdrive_status(user: str = Depends(get_current_user)):
    from app.db import get_pending_sync_files
    has_token = os.path.exists("token.json") or os.path.exists(os.path.join("..", "token.json"))
    creds_file = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE
    creds_found = os.path.exists(creds_file) or os.path.exists(os.path.join("..", creds_file))
    pending_list = get_pending_sync_files(limit=100)
    return {
        "enabled": settings.GOOGLE_DRIVE_ENABLED,
        "is_configured": gdrive_service.is_configured,
        "has_oauth": has_token,
        "service_account_file": creds_file,
        "credentials_found": has_token or creds_found,
        "folder_id_set": bool(settings.GOOGLE_DRIVE_FOLDER_ID),
        "pending_sync_count": len(pending_list),
    }

@router.post("/gdrive/test")
def test_gdrive_connection(user: str = Depends(get_current_user)):
    return gdrive_service.test_connection()

@router.post("/gdrive/sync")
def sync_gdrive_now(user: str = Depends(get_current_user)):
    return gdrive_service.sync_now()

@router.post("/config")
def update_config(req: CameraConfigRequest, user: str = Depends(get_current_user)):
    pwd = req.password.strip() if req.password else ""
    if not pwd:
        existing = get_camera_config()
        pwd = existing.get("password") or settings.CAMERA_PASSWORD

    camera_service.update_config(
        ip=req.ip,
        user=req.user,
        password=pwd,
        port=req.port,
        http_port=req.http_port,
        subtype=req.subtype,
    )
    res_data = req.dict()
    res_data["password"] = "******"
    return {"status": "updated", "camera": res_data}

class SubtypeRequest(BaseModel):
    subtype: int # 0 = Principal (1080p), 1 = Substream (480p)

@router.post("/subtype")
def change_stream_subtype(req: SubtypeRequest, user: str = Depends(get_current_user)):
    if req.subtype not in (0, 1):
        raise HTTPException(status_code=400, detail="Subtype inválido. Use 0 (1080p) ou 1 (Substream).")
    existing = get_camera_config()
    pwd = existing.get("password") or settings.CAMERA_PASSWORD
    ip = camera_service.ip or existing.get("ip") or settings.CAMERA_IP
    user_name = camera_service.user or existing.get("user") or settings.CAMERA_USER
    port = camera_service.port or existing.get("port") or settings.CAMERA_PORT
    http_port = camera_service.http_port or existing.get("http_port") or settings.CAMERA_HTTP_PORT

    camera_service.update_config(
        ip=ip,
        user=user_name,
        password=pwd,
        port=port,
        http_port=http_port,
        subtype=req.subtype,
    )
    return {
        "status": "updated",
        "subtype": req.subtype,
        "name": "1080p FHD" if req.subtype == 0 else "480p Substream",
    }

class WhatsAppConfigRequest(BaseModel):
    enabled: bool = False
    api_url: str = ""
    api_key: str = ""
    instance_name: str = ""
    target_number: str = ""
    send_photo: bool = True
    send_video: bool = True

class WhatsAppTestRequest(BaseModel):
    api_url: str = ""
    api_key: str = ""
    instance_name: str = ""
    target_number: str = ""

class WhatsAppConnectRequest(BaseModel):
    api_url: str = ""
    api_key: str = ""
    instance_name: str = ""

class WhatsAppResetRequest(BaseModel):
    api_url: str = ""
    api_key: str = ""
    instance_name: str = ""

@router.get("/whatsapp")
def get_whatsapp_settings(user: str = Depends(get_current_user)):
    cfg = get_whatsapp_config(decrypt_key=False)
    # Valores default inteligentes para facilitar a vida do usuário
    if not cfg["api_url"]:
        cfg["api_url"] = settings.EVOLUTION_API_URL or "http://evolution-api:8080"
    if not cfg["instance_name"]:
        cfg["instance_name"] = settings.EVOLUTION_INSTANCE_NAME or "x9-vigia"
    if not cfg["has_key"] and settings.EVOLUTION_API_KEY:
        cfg["has_key"] = True
        cfg["api_key"] = "••••••••"

    conn_info = {"connected": False, "state": "unconfigured", "message": "WhatsApp não configurado"}
    if cfg["api_url"] and cfg["instance_name"] and cfg["has_key"]:
        real_key = get_whatsapp_config(decrypt_key=True).get("api_key", "") or settings.EVOLUTION_API_KEY or "x9-evolution-key-2026"
        conn_info = evolution_service.test_connection(cfg["api_url"], real_key, cfg["instance_name"])
    return {
        **cfg,
        "connection": conn_info,
    }

@router.post("/whatsapp")
def update_whatsapp_settings(req: WhatsAppConfigRequest, user: str = Depends(get_current_user)):
    save_whatsapp_config(
        enabled=req.enabled,
        api_url=req.api_url,
        instance_name=req.instance_name,
        target_number=req.target_number,
        send_photo=req.send_photo,
        send_video=req.send_video,
        api_key=req.api_key,
    )
    return {"status": "ok", "message": "Configurações do WhatsApp salvas com sucesso."}

@router.post("/whatsapp/connect")
def connect_whatsapp_instance(req: WhatsAppConnectRequest, user: str = Depends(get_current_user)):
    current = get_whatsapp_config(decrypt_key=True)
    api_url = (req.api_url or current.get("api_url") or settings.EVOLUTION_API_URL or "http://evolution-api:8080").strip()
    instance = (req.instance_name or current.get("instance_name") or settings.EVOLUTION_INSTANCE_NAME or "x9-vigia").strip()
    key = req.api_key.strip() if (req.api_key and req.api_key.strip() != "••••••••") else current.get("api_key", "")
    if not key:
        key = settings.EVOLUTION_API_KEY or "x9-evolution-key-2026"

    res = evolution_service.connect_or_get_qrcode(api_url, key, instance)
    return res

@router.post("/whatsapp/reset")
def reset_whatsapp_instance(req: WhatsAppResetRequest, user: str = Depends(get_current_user)):
    current = get_whatsapp_config(decrypt_key=True)
    api_url = (req.api_url or current.get("api_url") or settings.EVOLUTION_API_URL or "http://evolution-api:8080").strip()
    instance = (req.instance_name or current.get("instance_name") or settings.EVOLUTION_INSTANCE_NAME or "x9-vigia").strip()
    key = req.api_key.strip() if (req.api_key and req.api_key.strip() != "••••••••") else current.get("api_key", "")
    if not key:
        key = settings.EVOLUTION_API_KEY or "x9-evolution-key-2026"

    res = evolution_service.reset_instance(api_url, key, instance)
    return res

@router.post("/whatsapp/test")
def test_whatsapp_notification(req: WhatsAppTestRequest, user: str = Depends(get_current_user)):
    current = get_whatsapp_config(decrypt_key=True)
    api_url = (req.api_url or current["api_url"]).strip()
    instance = (req.instance_name or current["instance_name"]).strip()
    target = (req.target_number or current["target_number"]).strip()
    key = req.api_key.strip() if (req.api_key and req.api_key.strip() != "••••••••") else current["api_key"]

    if not api_url or not instance or not target or not key:
        raise HTTPException(status_code=400, detail="Preencha URL, Instância, API Key e Número antes de testar.")

    conn_res = evolution_service.test_connection(api_url, key, instance)
    if not conn_res.get("connected"):
        return {
            "success": False,
            "message": f"Falha na conexão com Evolution API: {conn_res.get('message')}",
            "connection": conn_res
        }

    send_res = evolution_service.send_test_message(api_url, key, instance, target)
    return {
        "success": send_res.get("success", False),
        "message": send_res.get("message"),
        "connection": conn_res
    }

# ==============================================================================
# Endpoints de Gravação Contínua 24h (DVR 15 dias + Seek / Replay Instantâneo)
# ==============================================================================

class DvrToggleRequest(BaseModel):
    enabled: bool

@router.get("/dvr/status")
def get_dvr_status(user: str = Depends(get_current_user)):
    return dvr_service.get_status()

@router.post("/dvr/toggle")
def toggle_dvr_recording(req: DvrToggleRequest, user: str = Depends(get_current_user)):
    st = dvr_service.set_enabled(req.enabled)
    return {
        "status": "ok",
        "enabled": st["enabled"],
        "recording": st["recording"],
    }

@router.get("/dvr/segments")
def get_dvr_segments(
    date: str | None = Query(None, description="Filtro de data YYYY-MM-DD"),
    hour: str | None = Query(None, description="Filtro de hora HH (00-23)"),
    limit: int = 100,
    offset: int = 0,
    user: str = Depends(get_current_user),
):
    if gdrive_service.is_configured:
        threading.Thread(target=gdrive_service.reconcile_dvr_from_drive, daemon=True).start()

    items = list_dvr_segments(date_str=date, hour_str=hour, limit=limit, offset=offset)
    total = count_dvr_segments(date_str=date, hour_str=hour)
    available_dates = list_dvr_available_dates()
    return {
        "items": items,
        "total": total,
        "available_dates": available_dates,
    }

@router.get("/dvr/seek")
def seek_dvr_recording(
    seconds_ago: int = Query(30, ge=1),
    target_time: str | None = Query(None),
    user: str = Depends(get_current_user),
):
    res = find_dvr_segment_for_seek(seconds_ago=seconds_ago, target_time_str=target_time)
    if not res:
        return {
            "found": False,
            "message": "Nenhuma gravação 24h disponível ainda.",
        }
    seg = res["segment"]
    return {
        "found": True,
        "segment": seg,
        "seek_offset": res["seek_offset"],
        "url": f"/api/camera/dvr/video/{seg['filename']}",
    }

def _serve_video_with_range(request: Request, filepath: str | None = None, raw_bytes: bytes | None = None) -> Response:
    """
    Serve vídeo MP4 com suporte a HTTP Range (206 Partial Content) otimizado para mobile:
    - Limita respostas de Range aberto (ex: 'bytes=0-') a chunks de 1 MB para início imediato do play.
    - Evita carregar dezenas de MB na RAM de uma só vez.
    """
    MAX_RANGE_CHUNK = 1024 * 1024  # 1 MB por janela para streaming mobile instantâneo
    range_header = request.headers.get("range")
    if filepath and os.path.exists(filepath):
        file_size = os.path.getsize(filepath)
        if not range_header or file_size == 0:
            return FileResponse(
                filepath,
                media_type="video/mp4",
                headers={
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "private, max-age=3600",
                },
            )
        try:
            range_val = range_header.strip().lower().replace("bytes=", "")
            start_str, end_str = (range_val.split("-", 1) + [""])[:2]
            start = int(start_str) if start_str else 0
            if end_str:
                end = int(end_str)
            else:
                end = min(file_size - 1, start + MAX_RANGE_CHUNK - 1)
            start = max(0, min(start, file_size - 1))
            end = max(start, min(end, file_size - 1))
            length = end - start + 1
            with open(filepath, "rb") as f:
                f.seek(start)
                data = f.read(length)
            return Response(
                content=data,
                status_code=206,
                media_type="video/mp4",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(data)),
                    "Cache-Control": "private, max-age=3600",
                },
            )
        except Exception:
            return FileResponse(filepath, media_type="video/mp4")

    if raw_bytes is not None:
        total_size = len(raw_bytes)
        if range_header and total_size > 0:
            try:
                range_val = range_header.strip().lower().replace("bytes=", "")
                start_str, end_str = (range_val.split("-", 1) + [""])[:2]
                start = int(start_str) if start_str else 0
                if end_str:
                    end = int(end_str)
                else:
                    end = min(total_size - 1, start + MAX_RANGE_CHUNK - 1)
                start = max(0, min(start, total_size - 1))
                end = max(start, min(end, total_size - 1))
                chunk = raw_bytes[start : end + 1]
                return Response(
                    content=chunk,
                    status_code=206,
                    media_type="video/mp4",
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{total_size}",
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(len(chunk)),
                        "Cache-Control": "private, max-age=3600",
                    },
                )
            except Exception:
                pass
        return Response(
            content=raw_bytes,
            media_type="video/mp4",
            headers={"Accept-Ranges": "bytes"},
        )

    raise HTTPException(status_code=404, detail="Segmento DVR não encontrado")

@router.get("/dvr/video/{filename}")
def get_dvr_video(filename: str, request: Request, user: str = Depends(get_current_user)):
    safe_name = os.path.basename(filename)
    search_dirs = [
        dvr_service.recordings_dir,
        "/tmp/x9_dvr_buffer",
        "recordings_24h",
        os.path.join("backend", "recordings_24h"),
        os.path.join("..", "recordings_24h"),
    ]
    for folder in search_dirs:
        if not folder:
            continue
        candidate = os.path.join(folder, safe_name)
        if os.path.exists(candidate) and os.path.getsize(candidate) >= 16384:
            return _serve_video_with_range(request, filepath=candidate)

    if gdrive_service.is_configured:
        data = gdrive_service.get_dvr_file_bytes(safe_name)
        if data:
            # Salva em cache no buffer em RAM (dvr_service.recordings_dir) para que
            # as requisições Range subsequentes sejam instantâneas sem tocar no disco D:
            cache_dir = dvr_service.recordings_dir
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, safe_name)
            try:
                with open(cache_path, "wb") as f:
                    f.write(data)
                dvr_service._prune_ram_buffer()
                return _serve_video_with_range(request, filepath=cache_path)
            except Exception:
                return _serve_video_with_range(request, raw_bytes=data)

    raise HTTPException(status_code=404, detail="Gravação 24h não encontrada")

@router.delete("/dvr/segments/{segment_id}")
def remove_dvr_segment(segment_id: int, user: str = Depends(get_current_user)):
    ok = delete_dvr_segment(segment_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Segmento DVR não encontrado")
    return {"status": "ok", "deleted_id": segment_id}

