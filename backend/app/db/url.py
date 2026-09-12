"""Turning one DATABASE_URL into the two dialects that have to consume it.

A managed Postgres hands out one connection string and this node feeds it to
two different pieces of code, which disagree about it. Measured against
SQLAlchemy 2.0.36 and asyncpg 0.30.0:

    asyncpg.connect(dsn)          sslmode=require  OK
                                  ssl=require      CantChangeRuntimeParamError
    create_async_engine(url)      sslmode=require  TypeError: unexpected
                                                   keyword argument 'sslmode'
                                  ssl=require      OK

They are mutually exclusive. There is no single string that works for both, so
pasting a provider's URL in unmodified breaks the node whichever spelling the
provider chose -- and it breaks it in the worst possible shape, because
migrations run through asyncpg directly and the API runs through SQLAlchemy.
With Neon's own `sslmode`, the container starts, applies every migration,
prints "migrations up to date", passes its build, and then fails every single
request. A green deploy and a dead node.

`channel_binding` is dropped rather than translated. Neon puts
`channel_binding=require` in the string it shows you, and neither driver
accepts it -- SQLAlchemy raises TypeError, asyncpg sends it to the server as a
runtime parameter and gets `unrecognized configuration parameter`. asyncpg
authenticates with SCRAM-SHA-256 but does not implement the channel-binding
variant, so this is not something a translation could satisfy. It is logged
when dropped, because it is a real protection against an attacker who already
holds a trusted certificate, and losing it quietly is not the same as deciding
it is acceptable. TLS itself is unaffected: `sslmode=require` still applies.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

ASYNCPG_SCHEME = "postgresql+asyncpg"
PLAIN_SCHEME = "postgresql"

# Understood by libpq, and by neither of the two drivers here.
UNSUPPORTED_PARAMS = frozenset({"channel_binding"})


def _split(url: str) -> tuple[list[str], list[tuple[str, str]]]:
    parts = urlsplit(url)
    return list(parts), parse_qsl(parts.query, keep_blank_values=True)


def _rebuild(parts: list[str], params: list[tuple[str, str]]) -> str:
    parts = list(parts)
    parts[3] = urlencode(params)
    return urlunsplit(parts)


def _drop_unsupported(params: list[tuple[str, str]]) -> list[tuple[str, str]]:
    kept = [(k, v) for k, v in params if k.lower() not in UNSUPPORTED_PARAMS]
    for key, _ in params:
        if key.lower() in UNSUPPORTED_PARAMS:
            logger.warning(
                "Dropping '%s' from DATABASE_URL: no Python driver this node "
                "uses implements it. The connection is still encrypted if "
                "sslmode is set.",
                key,
            )
    return kept


def sqlalchemy_url(url: str) -> str:
    """The URL SQLAlchemy's asyncpg dialect will accept.

    `sslmode` becomes `ssl`, which is the name the dialect forwards to
    asyncpg's own keyword argument.
    """
    parts, params = _split(url)
    if not parts[0].startswith(ASYNCPG_SCHEME):
        parts[0] = parts[0].replace(PLAIN_SCHEME, ASYNCPG_SCHEME, 1)

    params = _drop_unsupported(params)
    params = [("ssl", v) if k.lower() in ("sslmode", "ssl") else (k, v) for k, v in params]
    return _rebuild(parts, params)


def asyncpg_dsn(url: str) -> str:
    """The DSN `asyncpg.connect` will accept.

    The reverse translation, plus the plain scheme: asyncpg is the driver, not
    a SQLAlchemy dialect, and does not know the `+asyncpg` suffix.
    """
    parts, params = _split(url)
    parts[0] = PLAIN_SCHEME

    params = _drop_unsupported(params)
    params = [
        ("sslmode", v) if k.lower() in ("sslmode", "ssl") else (k, v) for k, v in params
    ]
    return _rebuild(parts, params)
