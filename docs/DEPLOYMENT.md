# Running an AgriN node

Written for someone standing up a national node for the first time. Order
matters: several steps are safety gates rather than configuration.

## 1. Infrastructure

```bash
git clone <repo> && cd agrin
docker compose up -d                     # Postgres+PostGIS+TimescaleDB, Redis
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp ../.env.example .env
.venv/bin/python -m app.db.migrate
```

Python 3.12 specifically: TensorFlow has no 3.14 wheels, and the geospatial
stack lags new releases.

### 1b. Managed hosting (Render + Neon)

`render.yaml` in the repo root deploys one node on Render's free tier with its
database on Neon. Render's own free Postgres is deleted after 30 days; Neon's
free tier does not expire and offers PostGIS, which is not optional here —
field geometry is `geography(Polygon,4326)` and every advisory calls `ST_X`,
`ST_Y` and `ST_Centroid`. TimescaleDB *is* optional: migration `001` wraps it
in an exception handler, the two hypertables stay ordinary tables, and nothing
else in the schema notices.

1. **Neon** — create a project, region closest to your farmers (AWS Singapore
   for India; every request pays that round trip). Copy the connection string.
   `CREATE EXTENSION postgis` runs inside migration `001`; you do not need to
   enable anything by hand.
2. **Render** — New → Blueprint, point it at this repository. Render reads
   `render.yaml` and prompts for each `sync: false` secret.
3. Paste the Neon string into `DATABASE_URL` **exactly as Neon shows it**,
   `?sslmode=require&channel_binding=require` included. See the warning below.
