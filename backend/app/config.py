from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agrin_env: str = "dev"
    node_id: str = "node-in"
    node_country: str = "IN"
    secret_key: str = "dev-only-insecure-key"

    database_url: str = "postgresql+asyncpg://agrin:agrin@localhost:5433/agrin"
    redis_url: str = "redis://localhost:6380"

    # Copernicus Data Space. Free tier is 10,000 processing units/month with no
    # carryover, so we keep our own ledger and stop well short of the ceiling.
    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    cdse_monthly_pu_cap: float = 9000.0

    anthropic_api_key: str = ""
    # Narration only rephrases the computed advisory, and ai/guard.py rejects
    # any figure the payload does not contain -- so the safety property here is
    # enforced by code, not by model strength. Measured guard-clean on a real
    # advisory at a third of Opus 5's cost.
    claude_narrate_model: str = "claude-sonnet-5"
    claude_vision_model: str = "claude-opus-5"

    # Hard ceilings on what this node can spend on Claude. The monthly cap is
    # what makes a stated running cost true rather than hopeful; the per-user
    # daily cap stops one handset draining the month. Both degrade to
    # on-device-only, never to an error the farmer has to understand.
    # Vision runs at high effort: a misdiagnosis costs a farmer a spray or a
    # season, and thinking tokens are a small share of a photo's cost. Lower it
    # only against measured accuracy on held-out photos.
    claude_vision_effort: str = "high"

    llm_monthly_usd_cap: float = 25.0
    llm_daily_calls_per_user: int = 20

    # SoilGrids fair-use is 5 calls/minute.
    soilgrids_rate_per_min: int = 5

    # --- SMS. Without a gateway no farmer can sign in, so this is a launch
    # blocker rather than an optional integration. See docs/DEPLOYMENT.md.
    sms_provider: str = "console"          # console | twilio | webhook
    sms_brand: str = "AgriN"

    # Let the console gateway run on a node that is not in development.
    #
    # The console gateway normally refuses outside dev, so a node cannot sit in
    # production looking healthy while silently unable to sign anybody in. That
    # refusal is right and stays. This is the narrow escape hatch for the one
    # legitimate case: a node deployed for the operator's own testing, before
    # an SMS provider exists, where the OTP can be read from the host's logs.
    #
    # Deliberately NOT achieved by setting AGRIN_ENV=dev, which is the obvious
    # shortcut and a much larger hole: dev also returns the code in the HTTP
    # response body (api/auth.py) and opens CORS to every origin (main.py), so
    # anyone who finds the URL can sign in as any phone number. This flag opens
    # exactly one door and leaves those shut.
    #
    # /ready still grades the console gateway a blocker regardless, because a
    # node no farmer can sign in to is not ready however deliberate it is.
    sms_allow_console: bool = False

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    twilio_messaging_service_sid: str = ""

    # Any other aggregator, configured rather than coded. India's DLT rules push
    # most senders onto local providers, and a network deployed by other people
    # cannot ship an SDK per country.
    sms_webhook_url: str = ""
    sms_webhook_method: str = "POST"
    sms_webhook_content_type: str = "form"   # form | json
    sms_webhook_payload: str = ""
    sms_webhook_headers: str = ""            # JSON object
    sms_webhook_success_field: str = ""
    # India: the DLT-registered template id the delivered text must match.
    sms_template_id: str = ""

    access_token_ttl_minutes: int = 60 * 24 * 30
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5

    # One allowlisted account that signs in with a fixed code and no SMS, so a
    # node with no provider can still be evaluated. Both must be set or the door
    # does not exist, and /ready calls it a blocker once any other account is on
    # the node. See app/demo.py.
    demo_phone: str = ""
    demo_code: str = ""

    media_root: str = "./media"


@lru_cache
def get_settings() -> Settings:
    return Settings()
