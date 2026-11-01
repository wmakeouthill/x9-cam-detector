import base64
import hashlib
from cryptography.fernet import Fernet
from app.core.config import settings

def _get_fernet() -> Fernet:
    # Deriva uma chave Fernet de 32 bytes a partir do JWT_SECRET
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.JWT_SECRET.encode()).digest())
    return Fernet(key)

def encrypt_credential(text: str) -> str:
    """Criptografa senha de forma reversível com AES-256 (Fernet)."""
    if not text:
        return ""
    return _get_fernet().encrypt(text.encode("utf-8")).decode("utf-8")

def decrypt_credential(cipher_text: str) -> str:
    """Descriptografa a credencial usando a chave do sistema."""
    if not cipher_text:
        return ""
    try:
        return _get_fernet().decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except Exception:
        # Se não conseguir descriptografar (ex: texto antigo não criptografado)
        return cipher_text
