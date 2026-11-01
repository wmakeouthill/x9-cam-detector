from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from fastapi import Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

_ph = PasswordHasher()
security_bearer = HTTPBearer(auto_error=False)

def hash_password(plain_password: str) -> str:
    """Gera hash Argon2id seguro."""
    return _ph.hash(plain_password)

def verify_password(password_hash: str, plain_password: str) -> bool:
    """Valida a senha contra o hash Argon2id."""
    try:
        return _ph.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False

def create_access_token(subject: str) -> str:
    """Gera token JWT."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    payload = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def decode_access_token(token: str) -> dict:
    """Decodifica e valida o JWT."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_user(
    auth: HTTPAuthorizationCredentials = Depends(security_bearer),
    token_query: str | None = Query(None, alias="token"),
) -> str:
    """
    Autenticação unificada: aceita Header Authorization: Bearer <token>
    ou query param ?token=<token> (essencial para tags <img> e <video>).
    """
    token = None
    if auth and auth.credentials:
        token = auth.credentials
    elif token_query:
        token = token_query

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado. Forneça o token no cabeçalho ou parâmetro.",
        )

    payload = decode_access_token(token)
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    return username
