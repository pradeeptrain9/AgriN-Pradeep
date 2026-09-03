# Digital Public Good compliance

Assessed against the [DPG Alliance's nine indicators](https://digitalpublicgoods.net/standard/).
Honest status, including what is not met yet.

| # | Indicator | Status | Evidence |
|---|---|---|---|
| 1 | Relevance to an SDG | **Met** | SDG 2 (Zero Hunger), targets 2.3 and 2.4. Also SDG 6.4 via the AWD water model and SDG 13 via reduced methane and nitrogen loss |
| 2 | Open licence | **Met** | Code Apache-2.0, documentation and published data CC-BY-4.0, declared in every node descriptor and signed envelope |
| 3 | Clear ownership | **Met** | `NOTICE` records copyright and contribution terms |
| 4 | Platform independence | **Met** | No proprietary dependency. Postgres/PostGIS, Python, React Native, MapLibre. No Mapbox token, no hosted vector-tile key, no managed cloud service |
| 5 | Documentation | **Met** | `README.md`, `docs/DEPLOYMENT.md`, `training/README.md`, `federation/README.md`, `docs/DPG.md`, plus `/federation/conformance` served by the node itself |
| 6 | Mechanism for data extraction | **Met** | `GET /fields`, `/fields/{id}/advisory`, `/diagnoses` return a farmer's own data as JSON. `/federation/aggregates` exports node-level statistics |
| 7 | Privacy and applicable laws | **Partial** | k-anonymity enforced structurally, EXIF/GPS stripped from uploads, raw farmer data never federated, cleartext HTTP refused for non-loopback hosts. A DPDP Act (India) and LGPD (Brazil) review has not been done, and there is no data-retention policy yet |
| 8 | Standards and best practices | **Met** | AGROVOC crop concepts, UCUM units, GeoJSON RFC 7946, RFC 3339 timestamps, OpenAPI 3.1, Ed25519 signatures |
| 9 | Do no harm by design | **Partial** | See below. A grievance mechanism now exists (`/feedback`, harm reports never blocked and surfaced for human triage); independent agronomic review and a DPIA are still outstanding |

## Indicator 9 in detail

Specific harms this system could cause, and what structurally prevents each.

**Wrong agronomic advice destroying a season.** Every quantity is computed
deterministically and unit-tested against FAO-56 worked examples. A language
model may only rephrase those numbers, enforced mechanically by `ai/guard.py`,
which rejects any narration containing a figure absent from the computed payload.

**Pesticide misuse.** Chemical recommendations are served only from rows marked
`verified` against a national register, and the table ships empty, so an
unverified deployment gives cultural advice only. A model can never introduce a
product name; dose and pre-harvest interval always come from the verified row.
Bacterial and viral diagnoses return no chemical at all.

**False confidence from a machine-learning model.** Crop coverage gates the
on-device classifier before confidence does, because a softmax always sums to one
and an untrained crop still produces a confident label. Model cards must state
field accuracy, not just in-domain accuracy.

**Stale data presented as current.** Cloud-masked satellite gaps are reported as
gaps. A field whose last clear image is old is labelled stale and downgraded from
`ok` to `unknown` rather than shown as healthy.

**Surveillance of farmers.** Nodes exchange aggregates, never records.
k-anonymity is enforced with complementary suppression, and a publish is aborted
if an identifier is detected in the payload.

**Grievance mechanism.** `/feedback` lets a farmer mark advice as helpful,
unclear, wrong or harmful, and the prompt is rendered directly beneath the
advisory and beneath a diagnosis -- an API a farmer cannot reach is not a
grievance mechanism. A report of harm is never refused for a missing field or
diagnosis reference: requiring someone to first identify which field is exactly
the friction that stops the reports that matter most from arriving. `helpful`
and `unclear` submit on one tap; `wrong` and `harmful` open a free-text box,
because those are the ones worth a sentence. Reports are surfaced for human
triage at `GET /feedback/summary` rather than counted in a chart, and the
confirmation shown to a farmer who reports harm promises that an extension
officer will make contact -- a promise `docs/PILOT.md` binds the pilot operator
to keep. Corrections also supply ground-truth labels from real fields, which is
the only locally-representative evaluation data a node can obtain.

**Consent.** Enforced in the app, not just documented. A signed-in account with
no recorded consent has exactly one route available to it, the consent screen;
there is no path into fields, advisories or the camera until the farmer has
ticked an unticked box. The screen names the specific node their data will sit
on, lists what is kept and what leaves the node, and states plainly that the
advice is a suggestion to check rather than an instruction to follow. Consent is
cleared on sign-out, because these handsets are shared.

**Still not addressed:** no independent agronomic review of the recommendation
logic, and no formal DPIA. Both need people this project does not yet have, and
neither is a coding task.

## Federation, concretely

An AgriN node is one deployable unit. A country runs its own; nodes exchange
models and aggregate statistics, never farmer data. Conformance is served by the
node itself at `/federation/conformance` so it can be checked over the wire.

```bash
curl https://node-in.example/federation/.well-known/agrin-node   # identity, keys, policy
curl https://node-in.example/federation/vocabulary               # AGROVOC + UCUM terms
curl https://node-in.example/federation/models                   # model cards
curl https://node-in.example/federation/aggregates               # signed, k-anonymised
```

Every published payload is Ed25519-signed over a canonical JSON encoding, with
`node_id`, `issued_at` and `licence` inside the signed bytes so a payload cannot
be replayed under another node's name.