4. Generate `SECRET_KEY` with
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`. It signs
   every farmer's session token, and `/ready` fails the node while the
   development default is still in place.
5. Deploy, then from **outside your own network**:

```bash
curl https://<host>/health          # {"status":"ok","database":"up",...}
curl https://<host>/ready | jq .summary
```

#### The connection string is not portable between the two drivers

This node reads one `DATABASE_URL` and gives it to two pieces of code that
disagree about how to spell SSL. Measured on SQLAlchemy 2.0.36 / asyncpg
0.30.0:

| | `sslmode=require` | `ssl=require` |
|---|---|---|
| `asyncpg.connect` — migrations | works | `CantChangeRuntimeParamError` |
| `create_async_engine` — every request | `TypeError: unexpected keyword argument` | works |

They are mutually exclusive, so no single string satisfies both, and
`channel_binding=require` — which Neon puts in the string it shows you —
is rejected by *both*. Pasted in raw, Neon's spelling produces the worst of
the available failures: the container starts, applies all nine migrations,
prints `migrations up to date`, passes its health check, and then fails every
single request. A green deploy and a dead node.

`app/db/url.py` translates per consumer, so paste the string unmodified and
leave it alone. It drops `channel_binding` with a logged warning rather than
silently: asyncpg speaks SCRAM-SHA-256 but not the channel-binding variant, so
no translation could honour it. TLS is unaffected — `sslmode=require` still
applies.

#### What the free tier costs, stated plainly

* **Cold start.** Free services sleep after 15 minutes idle and take 30–60
  seconds to wake. Acceptable while you are the only user; not acceptable for a
  farmer standing in a field. The 750 instance-hours per month is roughly one
  service running continuously, with no headroom for a second.
* **No scheduled ingest.** Background workers need a paid instance, so
  `render.yaml` declares no worker and no Key Value store. Satellite and
  weather then refresh only when a farmer opens a field and the app POSTs
  `/refresh`, which FastAPI runs as a `BackgroundTask` with no Redis involved.
  This is backwards — see the docstring in `app/worker.py` — because the data a
  farmer needs is the data fetched *before* they opened the app, while they had
  signal. `/ready` grades weather freshness, so the staleness is visible rather
  than assumed.
* **Photographs are lost.** Free instances have ephemeral storage, rebuilt on
  every deploy and every wake. The diagnosis row and its verdict are in
  Postgres and survive; the image does not. `/ready` has a `photo_storage`
  check that compares stored diagnoses against files on disk and reports
  `degraded` once they diverge — it detects this rather than trusting a flag,
  because the operator who needs telling is the one who would not think to set
  one. Attach a persistent disk or object storage before an extension officer
  is expected to review a disputed photo.

None of the three is acceptable for the 15–25 farmer pilot in `PILOT.md`.
All three are fine for finishing the build and testing against a real node.

## 2. Node identity

Generated automatically on first request to `/federation/.well-known/agrin-node`
and stored in `node_identity`. **Back up that table.** Losing the private key
means every peer that pinned your public key sees verification failures, and
each must re-pin — a manual, trust-establishing step by design.

Set `NODE_ID` and `NODE_COUNTRY` in `.env` before first start; changing them
afterwards creates a second identity rather than renaming the first.

## 3. Credentials

| variable | needed for | without it |
|---|---|---|
| `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` | Sentinel-2 NDVI | Crop health reports `unknown` with a stated reason. Everything else works |
| `GEMINI_API_KEY` | narration, vision fallback | Deterministic English template is used; diagnoses that fail the gate return `inconclusive` |
| `SECRET_KEY` | JWT signing, OTP hashing | **Must be changed.** `openssl rand -hex 32` |

Register at <https://dataspace.copernicus.eu> for Copernicus. The free tier is
10,000 processing units per month with no carryover; `CDSE_MONTHLY_PU_CAP`
defaults to 9,000 so a node stops short of the ceiling. Watch `GET /quota`.

Open-Meteo needs no key but its free tier is **non-commercial only**. A national
deployment charging for the service needs their paid tier or a self-hosted
instance.

## 3b. Cloud diagnosis fallback

The on-device model answers about 44% of photos. The other 56% fail the
confidence gate and go to Gemini vision. **Without `GEMINI_API_KEY` those
photos return `inconclusive`** with "show this to your extension officer" -- a
safe, honest answer, but half your users get no diagnosis.

What the fallback does, in order:

1. gate refuses (crop not covered, low confidence, split decision, or high entropy)
2. the photo is re-encoded to <=640 px, stripping EXIF including GPS
3. The model is asked to choose from a **closed enum** of that crop's taxonomy
   classes, or answer `unknown`. It cannot invent a disease name.
4. the on-device guess is deliberately **not** sent, so the model is not anchored
   to the answer we already decided was untrustworthy
5. chemicals still come only from the verified allowlist, never from the model

Cost per escalated photo, from the rates in `app/ai/budget.py` (check them
against <https://ai.google.dev/gemini-api/docs/pricing> before trusting the
arithmetic -- they are rounded up on purpose, so the monthly cap binds early
rather than not at all):

| model | roughly, per photo | per 1,000 |
|---|---|---|
| gemini-3.6-flash | not yet published here | see the pricing page |
| gemini-2.5-pro | $0.032 | $32 |
| gemini-2.5-flash | $0.005 | $5 |

For 100 farmers scanning four leaves a month, 224 escalate: single dollars a
month at Flash rates. Set `GEMINI_VISION_MODEL` accordingly, and drop to a
cheaper model only against measured accuracy on held-out photographs of your own
crops, not to save money in the abstract -- a misdiagnosis costs a farmer a spray
or a season.

**`gemini-3.6-flash` is priced in `ai/budget.py` at the 2.5 Pro rate, which is
an over-estimate and is marked as unverified.** That is deliberate: `price()`
returns 0.00 for a model it does not recognise, and a model that costs nothing
can never reach the monthly cap. Over-pricing makes the cap bind early;
under-pricing removes it silently. Replace it with the real figure.

Narration is a different trade and uses `GEMINI_NARRATE_MODEL`, defaulting to
Flash. It only rephrases figures the engine already computed, and `ai/guard.py`
rejects any number it invents -- so the safety property there is enforced by
code, not by model strength.

The call runs in a threadpool, not on the event loop -- a synchronous multi-second
vision call inline in an async endpoint would stall every other request on the
node.

## 4. SMS delivery — the launch blocker

Without SMS nobody can sign in. `SMS_PROVIDER` defaults to a console gateway
that **refuses to run outside development**, so a misconfigured production node
fails loudly at the first sign-in rather than looking healthy while no farmer can
get past the login screen.

Three providers, chosen by `SMS_PROVIDER`:

### `twilio`

```bash
SMS_PROVIDER=twilio
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_MESSAGING_SERVICE_SID=MG...   # preferred
# or TWILIO_FROM_NUMBER=+1...
```

Prefer a Messaging Service: it handles sender pools, geo-matching and sticky
sender, which a bare number does not. No `twilio` SDK is installed — the
integration is one authenticated form POST, and a vendor SDK in a digital public
good is a dependency every other country inherits.

### `webhook` — any other aggregator

India's DLT rules push most senders onto local aggregators (MSG91, Gupshup,
Kaleyra); Brazil and South Africa have their own. Rather than an SDK per
country, point at the aggregator's HTTP API:

```bash
SMS_PROVIDER=webhook
SMS_WEBHOOK_URL=https://api.example-aggregator.com/v1/sms
SMS_WEBHOOK_CONTENT_TYPE=json
SMS_WEBHOOK_PAYLOAD='{"mobile":"{phone}","message":"{message}","template_id":"<DLT id>"}'
SMS_WEBHOOK_HEADERS='{"Authkey":"..."}'
SMS_WEBHOOK_SUCCESS_FIELD=success
```

`{phone}` and `{message}` are substituted; the message is JSON-escaped so an
apostrophe or newline cannot corrupt the body. `SMS_WEBHOOK_SUCCESS_FIELD`
matters because several aggregators return HTTP 200 with a failure in the body.

### `console` — development only

`ConsoleGateway` refuses to run when `AGRIN_ENV` is anything but `dev`,
deliberately: the failure it prevents is a node that looks healthy in every
check while no farmer can get past the login screen.

Testing your own deployed node before you have a provider is the one legitimate
exception, and it has its own switch:

```bash
SMS_ALLOW_CONSOLE=true
```

Codes then go to the node's own logs — on Render, the service's Logs tab — and
nowhere else. `/ready` still reports `sms_delivery` as a **blocker**, because
only someone with access to the host can sign in, and a farmer holding a phone
cannot.

**Do not reach for `AGRIN_ENV=dev` instead.** It is the obvious shortcut and a
much larger hole: dev mode also returns the code in the HTTP response body
(`api/auth.py`) and opens CORS to every origin (`main.py`). On a public URL
that means anyone who finds the host can request a code for any phone number
and sign in as that farmer. `SMS_ALLOW_CONSOLE` opens one door; `AGRIN_ENV=dev`
opens the building.


Prints the code to the log and refuses to run anywhere else.

### The demo account — for evaluation, never for a pilot

`SMS_ALLOW_CONSOLE` lets *you* sign in by reading your own logs. It does nothing
for someone you hand the app to: they reach the login screen and stop. That is
the correct behaviour for a farmer and useless for a reviewer, a judge, or a
ministry official you want to try the thing.

One allowlisted number fixes that:

```bash
DEMO_PHONE=+919876543210
DEMO_CODE=404404
```

Requesting a code for that number stores the fixed code instead of a random one
and skips the gateway entirely. **Verification is untouched** — the stored hash
is built exactly as any other code's is, so expiry, the five-attempt cap, the
single-use rule and the 30-second resend interval all still apply. There is no
branch for the demo account in `verify()`, which is why none of those properties
have to be re-argued.

Publish the number and the code to the people meant to have them. The node never
returns the code in a response; `{"demo_account": true}` says the door was used
and nothing more.

**This is a deliberate open door and the node treats it as one:**

| | |
|---|---|
| Fails closed | Both variables must be set and the code must be six digits. One stray export opens nothing. |
| Exactly one number | Not a prefix, not a range. A number one digit away goes to the real gateway. |
| Self-policing | `/ready` reports `demo_login` as **degraded** while this node has no other accounts, and escalates it to a **blocker** the moment any other account exists — a published sign-in code must not sit on a node holding somebody's real fields. |

Unset both before a pilot. The node will tell you if you forget, but do not make
it have to.

Compared with the two nearby shortcuts: `SMS_ALLOW_CONSOLE` opens one door to
whoever can read the logs; `DEMO_PHONE` opens one door to whoever holds one
published code; `AGRIN_ENV=dev` opens every door to everybody. Only the first
two are defensible, and only on a node with no farmers on it.

### When Google withdraws a model

`gemini-2.5-flash` was the default and now returns 404 for new API keys:

> This model models/gemini-2.5-flash is no longer available to new users.
> Please update your code to use models/gemini-3.6-flash.

The failure is quiet by construction. A 404 is caught as "the cloud is
unavailable", so narration serves the deterministic template and escalated
photographs return "not identified" -- both correct, honest degradations, and
both identical to an outage from inside the app.

`/ready` now asks Google whether each configured model will answer, by reading
the model's metadata rather than generating anything, so it costs no tokens.
A withdrawn model is reported as a **blocker** carrying Google's own message,
which usually names the replacement. Set `GEMINI_NARRATE_MODEL` and
`GEMINI_VISION_MODEL` and redeploy.

## Registration: what actually delays a launch

The integration is an afternoon. Registration is weeks, and it is
country-specific.

| Country / sender | Requirement | Typical wait |
|---|---|---|
| India, any aggregator | **DLT registration** with the telcos: sender ID and message template registered, and the delivered text must match the registered template exactly | days to weeks |
| US 10DLC | A2P brand + campaign | brand minutes, campaign 10–15 business days |
| US toll-free | Toll-free verification | 3–5 business days |
| **Twilio Verify** | **exempt from A2P registration** | immediate |

For India, `SMS_TEMPLATE_ID` exists so the DLT template id can be carried in the
webhook payload. **Register before you build the pilot schedule, not after** —
this is the single most common reason a launch slips.

**Twilio Verify is the way through a registration delay.** It is exempt from A2P
registration and available immediately, but it owns code generation and checking,
so it replaces this node's OTP logic rather than plugging in behind it. That is a
deliberate trade: faster launch, but the auth flow becomes Twilio-specific and
stops being portable to another country's aggregator. For a time-boxed pilot that
can be the right call; for a node meant to be handed to another country it is
not.

## 5. Districts## 5. Districts

Aggregates group by `fields.district`, which is null until populated. Fill it
from your own administrative boundaries:

```sql
UPDATE fields SET district = <your lookup>
WHERE district IS NULL AND archived_at IS NULL;
```

Fields with a null district collapse into one `unknown` bucket, which is
harmless but useless for cross-node comparison.

## 6. Pesticide verification — a safety gate, not configuration

`pesticides` ships **empty**, and a row serves only when four conditions hold:
verified, not revoked, review date not passed, and the active not on the
country's denylist. An unverified node gives cultural and preventive advice
only. That is deliberate: saying nothing is safe, saying the wrong dose is not.

**Never populate this from a language model, a web search, or another country's
register.** Doses and pre-harvest intervals come from the registered product
label in your jurisdiction. If you cannot point at the label, the row does not
go in.

### Workflow

```bash
python scripts/pesticides.py template > rows.csv   # blank template
# an agronomist fills it in from the register
python scripts/pesticides.py validate rows.csv     # checks, changes nothing
python scripts/pesticides.py import rows.csv \
    --verified-by "Dr A. Sharma, State Agriculture University" \
    --register  "CIB&RC Major Uses of Pesticides, 2026 edition" \
    --review-by 2027-09-01
