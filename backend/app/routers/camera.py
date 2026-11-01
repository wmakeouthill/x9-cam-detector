import os
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.core.security import get_current_user
from app.services.camera_service import camera_service
from app.services.detector_service import detector_service
from app.services.gdrive_service import gdrive_service
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

@router.post("/snapshot")
def take_snapshot(req: SnapshotRequest = SnapshotRequest(), user: str = Depends(get_current_user)):
    try:
        if req.with_flash:
            # Captura dupla: Foto 1 Flagrante Instantâneo (P&B/IR no milissegundo 0)
            snapshot_pb = camera_service.take_snapshot(prefix="snapshot_pb")
            log_detection_event(confidence=1.0, flash_triggered=False, snapshot_path=snapshot_pb)
            gdrive_service.upload_file_async(snapshot_pb, description="Snapshot manual [Flagrante P&B]")

            # Aciona Flash de Luz Branca (3s)
            camera_service.trigger_flash(seconds=3.0)
            time.sleep(1.2)

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
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Snapshot não encontrado")
    return FileResponse(filepath, media_type="image/jpeg")

@router.get("/recordings/{filename}")
def get_recording(filename: str, user: str = Depends(get_current_user)):
    filepath = os.path.join("recordings", filename)
    if not os.path.exists(filepath):
        filepath = os.path.join("backend", "recordings", filename)
    if not os.path.exists(filepath):
        filepath = os.path.join("..", "recordings", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Gravação de vídeo não encontrada")
    return FileResponse(filepath, media_type="video/mp4")

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
def scan_network_cameras(subnet: str | None = None, user: str = Depends(get_current_user)):
    devices = camera_service.scan_network_cameras(subnet)
    return {"cameras": devices}

@router.get("/gdrive/status")
def get_gdrive_status(user: str = Depends(get_current_user)):
    has_token = os.path.exists("token.json") or os.path.exists(os.path.join("..", "token.json"))
    creds_file = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE
    creds_found = os.path.exists(creds_file) or os.path.exists(os.path.join("..", creds_file))
    return {
        "enabled": settings.GOOGLE_DRIVE_ENABLED,
        "is_configured": gdrive_service.is_configured,
        "has_oauth": has_token,
        "service_account_file": creds_file,
        "credentials_found": has_token or creds_found,
        "folder_id_set": bool(settings.GOOGLE_DRIVE_FOLDER_ID),
    }

@router.post("/gdrive/test")
def test_gdrive_connection(user: str = Depends(get_current_user)):
    return gdrive_service.test_connection()

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
