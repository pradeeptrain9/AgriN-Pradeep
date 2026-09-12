# AgriN — Interoperable Digital Agriculture Network

An **AgriN node**: localised agro-advisories for small and marginal farmers from
satellite, soil and weather data, plus photo-based crop disease diagnosis.
Designed as a digital public good — each country runs its own node, and nodes
exchange *models and aggregated statistics*, never raw farmer data.

## The one rule

**Every recommended quantity is computed deterministically. Language models only
translate and explain those numbers — they never produce them.**

An LLM that invents an irrigation depth or a nitrogen dose is a crop-failure
liability. The engine emits JSON; `ai/narrate.py` may only rephrase it, and its
output is validated so that every figure it prints also appears in the input.

## Status

| Area | State |
|---|---|
| FAO-56 ET0 (`engine/et0.py`) | Done. Reproduces both FAO chapter 4 worked examples |
| Crop registry (`engine/crops.py`) | Done. 14 crops, FAO-56 tables 11/12/22 |
| Soil water balance (`engine/water.py`) | Done. FAO-56 chapter 8, mass-conservation tested. Upland crops only |
| Paddy water balance (`engine/paddy.py`) | Done. Ponded-depth model + IRRI safe AWD. Rice routes here, not to `water.py` |
| Crop health (`engine/anomaly.py`) | Done. NDVI vs expected curve, cloud-honest |
| Nutrients (`engine/nutrients.py`) | Done. Removal + fertility class + credits |
| Rotation (`engine/rotation.py`) | Done. Explainable weighted scoring |
| Weather (`providers/weather.py`) | Done, live against Open-Meteo |
| Soil (`providers/soil.py`) | Done. Four-source priority chain |
| Satellite (`providers/sentinel.py`) | **Done, verified live against Copernicus.** A real field ingested 57 observations for 1.63 PU, with 30 of 49 intervals rejected for cloud. Crop health reaches the phone as plain language: "Greenness 0.20 against 0.38 expected at this stage. Last clear picture was 24 days ago" |
| REST API | Auth, fields, crop, soil, refresh, advisory, quota |
| Gemini narration | **Done, verified live.** Guard came back clean on the first real call and on every model tried; each figure traces to the engine. Reused until the advice itself changes, so screen reloads are free |
| Gemini spend control | Done. Every call priced from the API's own `usage` into `llm_ledger`; hard monthly USD cap and a per-user daily cap, both checked **before** the call. Visible on `/quota` and `/ready` |
| Paying twice for the same answer | Prevented on both paths. A narration is reused until the advice changes; a cloud diagnosis is reused for a byte-identical photograph. Verified live: 3 advisory requests billed 1 call, 2 identical leaf submissions billed 1 call |
| Disease gate + taxonomy | Done. Coverage-first gate, IPM advice, verified-only chemicals |
| Disease endpoints | Done. `/diagnoses` with EXIF stripping and on-device gate |
| Cloud diagnosis fallback | **Done, verified live.** First honest measurement of the escalated path: on 40 held-out rice leaves it answered 80% and was **0.781 accurate on those**, abstaining rather than guessing on the rest. Weakest on bacterial leaf blight (3 right, 4 wrong) -- that class is near chance and the "confirm with your extension officer" line is doing real work |
| Disease model | **Shipping.** `rice_disease` v1.0.0, 1.14 MB, 10 rice classes. **0.981 accuracy on what is shown to a farmer**, answering 44% of photos and escalating the rest. Runs on device; verified diagnosing a held-out leaf at 0.89 confidence. See `training/README.md` |
| Mobile app | **Complete loop verified on an emulator.** Sign in -> map field -> set crop -> advisory -> Soil Health Card -> scan. Signed release, MapLibre tiles, runtime node switching. Geometry (20/20), tile sizing (6/6), node URL (22/22) verified |
| Scheduled ingest (`app/worker.py`) | Done. arq cron: weather daily, satellite every 3 days when stale, soil once per field. PU-capped |
| Deployment runbook | Done. `docs/DEPLOYMENT.md` |
| Deployable stack | Done, and **started end to end locally**: API, ingest worker, Postgres, Redis and Caddy for automatic TLS. Migrations apply on start, the API runs as a non-root user, and the database publishes no ports. A phone cannot reach a node without a certificate -- the client refuses cleartext to any real host -- so TLS is part of the stack, not an afterthought |
| Diagnosis without a field | Done. Checking a leaf needs nothing but the leaf: reachable from the home screen with no fields mapped, and it asks which crop rather than assuming one -- the coverage gate is scoped per crop, so a maize leaf checked as rice is exactly what the gate exists to refuse |
| SMS gateway | Done. Provider-agnostic (`twilio`, `webhook` for any aggregator, `console` for dev). Plain HTTP, no vendor SDK. Console gateway refuses to run outside development |
| Pilot readiness | `GET /ready` grades every dependency by farmer-visible consequence. Protocol in `docs/PILOT.md` |
| Farmer feedback / grievance | Done. Prompt sits under every advisory and diagnosis; harm reports never blocked, surfaced for human triage, corrections collected as ground truth |
| Consent | Done. Enforced as a gate, not a banner: a signed-in account with no recorded consent can reach nothing but the consent screen. Names the node, cleared on sign-out |
| Offline walk, verified | **Run end to end on the emulator with networking disabled.** Walk a boundary, close the loop, save, set the crop -- all with no signal -- then reconnect and watch it sync. 31 GPS points, 519 m, 1.74 ha computed on device against the node's 1.73 ha. Found four bugs no unit test had: see below |
| Offline-first | Done. A field walked with no signal gets a local id and behaves like any other field -- it is listed, marked `Not sent yet`, and accepts a crop, a soil card, leaf photos and complaints. The outbox drains in order on reconnect and rewrites every queued reference to the id the node assigns. 15 tests against real SQLite; see the caveat below |
| Federation / model registry | Done, and **demonstrated between two live nodes**. Discovery, key pinning, model cards, k-anonymised signed aggregates, tamper and replay rejection. See `federation/README.md` |

