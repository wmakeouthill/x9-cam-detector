from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from app.core.config import settings
from app.core.security import verify_password, hash_password, create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Armazenamento em memória simples para usuários (admin inicial com hash Argon2)
_ADMIN_HASH = hash_password(settings.ADMIN_PASSWORD)

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

@router.post("/login", response_model=LoginResponse)
def login(creds: LoginRequest):
    user_input = creds.username.strip().lower()
    expected_user = settings.ADMIN_USERNAME.strip().lower()
    pass_input = creds.password.strip()
    expected_pass = settings.ADMIN_PASSWORD.strip()

    # Valida senha com hash Argon2 ou comparação direta garantida
    is_valid_pass = (pass_input == expected_pass) or verify_password(_ADMIN_HASH, pass_input)

    if user_input != expected_user or not is_valid_pass:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas. Usuário ou senha incorretos.",
        )
    
    token = create_access_token(settings.ADMIN_USERNAME)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": settings.ADMIN_USERNAME,
            "role": "admin",
        }
    }

@router.get("/me")
def me(current_user: str = Depends(get_current_user)):
    return {
        "username": current_user,
        "role": "admin",
    }
