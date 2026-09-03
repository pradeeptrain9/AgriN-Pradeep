"""Phone OTP authentication."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.schemas import OtpRequest, OtpVerify, TokenResponse
from app.security import (
    create_access_token,
    generate_otp,
    hash_otp,
    send_otp,
    verify_otp,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# An unauthenticated endpoint that sends SMS is a spend vector, so cap how often
# a single number can request one.
OTP_MIN_INTERVAL_SECONDS = 30


@router.post("/otp/request", status_code=status.HTTP_202_ACCEPTED)
async def request_otp(payload: OtpRequest, db: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    recent = await db.execute(
        text(
            "SELECT created_at FROM otp_codes WHERE phone = :phone "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"phone": payload.phone},
    )
    row = recent.first()
    if row and (now - row[0]).total_seconds() < OTP_MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="A code was just sent. Wait a moment before asking for another.",
        )

    code = generate_otp()
    await db.execute(
        text(
            "INSERT INTO otp_codes (phone, code_hash, expires_at) "
            "VALUES (:phone, :code_hash, :expires_at)"
        ),
        {
            "phone": payload.phone,
            "code_hash": hash_otp(code, payload.phone),
            "expires_at": now + timedelta(seconds=settings.otp_ttl_seconds),
        },
    )
    await db.commit()
    await send_otp(payload.phone, code)

    response = {"sent": True, "expires_in": settings.otp_ttl_seconds}
    if settings.agrin_env == "dev":
        # Convenience for local testing only; never returned outside dev.
        response["dev_code"] = code
    return response


@router.post("/otp/verify", response_model=TokenResponse)
async def verify(payload: OtpVerify, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    result = await db.execute(
        text(
            "SELECT id, code_hash, expires_at, consumed_at, attempts FROM otp_codes "
            "WHERE phone = :phone ORDER BY created_at DESC LIMIT 1"
        ),
        {"phone": payload.phone},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=400, detail="Request a code first")

    otp_id, code_hash, expires_at, consumed_at, attempts = row
    if consumed_at is not None:
        raise HTTPException(status_code=400, detail="This code has already been used")
    if now > expires_at:
        raise HTTPException(status_code=400, detail="This code has expired")
    if attempts >= settings.otp_max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Request a new code.",
        )

    if not verify_otp(payload.code, payload.phone, code_hash):
        await db.execute(
            text("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = :id"),
            {"id": otp_id},
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="Incorrect code")

    await db.execute(
        text("UPDATE otp_codes SET consumed_at = :now WHERE id = :id"),
        {"id": otp_id, "now": now},
    )

    existing = await db.execute(
        text("SELECT id, role FROM users WHERE phone = :phone"), {"phone": payload.phone}
    )
    user_row = existing.first()
    if user_row is None:
        user_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO users (id, phone, lang, country) "
                "VALUES (:id, :phone, :lang, :country)"
            ),
            {"id": user_id, "phone": payload.phone, "lang": "en", "country": settings.node_country},
        )
        role, is_new = "farmer", True
    else:
        user_id, role = user_row
        is_new = False

    await db.commit()
    return TokenResponse(
        access_token=create_access_token(str(user_id), role=role),
        user_id=str(user_id),
        is_new_user=is_new,
    )