## Not verified

**The offline walk has now been run end to end on the emulator**, and it found
four bugs no test had (below). What remains unverified is real hardware: a
physical phone, real satellites, and a boundary walked on foot. The emulator
feeds perfect 3-6 m fixes on demand; a real walk has drift, canopy shadow, and a
farmer who stops to talk to someone.

**Feedback can be submitted twice for the same advisory.** The prompt resets when
the screen is remounted, and the server does not deduplicate. For a harm report a
duplicate is much better than a loss, so this is left alone deliberately, but any
counting over the feedback table has to account for it.

## Bugs the offline walk found

None of these were visible to the test suites; all four needed the real app on a
real screen with the network off.

**The app crashed on "Start walking".** `react-native-background-actions` 3.0.1
built its notification `PendingIntent` with `FLAG_MUTABLE` and an implicit
intent, which Android 14 forbids outright. Every attempt to map a field
hard-crashed the process on any modern phone -- the core interaction of the
product, on most of the devices it targets. Fixed by upgrading to 4.0.1.

**Then it crashed again, on a missing permission.** The boundary walk runs as a
foreground service so GPS keeps sampling with the screen off in a pocket; that
service holds a wake lock, and `WAKE_LOCK` was never declared. Added.

**Reconnecting created a duplicate field every time.** The sync saved the
server's copy of a field *before* remapping the local placeholder onto its new
id, so the remap's `UPDATE` collided with the row just inserted, threw, and left
the item queued -- which created another field on the next drain. Two identical
fields, ninety seconds apart, on a real run. The unit tests missed it because
they exercised the remap in isolation and never after a save. Fixed by remapping
first, made collision-safe, and pinned by a regression test that fails without
the fix.

**A field mapped offline could not be given a crop.** The crop registry was
fetched from the node on every visit, so with no signal the screen dead-ended at
"Could not load the crop list" -- while the pending-field screen promised "you
can add the crop now". It is static reference data; it is now mirrored locally
and served from the mirror when offline.

Two smaller ones from the same run: the field list did not reconcile after a
drain, so a field that had just synced still read "Not sent yet" and then "no
crop set"; and `.env` carried a stale `CLAUDE_NARRATE_MODEL` that silently
outranked the code default, so narration ran on the expensive model after the
cheaper one had been chosen. `/ready` now names both models in use.

## Verified upstream findings

These were established by probing the live services, and they shape the design.

**SoilGrids has no data over India.** Queried 2026-09-02: Brazil and Iowa return
values; Ludhiana, Nashik and Guntur return `null` for every property, and this
persists across backoff. India is masked in the public product. A single-source
soil design would have produced nutrient advice derived from nulls. Hence the
priority chain in `providers/soil.py`:

    SOIL_HEALTH_CARD (high) -> FEEL_TEST (medium) -> SOILGRIDS (medium) -> FALLBACK (low)

Every profile carries its source and confidence, and the advisory states which
was used.

**Open-Meteo soil bands differ by endpoint.** The forecast model exposes
`soil_moisture_9_to_27cm`; the ERA5 archive exposes `soil_moisture_7_to_28cm`.
Asking the archive for the forecast band returns a column of nulls rather than
an error. Both variable sets are pinned and asserted by tests.

**PlantVillage cannot carry the disease model.** It covers 14 crops and contains
no rice and no wheat, overlapping the AgriN registry on maize, potato and soybean
only. The dataset stack is crop-led instead, anchored on Paddy Doctor (16,225
field images) for rice; wheat data is thin (~2,400 images) and gated at a higher
confidence threshold. Crops with no dataset never reach the on-device model --
a softmax always sums to one, so an untrained crop still yields a confident
label. See `training/README.md`.

