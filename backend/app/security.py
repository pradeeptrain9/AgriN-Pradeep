"""Authentication primitives: phone OTP and JWT bearer tokens.

Phone OTP rather than passwords: the target user is a smallholder farmer with a
low-end Android phone, and a password they must remember is a barrier and a
support burden. The trade-off is that SMS delivery becomes a dependency, so the
OTP transport is abstracted behind `send_otp` and prints to the log in dev.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import get_settings
from app.sms.base import SmsError, otp_message
from app.sms.registry import gateway

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


def generate_otp() -> str:
    """Six digits from a cryptographically secure source."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str, phone: str) -> str:
    """Keyed hash so a database leak does not expose live codes.

    Salted with the phone number so the same code for two users hashes
    differently, and compared with a constant-time function.
    """
    settings = get_settings()
    return hmac.new(
        settings.secret_key.encode(),
        f"{phone}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_otp(code: str, phone: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(code, phone), code_hash)


def create_access_token(user_id: str, *, role: str = "farmer") -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
        "iss": settings.node_id,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


class CurrentUser:
    def __init__(self, user_id: str, role: str) -> None:
        self.user_id = user_id
        self.role = role


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return CurrentUser(user_id=user_id, role=payload.get("role", "farmer"))


async def send_otp(phone: str, code: str) -> None:
    """Deliver an OTP through the configured gateway.

    The gateway is chosen by SMS_PROVIDER and defaults to a console gateway that
    refuses to run outside development -- so a misconfigured production node
    fails loudly at the first sign-in attempt rather than looking healthy while
    nobody can get past the login screen.
    """
    settings = get_settings()
    minutes = max(1, settings.otp_ttl_seconds // 60)
    message = otp_message(code, minutes, brand=settings.sms_brand)

    try:
        result = await gateway().send(phone, message)
    except SmsError as exc:
        logger.error("OTP delivery failed for %s: %s", _redact(phone), exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "We could not send your code just now. Please try again in a "
                "moment." if exc.retryable else
                "Sending codes is not working. Please contact your extension officer."
            ),
        ) from exc

    logger.info(
        "OTP sent to %s via %s (%s)", _redact(phone), result.provider,
        result.provider_message_id or result.detail,
    )


def _redact(phone: str) -> str:
    """Log the last four digits only. A support log should be enough to match a
    farmer's report to an event without becoming a list of phone numbers."""
    digits = "".join(c for c in phone if c.isdigit())
    return f"...{digits[-4:]}" if len(digits) >= 4 else "..."
