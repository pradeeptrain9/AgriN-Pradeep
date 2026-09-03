"""This node's signing identity.

Generated once on first use and stored in the node's own database. The private
key never appears in an API response, a log line, or a federation payload; only
the public key is ever published.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.federation.signing import generate_keypair


async def ensure_identity(db: AsyncSession) -> dict:
    """Return this node's identity, creating a keypair on first call."""
    settings = get_settings()
    result = await db.execute(
        text("SELECT node_id, public_key, country FROM node_identity WHERE node_id = :id"),
        {"id": settings.node_id},
    )
    row = result.mappings().first()
    if row:
        return dict(row)

    private_key, public_key = generate_keypair()
    await db.execute(
        text(
            "INSERT INTO node_identity (node_id, public_key, private_key, country) "
            "VALUES (:id, :pub, :priv, :country) ON CONFLICT (node_id) DO NOTHING"
        ),
        {
            "id": settings.node_id, "pub": public_key,
            "priv": private_key, "country": settings.node_country,
        },
    )
    await db.commit()
    return {
        "node_id": settings.node_id,
        "public_key": public_key,
        "country": settings.node_country,
    }


async def private_key(db: AsyncSession) -> str:
    """Read the signing key. Callers must never place this in a response."""
    settings = get_settings()
    await ensure_identity(db)
    result = await db.execute(
        text("SELECT private_key FROM node_identity WHERE node_id = :id"),
        {"id": settings.node_id},
    )
    row = result.first()
    if row is None:
        raise RuntimeError("node identity missing after creation")
    return row[0]
