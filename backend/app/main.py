from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, stream, camera
from app.services.camera_service import camera_service
from app.services.detector_service import detector_service
from app.services.gdrive_service import gdrive_service
from app.db import init_db
from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa banco de dados SQLite
    init_db()
    # Inicia captura RTSP em background
    camera_service.start()
    # Inicia detector YOLO em background
    detector_service.start()
    # Inicia sincronização e auto-cura Google Drive (recupera pendências pós-queda)
    gdrive_service.start()
    yield
    # Encerra graciosamente
    gdrive_service.stop()
    detector_service.stop()
    camera_service.stop()

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Plataforma de Monitoramento Intelbras VIP 1230 FC+ com Detecção Noturna e Flash",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS para frontend React (porta 5173 / localhost / IP da rede)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(stream.router)
app.include_router(camera.router)

@app.get("/api/health")
def health():
    return {"status": "ok", "system": settings.PROJECT_NAME}

# Servir frontend compilado (permite rodar sem Docker em processo único na porta 8000)
import os
from fastapi.staticfiles import StaticFiles

dist_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))
if os.path.exists(dist_dir):
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="frontend")