```

Provenance is supplied on the command line, not in the CSV, so it cannot be
copied along with a spreadsheet of rows from somewhere else. The database
enforces it: a row marked verified without `verified_by`, `register_source` and
`review_by` violates a CHECK constraint and cannot be stored.

### What the validator catches

Automated checks, all covered by tests: unknown crop or disease codes, a disease
that does not belong to its crop, a pre-harvest interval outside 1–365, missing
dose or active, duplicates, and — less obviously — **a chemical row against a
bacterial or viral disease**, which could never serve because the diagnosis path
strips chemicals for those. It cannot tell you whether a dose is correct. That
is what the attribution is for.

### Expiry, revocation and denial

```bash
python scripts/pesticides.py expiring --days 90        # re-verify before these lapse
python scripts/pesticides.py revoke <id> --reason "registration withdrawn"
python scripts/pesticides.py deny --country IN --active "<name>" \
    --reason "banned for this crop" --source "<notification>" --by "<name>"
```

Expiry is enforced in the serving query, not by a cleanup job, so **a node nobody
maintains fails closed** — it stops offering chemicals rather than serving stale
ones indefinitely. The denylist overrides everything, including a row that
claims to be verified, so a national ban is one statement rather than a hunt
through per-crop rows.

## 7. Scheduled ingest

```bash
.venv/bin/arq app.worker.WorkerSettings
```

Weather daily, satellite every three days for fields whose last observation is
stale, soil once per field ever. The satellite job stops when the PU ledger hits
the cap rather than failing per field.

## 8. Serve your own basemap before scaling

`mobile/src/constants/mapStyle.ts` points at `tile.openstreetmap.org`, whose
usage policy forbids bulk downloading and heavy automated use. Acceptable for a
pilot of tens of farmers; **not** acceptable for national rollout. Stand up your
own raster tiles or a PMTiles archive and repoint `BASEMAP_TILE_URL`.

## 9. Mobile build

```bash
cd mobile && npm install
# release signing credentials in ~/.gradle/gradle.properties, never in the repo
cd android && ./gradlew bundleRelease
```

Set `PRODUCTION_URL` in `mobile/src/constants/config.ts` to your node before
building, though farmers can also change it in Settings.

## 10. Joining the network

```bash
curl -X POST https://your-node/federation/peers \
     -H 'Content-Type: application/json' \
     -d '{"base_url": "https://node-in.example"}'
