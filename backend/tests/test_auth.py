import pytest
from app.core.security import hash_password, verify_password, create_access_token, decode_access_token

def test_password_hashing():
    pwd = "secret_password_123"
    hashed = hash_password(pwd)
    assert hashed.startswith("$argon2")
    assert verify_password(hashed, pwd) is True
    assert verify_password(hashed, "wrong_password") is False

def test_jwt_token():
    token = create_access_token("admin")
    decoded = decode_access_token(token)
    assert decoded["sub"] == "admin"