**Our ET0 tracks Open-Meteo's within 0.16 mm/day** on live data (they integrate
hourly, we compute daily — the difference is expected and documented).

**Rice cannot use the upland water balance.** Running `water.py` on a live
Ludhiana paddy reported 15 water-stressed days in 30 while the field sat under
310 mm of monsoon rain. A puddled paddy is deliberately ponded on a plough pan
and loses most water to percolation, not transpiration. `engine/paddy.py` models
water level relative to the soil surface (ponded above, perched water table
below) and follows IRRI safe AWD. Same field after the fix: 0 stress days, and a
19.9% water saving against continuous flooding.

## Running it

```bash
docker-compose up -d
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp ../.env.example .env
.venv/bin/python -m app.db.migrate
.venv/bin/uvicorn app.main:app --reload --port 8099
```

Tests (no network, no database needed):

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
```

Full API walkthrough against a running server:

```bash
cd backend && .venv/bin/python scripts/e2e_smoke.py
```

### Satellite credentials

NDVI stays empty until Copernicus credentials are set — the advisory degrades
honestly, reporting crop health as `unknown` with a stated reason rather than
guessing. Register free at <https://dataspace.copernicus.eu>, create an OAuth
client, and set `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET` in `.env`.

The free tier is 10,000 processing units per month with no carryover. The node
records actual spend from the `x-processingunits-spent` response header in
`pu_ledger` and refuses further requests past `CDSE_MONTHLY_PU_CAP`. Check it at
`GET /quota`.

## Data sources

| Source | Use | Terms |
|---|---|---|
| Copernicus Data Space (Sentinel Hub) | NDVI/NDMI/NDRE, 10 m | Free tier, 10k PU/month |
| Open-Meteo | Forecast + ERA5 archive + soil moisture | Free, no key, **non-commercial only** |
| ISRIC SoilGrids v2 | Soil properties, 250 m | Free, 5 req/min, beta, **no India coverage** |
| Soil Health Card | Available N/P/K, OC, pH | Farmer-held government card |

## Release build

Signed with a real release key (not the debug key). Credentials live in
`~/.gradle/gradle.properties`, never in the repository:

```bash
cd mobile/android && ./gradlew assembleRelease   # per-ABI APKs
cd mobile/android && ./gradlew bundleRelease     # Play App Bundle
```

Measured on an arm64 Android 16 emulator with 2.0 GB RAM -- the low-tier target
device:

| ABI | before | after | change |
|---|---|---|---|
| armeabi-v7a | 25.5 MB | **24.3 MB** | meets the <=25 MB target |
| arm64-v8a | 35.3 MB | **33.4 MB** | still over |

What changed: the TFLite GPU delegate (`libtensorflowlite_gpu_jni.so`, 1.25 MB
per ABI) is excluded from packaging and `services/tflite.ts` requests the CPU
delegate explicitly. An int8 MobileNetV3-Small runs in tens of milliseconds on
the CPU of the cheapest target device, so the delegate was paying 1.25 MB in
every install to speed up something that was never the bottleneck.

Native libraries are still ~21 MB of the armeabi build, led by `libmaplibre.so`
at 8.05 MB.

**Decision: the map stays and the size is accepted.** A farmer confirming that
the polygon on screen is genuinely their field is worth more than 8 MB of
download. arm64 sits at 33.4 MB and that is fine. The App Bundle config splits
by ABI, density and language so Play delivers one variant per device rather than
a universal binary, which is where the real download saving comes from.

Size is therefore closed, not outstanding. It is only worth reopening if field
testing shows farmers abandoning the install on a metered connection.

## The farmer's loop, verified end to end

Walked on an arm64 Android 16 emulator against a live node, with each step
confirmed in Postgres rather than only on screen:

| step | result |
|---|---|
| sign in | user row created, OTP consumed |
| field list | `GET /fields` 200, field renders |
| no crop set | shows "Tell us what you are growing" with one action, not an error |
| set crop | `rice`, sown 2026-07-04, previous crop recorded in `crop_cycles` |
| advisory | narration, stage "mid, day 61", fertiliser 64-96 kg N/ha |
| Soil Health Card | saved as `soil_health_card`, pH 8.1, OC 0.41, N 252, confidence **high** |
| advisory recomputed | nitrogen **64-96 -> 72-112 kg/ha**, soil source now "high confidence" |
| scan a leaf | screen reachable from the crop section |

Crop health correctly reads "Not known -- no clear satellite picture of this
field yet" throughout, because this field has no Sentinel observations. That is
the designed honest degradation, not a failure.

### Three bugs this walkthrough found

**The node URL was only loaded on the sign-in screen.** A signed-in user never
mounts that screen, so `currentNodeUrl()` fell back to the default production
host and the app reported "No internet" from the second launch onward -- while
being perfectly online. It now loads at app startup, before any request. This
was invisible in testing because signing in fresh always worked.

**Three screens were unreachable.** `SoilCard` was navigated to but never
registered, `Scan` was registered but never navigated to, and there was no
crop-selection screen at all -- so the advisory returned `status: no_crop` with
no way to fix it. A check in CI-able form: every `navigate()` target must be a
registered screen, and every registered screen must be reachable.

**The field screen never reloaded on focus.** Saving a crop and going back left
the old advisory on screen, so a farmer who had just entered their crop was
still being told to enter their crop. Now reloads via `useFocusEffect`.

## React Native platform findings

Two bugs that only appeared on a device, both worth knowing before writing
similar code.

**React Native does not ship a working `URL`.** Its class constructs fine but
every accessor throws:

```js
get protocol(): string { throw new Error('URL.protocol is not implemented'); }
```

`services/node.ts` used `new URL(x).protocol` to enforce https-only for the node
address. The constructor succeeded, so the `try/catch` around it passed, and the
throw happened outside it -- meaning the **https-only check never ran** and Save
crashed instead of validating. Now parsed with a regular expression, covered by
`scripts/verify-node-url.ts` (22 assertions including `javascript:` and `file://`
rejection).