```

This pins the peer's public key. It is trust-on-first-use: **confirm the key out
of band** — a phone call, a signed email — before registering. Everything that
peer later sends is verified against the key recorded at this moment.

## Pre-launch checklist

- [ ] `SECRET_KEY` changed from the default
- [ ] `node_identity` table included in backups
- [ ] SMS gateway wired and tested
- [ ] Districts populated
- [ ] Pesticide rows verified against the national register, or table left empty
- [ ] Own basemap serving, or pilot small enough for the OSM policy
- [ ] `GET /quota` monitored; alert before 9,000 PU
- [ ] Peer keys confirmed out of band
- [ ] Open-Meteo licensing appropriate to your use
- [ ] `/health` monitored

## What this node will not do

It will not name a pesticide you have not verified, will not report crop health
from a stale satellite image without saying so, will not send farmer records to
a peer, and will not let a language model invent a quantity. Those are enforced
in code, not policy, and the tests fail if they regress.

## Gemini API key, and keeping the bill small

The node runs without a key. On-device diagnosis answers about 44% of leaf
photos, the rest return "inconclusive", and advisories use the deterministic
template. Adding a key turns on the cloud fallback for the other 56% and lets
the model rephrase advisories into plainer language.

### Getting one

1. Sign in at <https://aistudio.google.com/apikey>.
2. **Settings -> API keys -> Create key.** Copy it once; it is not shown again.
3. **Enable billing** under Settings -> Billing. A key on an org with no credit
   authenticates and then fails on every request, which shows up here as
   `cloud_diagnosis` degraded rather than as an obvious billing error.
4. Put it in `backend/.env` as `GEMINI_API_KEY=AIza...` and restart the
   API. Never commit it; `.env` is gitignored and `.env.example` is the file
   that gets committed.
5. Confirm with `curl localhost:8099/ready` -- `cloud_diagnosis` should read
   `ok` with the month's spend so far.

Optionally set a **workspace spend limit** in the Console as a final backstop
underneath the node's own cap. The Console limit is enforced by Anthropic and
cannot be raised by a bug in this code.

### What it costs

Priced from the rates in `app/ai/budget.py` (verify against
<https://ai.google.dev/gemini-api/docs/pricing> when changing models):

**Estimated, not measured.** The figures below are token counts observed on a
real advisory and a real 640 px leaf photograph, priced through
`app/ai/budget.py`. The token counts are real; the prices are a rate table, and
no live Gemini invoice has been reconciled against them yet. Treat them as the
right order of magnitude and check `GET /quota`, which reports what this node
actually recorded.

| Call | Model | Estimated cost |
|---|---|---|
| One advisory narration | `gemini-2.5-flash` | $0.0038 |
| One advisory narration | `gemini-2.5-pro` | $0.0225 |
| One leaf photo, cloud fallback | `gemini-2.5-pro` | $0.0320 |
| One leaf photo, cloud fallback | `gemini-2.5-flash` | $0.0053 |

The 2.5 models are no longer available to new API keys, so these rows are kept
only as an order-of-magnitude guide.

**Narration dominates the bill, by roughly forty to one.** An advisory is read
daily; a leaf photograph is occasional. Sizing a 20-farmer pilot with one field
each, at one narration per field per day (what the cache allows once the weather
ingest moves the payload) and a few photos per farmer per month:

| Configuration | Narration | Diagnosis | Month |
|---|---|---|---|
| Opus 5 throughout | ~$27 | ~$0.60 | **~$28 -- over the $25 cap** |
| Haiku narration, Opus vision | ~$3 | ~$0.60 | ~$4 |

The default configuration does not fit the default cap at 20 farmers. Either
raise `LLM_MONTHLY_USD_CAP`, or move narration to a cheaper model -- see below.

### The controls, cheapest first

- **The on-device model** is the largest saving and needs no configuration: the
  44% of photos it answers cost nothing.
- **Narration reuse** keys the stored narration on a hash of the advisory
  payload, so it is regenerated only when the advice actually changes -- not on
  every screen open. The advisory screen reloads on focus, so this is the
  difference between paying per glance and paying per change.
- **Diagnosis reuse** keys a cloud identification on a SHA-256 of the prepared
  image bytes, so the same photograph is never paid for twice: a retry after
  "inconclusive", a double tap on a slow connection, an outbox replay, two
  people on a shared handset. Each submission is still its own record -- only
  the paid call is skipped, and the farmer is told the answer is a repeat. An
  earlier *inconclusive* is deliberately not cached: that was a refusal to
  answer, and re-asking is reasonable.
- **`LLM_MONTHLY_USD_CAP`** (default 25) is a hard stop checked before each
  call, not a warning after the fact. **Set it at or below the credit actually
  on the account.** A cap above the balance is not a cap: the account runs dry
  first, and every call then fails as "the cloud is unavailable" with nothing
  saying why. Sizing it from the measured per-call costs above tells you how
  many farmer-days a given balance buys -- at Sonnet narration, roughly 65
  advisories or 60 leaf photographs per dollar.
- **`LLM_DAILY_CALLS_PER_USER`** (default 20) stops a single handset -- a retry
  loop, a child with the camera -- consuming the month.
- **Effort** is already `low` on narration. Vision runs at `high`, now settable
  with `CLAUDE_VISION_EFFORT`; lowering it trades diagnostic quality and should
  be measured against held-out photos, not assumed.
- **A cheaper model for narration** is unusually well-suited here, and is the
  single biggest saving available. The narration invents nothing: `ai/guard.py`
  mechanically rejects any figure absent from the computed advisory, and a
  rejected narration falls back to the template. The safety property is enforced
  by code, not by model quality -- so a weaker model produces plainer sentences
  or gets rejected, and cannot produce a wrong number.

  `GEMINI_NARRATE_MODEL` therefore defaults to `gemini-3.6-flash`: a weaker
  model writes plainer sentences or gets rejected, and cannot write a wrong
  number.

  Vision is a different judgement: there the model *is* the answer, nothing
  downstream can check it, and a wrong disease costs a spray or a season. Leave
  `GEMINI_VISION_MODEL` on the strongest model your key can reach, unless you have measured a cheaper
  model on held-out photographs of your own crops.

  Request parameters are built in `ai/gemini.py`, which strips the JSON Schema
  keywords Gemini's OpenAPI subset rejects -- `additionalProperties`, `$schema`
  and friends. Sending one is a 400 for the whole request, which both call sites
  would swallow as "the cloud is unavailable", so a schema change that looks
  harmless can silently disable the feature. Adding a model means adding a price
  in `ai/budget.py`; `rate_for()` matches by family prefix, because Gemini
  reports the version it actually served (`gemini-3.6-flash-002`) and an
  exact-match table would price that at zero.

Prompt caching is **not** used, and would do nothing here: both system prompts
are short enough that implicit context caching does not engage,
so a `cache_control` marker would be silently ignored.

### Watching the spend

```bash
curl -H "Authorization: Bearer <token>" localhost:8099/quota
```

Returns the month's model spend against the cap, this user's checks today, and
the Copernicus processing units alongside. Per-call detail, including which
feature spent it, is in the `llm_ledger` table.

## 11. Deploying the node

The app refuses plain HTTP to anything but a loopback address -- enforced twice,
in the client and in Android's network security config -- so **a deployed node
must have a real certificate**. There is no configuration that lets a phone talk
to `http://203.0.113.10:8099`, and that is deliberate: farm data should not
cross a village network in clear text.

