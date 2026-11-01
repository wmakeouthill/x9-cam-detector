import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "X9 Cam Detector"
    JWT_SECRET: str = os.getenv("JWT_SECRET", "x9-cam-detector-super-secret-key-2026")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 60 * 24 # 24 horas

    # Usuário padrão do sistema
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123")

    # Configuração da Câmera Intelbras VIP 1230 FC+
    CAMERA_IP: str = os.getenv("CAMERA_IP", "192.168.1.108")
    CAMERA_PORT: int = int(os.getenv("CAMERA_PORT", "554"))
    CAMERA_HTTP_PORT: int = int(os.getenv("CAMERA_HTTP_PORT", "80"))
    CAMERA_USER: str = os.getenv("CAMERA_USER", "admin")
    CAMERA_PASSWORD: str = os.getenv("CAMERA_PASSWORD", "admin123")
    CAMERA_SUBTYPE: int = int(os.getenv("CAMERA_SUBTYPE", "0")) # 0 = Principal (1080p), 1 = Substream

    # Backup em Nuvem: Google Drive
    GOOGLE_DRIVE_ENABLED: bool = os.getenv("GOOGLE_DRIVE_ENABLED", "false").lower() in ("true", "1", "yes")
    GOOGLE_DRIVE_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE: str = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE", "service_account.json")

    # Notificações WhatsApp (Evolution API v2)
    EVOLUTION_API_URL: str = os.getenv("EVOLUTION_API_URL", "")
    EVOLUTION_API_KEY: str = os.getenv("EVOLUTION_API_KEY", "")
    EVOLUTION_INSTANCE_NAME: str = os.getenv("EVOLUTION_INSTANCE_NAME", "")
    EVOLUTION_TARGET_NUMBERS: str = os.getenv("EVOLUTION_TARGET_NUMBERS", "")

    @property
    def RTSP_URL(self) -> str:
        return f"rtsp://{self.CAMERA_USER}:{self.CAMERA_PASSWORD}@{self.CAMERA_IP}:{self.CAMERA_PORT}/cam/realmonitor?channel=1&subtype={self.CAMERA_SUBTYPE}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