**A `Modal` nested inside `KeyboardAvoidingView` will not reopen** after its
first dismissal on Android. The node selector opened twice and then stopped. The
two are now siblings, with `onRequestClose` wired for the back button; verified
by opening and closing it four times in a row.

## Choosing your node

AgriN is federated, so the app asks which node a farmer belongs to rather than
hard-coding one. The address is shown on the sign-in screen, changeable before
sign-in, and persisted on the device. Plain `http` is refused for any host other
than loopback, so a typo cannot quietly send farm data in the clear.

This also removes the need to rebuild the app to point at a local node during
development.

## Basemap and tiles

The app uses MapLibre with an **inline raster style and no API key**. Every
hosted vector-tile style (MapTiler, Stadia, Mapbox) requires an account, which
would put a vendor credential inside a digital public good and hand one company
a kill switch over a farming network.

`openstreetmap.org` tiles are a donated service whose usage policy forbids bulk
downloading and heavy automated use. That is acceptable for a pilot of tens of
farmers and **not** acceptable for a national rollout. A production node serves
its own basemap — self-hosted raster tiles or a PMTiles archive — by repointing
`BASEMAP_TILE_URL` in `mobile/src/constants/mapStyle.ts`.

Offline packs are scoped to one field plus ~500 m of context: 51 tiles, ~0.7 MB
at zoom 13-17. They are downloaded at field-creation time, while the phone still
has signal, because the next visit to that field almost certainly will not.

## Federation

An AgriN node is one deployable unit. A country runs its own; nodes exchange
**models and aggregate statistics, never farmer data**. That is both the
interoperability story and the data-sovereignty answer.

```bash
curl localhost:8099/federation/.well-known/agrin-node   # identity, public key, data policy
curl localhost:8099/federation/vocabulary               # AGROVOC crops, UCUM units
curl localhost:8099/federation/models                   # model cards (+ weights when trained)
curl localhost:8099/federation/aggregates               # signed, k-anonymised statistics
curl localhost:8099/federation/conformance              # what a node must implement
```

Three properties are enforced structurally rather than by policy:

**k-anonymity with complementary suppression.** A district/crop cell publishes
only with at least 5 distinct fields. Suppressing small cells while publishing a
total leaks them by subtraction, so the totals go too. Distinct *fields* are
counted, not observations — twenty readings from one field is still one farmer.

**No identifiers.** A payload is scanned for field ids, user ids, phone numbers
and geometry immediately before publication; a hit aborts the response with a
500 rather than leaking.

**Ed25519 signatures over canonical JSON.** `node_id`, `issued_at` and `licence`
are inside the signed bytes, so a payload cannot be replayed under another
node's name.

Verified live between two nodes with separate databases and separate keys
(`federation/demo.sh`, then `federation/demo.py`): node-br discovered node-in,
pinned its key, pulled its model cards and aggregates, verified the signature
against the **pinned** key, and rejected both a tampered payload and one replayed
under another node's name. A district with 6 rice fields published; a 2-field
wheat cell was suppressed; totals withheld; no identifiers crossed the wire.

The security property is that verification uses a key pinned at registration,
never one arriving with the payload — otherwise an attacker signs with their own
keypair and passes. See `federation/README.md`.

## Licence

Code Apache-2.0 (`LICENSE`), documentation and published data CC-BY-4.0.
Third-party data terms are in `NOTICE`. DPG Alliance self-assessment, including
what is *not* met, is in `docs/DPG.md`.