That leaves two useful paths.

### A tunnel, for a field test today

For trying the app on a real phone away from your desk, you do not need a server
at all. A quick tunnel puts an HTTPS address in front of the node running on
your own machine:

```bash
brew install cloudflared
```

```bash
cloudflared tunnel --url http://localhost:8099
```

It prints a `https://<something>.trycloudflare.com` address. Enter that in the
app under *Change*. No account, no cost, and it satisfies the certificate
requirement because the tunnel terminates TLS.

Two things to know: the address changes every time you restart it, and the
tunnel is public while it runs -- anyone with the URL reaches your node. Use it
for a test, not for a pilot, and stop it when you are done.

### The real thing

One machine, everything on it, TLS obtained automatically:

```bash
cp deploy/.env.prod.example deploy/.env.prod
```

Fill it in. `SECRET_KEY` and `POSTGRES_PASSWORD` must be generated, not invented:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Point your domain's A record at the machine **before** starting, then:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod up -d --build
```

Caddy obtains a Let's Encrypt certificate on first start, which needs ports 80
and 443 reachable from the internet and DNS already resolving.

What the production stack does differently from the development one, and why:

| | Why |
|---|---|
| API and ingest worker are both containers | In development the worker is run by hand and therefore forgotten. This node ran for a day with no satellite ingest because of exactly that. |
| Postgres and Redis publish no ports | They are reachable on the compose network and nowhere else. A database on the internet with the development password is the likeliest way this gets taken. |
| Migrations run on API start | Forward-only and idempotent, so a fresh machine needs no manual step -- the step that gets skipped at 6am on a launch day. The worker waits for the API rather than racing it. |
| The API runs as uid 10001, not root | It needs to read its own code and talk to Postgres. Nothing else. |
| A distinct compose project name | Both files otherwise default to the directory name, and starting production recreates the development containers against production volumes. |

Then check it from the machine itself:

```bash
curl -s https://<your domain>/ready | python3 -m json.tool
```

A fresh deployment reports **one blocker**: `sms_delivery — console gateway
outside development`. That is correct. The console gateway refuses to run
outside development, so nobody can sign in until a real SMS provider is
configured (section 4). It is the launch blocker, and `/ready` is supposed to
say so rather than let you discover it with farmers standing in front of you.

Back up the database volume before enrolling anyone:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod \
  exec -T db pg_dump -U agrin agrin | gzip > agrin-$(date +%F).sql.gz
```
