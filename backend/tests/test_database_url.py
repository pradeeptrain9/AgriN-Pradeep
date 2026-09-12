"""One DATABASE_URL, two drivers that disagree about how to spell SSL.

The node reads its connection string once and hands it to two different pieces
of code: `asyncpg.connect` for migrations, SQLAlchemy's asyncpg dialect for
every request. Measured on SQLAlchemy 2.0.36 / asyncpg 0.30.0, they accept
opposite spellings and reject each other's:

    asyncpg.connect          sslmode=require  OK
                             ssl=require      CantChangeRuntimeParamError
    create_async_engine      sslmode=require  TypeError
                             ssl=require      OK

So no single string satisfies both, and a provider's URL pasted in unmodified
breaks the node whichever way it is spelled. Neon uses `sslmode`, which is the
worse of the two failures: migrations run, the container reports success, and
then every request 500s.

These tests pin the translation. They do not connect to anything -- the
behaviour above was measured directly against both drivers, and what has to
hold from here is that each consumer is handed the form its driver accepts.
"""

import pytest

from app.db.url import asyncpg_dsn, sqlalchemy_url

# What Neon actually shows you on the dashboard, channel_binding included.
NEON = (
    "postgresql://agrin:pw@ep-cool-name-123.ap-southeast-1.aws.neon.tech"
    "/neondb?sslmode=require&channel_binding=require"
)

LOCAL = "postgresql+asyncpg://agrin:agrin@localhost:5433/agrin"


class TestTheSqlalchemyHalf:
    def test_sslmode_becomes_ssl(self):
        # The whole deploy turns on this line. `sslmode` reaches asyncpg as a
        # keyword argument it has never heard of.
        assert "ssl=require" in sqlalchemy_url(NEON)
        assert "sslmode" not in sqlalchemy_url(NEON)

    def test_the_driver_is_named_in_the_scheme(self):
        assert sqlalchemy_url(NEON).startswith("postgresql+asyncpg://")

    def test_a_url_that_already_names_the_driver_is_left_alone(self):
        assert sqlalchemy_url(LOCAL).startswith("postgresql+asyncpg://")

    def test_an_already_correct_url_is_unchanged(self):
        good = "postgresql+asyncpg://agrin:pw@host/db?ssl=require"
        assert sqlalchemy_url(good) == good


class TestTheAsyncpgHalf:
    def test_ssl_becomes_sslmode(self):
        # The reverse mistake is just as fatal: asyncpg treats `ssl` as a
        # server runtime parameter and the connection dies at startup.
        assert "sslmode=require" in asyncpg_dsn(NEON)
        assert "ssl=require" not in asyncpg_dsn(NEON)

    def test_it_translates_back_from_the_sqlalchemy_spelling(self):
        assert "sslmode=require" in asyncpg_dsn("postgresql+asyncpg://u:p@h/db?ssl=require")

    def test_the_dialect_suffix_is_removed(self):
        # asyncpg is a driver, not a SQLAlchemy dialect; it cannot parse
        # "postgresql+asyncpg" as a scheme.
        assert asyncpg_dsn(LOCAL).startswith("postgresql://")
        assert "+asyncpg" not in asyncpg_dsn(LOCAL)


class TestChannelBinding:
    """Neon ships it; neither driver implements it.

    SQLAlchemy raises TypeError, and asyncpg forwards it to the server as a
    runtime parameter and gets `unrecognized configuration parameter`. asyncpg
    speaks SCRAM-SHA-256 but not the channel-binding variant, so no translation
    could honour it.
    """

    @pytest.mark.parametrize("translate", [sqlalchemy_url, asyncpg_dsn])
    def test_it_is_dropped_for_both(self, translate):
        assert "channel_binding" not in translate(NEON)

    @pytest.mark.parametrize("translate", [sqlalchemy_url, asyncpg_dsn])
    def test_dropping_it_is_announced(self, translate, caplog):
        # Silently weakening a protection against an attacker holding a
        # trusted certificate is not the same as deciding it is acceptable.
        with caplog.at_level("WARNING"):
            translate(NEON)
        assert any("channel_binding" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("translate", [sqlalchemy_url, asyncpg_dsn])
    def test_encryption_itself_survives(self, translate):
        # Dropping channel binding must not drop TLS with it.
        assert "require" in translate(NEON)


class TestItDoesNotMangleTheRest:
    @pytest.mark.parametrize("translate", [sqlalchemy_url, asyncpg_dsn])
    def test_host_user_password_and_database_are_preserved(self, translate):
        out = translate(NEON)
        assert "agrin:pw@ep-cool-name-123.ap-southeast-1.aws.neon.tech" in out
        assert "/neondb" in out

    @pytest.mark.parametrize("translate", [sqlalchemy_url, asyncpg_dsn])
    def test_a_url_with_no_query_string_stays_clean(self, translate):
        # A trailing "?" would be harmless but suggests something was lost.
        assert translate("postgresql://agrin:agrin@localhost:5433/agrin").endswith("agrin")

    @pytest.mark.parametrize("translate", [sqlalchemy_url, asyncpg_dsn])
    def test_unrelated_parameters_are_passed_through(self, translate):
        # Only the two known-incompatible names are touched; guessing at the
        # rest would break a setting an operator deliberately set.
        assert "application_name=agrin" in translate(f"{LOCAL}?application_name=agrin")

    @pytest.mark.parametrize("translate", [sqlalchemy_url, asyncpg_dsn])
    def test_the_development_default_is_untouched(self, translate):
        # Every developer and CI run goes through this path too, and it has no
        # SSL parameters at all.
        assert "localhost:5433/agrin" in translate(LOCAL)
        assert "ssl" not in translate(LOCAL)


class TestPooledEndpoints:
    """A managed provider's pooled endpoint is PgBouncer in transaction mode.

    Measured against PgBouncer 1.25, this stack splits cleanly in two:

      * SQLAlchemy's asyncpg dialect is immune. Twelve concurrent workers
        through the pooler, with pooler-side prepared-statement support
        switched off, produced no failures at all -- the dialect does its own
        statement handling. The request path needs nothing, which is the
        opposite of what most write-ups on this claim.
      * Raw asyncpg is not. `python -m app.db.migrate` against a pooled DSN
        dies with DuplicatePreparedStatementError: prepared statement
        "__asyncpg_stmt_3__" already exists -- transaction pooling hands each
        transaction a different server connection, and asyncpg's per-client
        statement names collide with what a previous client left behind.

    That failure is at the worst moment: `set -e` in docker-entrypoint.sh
    aborts before uvicorn ever starts, so a node handed a pooler URL never
    serves at all.
    """

    def test_migrations_do_not_cache_prepared_statements(self):
        # The one line standing between a pooler URL and a container that
        # cannot boot. Structural because the alternative is a live PgBouncer
        # in the unit suite.
        import inspect

        from app.db import migrate

        source = inspect.getsource(migrate.run)
        assert "statement_cache_size=0" in source

    def test_a_pooler_host_survives_translation_unchanged(self):
        # The hostname is the only thing distinguishing the two endpoints, and
        # nothing here may rewrite it -- pointing a node at the wrong one is a
        # silent change of connection semantics.
        pooled = (
            "postgresql://u:p@ep-snowy-scene-123-pooler.c-4.ap-southeast-1"
            ".aws.neon.tech/neondb?sslmode=require"
        )
        assert "-pooler.c-4.ap-southeast-1.aws.neon.tech" in sqlalchemy_url(pooled)
        assert "-pooler.c-4.ap-southeast-1.aws.neon.tech" in asyncpg_dsn(pooled)
