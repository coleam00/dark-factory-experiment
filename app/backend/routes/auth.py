"""
Auth routes — signup, login, logout, me.

Session is an httpOnly + Secure + SameSite=Lax cookie named `session` carrying
a JWT signed with `JWT_SECRET`. The same-origin prod deployment means no CORS
gymnastics are required (see CLAUDE.md "Deployment").
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field

from backend.auth.dependencies import COOKIE_NAME, get_current_user
from backend.auth.password import hash_password, verify_password
from backend.auth.tokens import encode_token
from backend.config import JWT_EXPIRY_SECONDS
from backend.db import users_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: str
    email: str


def _set_session_cookie(response: Response, user_id: str) -> None:
    """Mint a JWT and attach it to the response as an httpOnly session cookie."""
    token = encode_token(user_id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=JWT_EXPIRY_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _user_to_response(user: dict[str, Any]) -> UserResponse:
    return UserResponse(id=str(user["id"]), email=str(user["email"]))


@router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
async def signup(body: SignupRequest, response: Response) -> UserResponse:
    """Create a user, set session cookie, return {id, email}. 409 on duplicate email."""
    password_hash = hash_password(body.password)
    try:
        user = await users_repo.create_user(email=body.email, password_hash=password_hash)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    _set_session_cookie(response, str(user["id"]))
    return _user_to_response(user)


@router.post("/login", response_model=UserResponse)
async def login(body: LoginRequest, response: Response) -> UserResponse:
    """Verify credentials and rotate session cookie. 401 on any failure."""
    user = await users_repo.get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    await users_repo.update_last_login(user["id"])
    _set_session_cookie(response, str(user["id"]))
    return _user_to_response(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    """Clear the session cookie. Always 204 — idempotent."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(user: dict[str, Any] = Depends(get_current_user)) -> UserResponse:
    """Return the currently-authenticated user, or 401."""
    return _user_to_response(user)
