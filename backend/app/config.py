from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agrin_env: str = "dev"
    node_id: str = "node-in"
    node_country: str = "IN"
    # ISO 3166-2 for a node run by a state rather than a country -- "IN-MH" for
    # Maharashtra. The network this is for is Indian states sharing models and
    # statistics with each other, and two state nodes both declaring country
    # "IN" are indistinguishable in a discovery document: a peer cannot tell
    # whose district figures it just pulled, or which of them trained the model
    # it is about to adopt. Empty means the node speaks for the whole country.
    node_region: str = ""
    secret_key: str = "dev-only-insecure-key"

    database_url: str = "postgresql+asyncpg://agrin:agrin@localhost:5433/agrin"
    redis_url: str = "redis://localhost:6380"

    # Copernicus Data Space. Free tier is 10,000 processing units/month with no
    # carryover, so we keep our own ledger and stop well short of the ceiling.
    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    cdse_monthly_pu_cap: float = 9000.0


    # Google Gemini is the only cloud model this node calls, for both narration
    # and diagnosis. There is no second provider: the guard, the corrective round
    # and the deterministic template do not care which model was asked, so a
    # fallback model bought resilience rather than safety -- and resilience is
    # already covered by the template. See app/ai/gemini.py.
    gemini_api_key: str = ""
    # gemini-2.5-flash was the default and is now refused for new API keys:
    #   404 "This model models/gemini-2.5-flash is no longer available to new
    #   users. Please update your code to use models/gemini-3.6-flash."
    #
    # Worth knowing how that failed. A 404 is caught as "the cloud is
    # unavailable", so narration quietly served the deterministic template and
    # escalated photographs came back "not identified" -- both correct, honest
    # degradations, and both indistinguishable from an outage. /ready reported
    # cloud_diagnosis ok throughout, because it checks that a key is present,
    # not that the model answers.
    gemini_narrate_model: str = "gemini-3.6-flash"
    gemini_vision_model: str = "gemini-3.6-flash"

    # Google Maps Platform satellite basemap. Unset means OpenStreetMap street
    # tiles, which is the keyless path and still works. See
    # app/providers/basemap.py for why the key lives here and not in the APK.
    google_maps_api_key: str = ""
    node_language: str = "en"

    # Google Cloud Text-to-Speech, so an advisory can be listened to rather than
    # read. Unset means the app shows text only.
    google_tts_api_key: str = ""

    # Cloud Translation, used ONLY on the deterministic template -- the fallback
    # wording is English-only, so without this the farmer most likely to be
    # handed English is the one whose node ran out of credit. Never applied to a
    # narration a model already wrote in the target language.
    google_translate_api_key: str = ""

    # Every Google service below needs a service-account file rather than an API
    # key, so each is off unless a path is given and a node that never sets one
    # behaves exactly as it did before.
    google_credentials_path: str = ""
    google_project_id: str = ""

    # Vertex AI serves the SAME rice classifier for phones whose TFLite delegate
    # will not load. It does not replace on-device inference -- see
    # app/providers/vertex.py.
    vertex_location: str = "asia-south1"
    vertex_endpoint_id: str = ""

    # Earth Engine as a second NDVI supply behind Copernicus, for when the
    # monthly processing-unit cap is reached. NOT verified against live GEE.
    earth_engine_project: str = ""

    # BigQuery export of signed federation aggregates. District-level rows only,
    # and the k-anonymity refusal runs again before anything leaves.
    bigquery_dataset: str = ""
    bigquery_table: str = "federation_aggregates"

    # Hard ceilings on what this node can spend on the cloud model. The monthly
    # cap is what makes a stated running cost true rather than hopeful; the
    # per-user daily cap stops one handset draining the month. Both degrade to
    # on-device-only, never to an error the farmer has to understand.
    #
    # Narration only rephrases the computed advisory, and ai/guard.py rejects any
    # figure the payload does not contain -- so the safety property here is
    # enforced by code, not by model strength. That is what makes Flash the right
    # default for narration and Pro the right one for a leaf photograph, where a
    # misdiagnosis costs a farmer a spray or a season.
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
