# Field pilot protocol

Everything in this repository has been verified on an emulator against synthetic
fields. Nothing has met a real farmer, a real boundary walked on foot, or a leaf
photographed in monsoon light. This is the plan for changing that without
harming anyone.

## Before any farmer is enrolled

```bash
curl localhost:8099/ready
```

`ready_for_pilot` must be true. It grades each dependency by what a farmer
experiences, not by internal state:

| check | if it fails |
|---|---|
| `secret_key` | **blocker** — anyone can forge a sign-in and read any farmer's fields |
| `sms_delivery` | **blocker** outside dev — nobody can sign in at all |
| `weather` | **blocker** if absent — no irrigation advice can be produced |
| `satellite` | degraded — crop health reports "not known"; everything else works |
| `cloud_diagnosis` | degraded — ~56% of leaf photos return "inconclusive" |
| `pesticide_allowlist` | degraded — cultural advice only, never a spray. **Safe default** |
| `districts` | degraded — fields collapse into one "unknown" bucket for aggregates |
| `node_identity` | back this table up; losing the key forces every peer to re-pin |

## Size and shape

Start at **15–25 farmers in one district, one crop, one season.** Small enough
that an extension officer can visit every one of them, which is the actual
safety mechanism during a pilot — not the software.

Choose a district where an extension officer already works. The app is designed
to route uncertain cases to a human; if there is no human, that design is a
dead end.

## What must be true for each farmer before enrolment

- [ ] They have been told, in their language, what data is collected, that it
      stays on this node, and that aggregates leave it but their own records
      never do
- [ ] They know the advice is a suggestion to check, not an instruction
- [ ] They know how to report that advice was wrong, and that a report of harm
      reaches a person. The prompt sits under every advisory and every
      diagnosis; it does not need to be found in a menu
- [ ] They have read the consent screen and ticked it themselves. The app will
      not let them past it, but a tick obtained by an officer tapping for them
      is not consent
- [ ] An extension officer's contact is available to them offline

## What to measure

Three questions, in order of importance.

**1. Did it harm anyone?** Every `harmful` feedback report is a pilot-stopping
event until investigated. This is not a metric to optimise; it is a tripwire.

```bash
curl -H "Authorization: Bearer <officer token>" localhost:8099/feedback/summary
```

**This has to be checked every working day, by a named person.** The app tells a
farmer who reports harm that an extension officer will contact them; if nobody
reads the queue, the app has lied to someone who just lost part of a crop. Write
the name in this file before enrolling anyone:

| Role | Name | Checks the queue |
|---|---|---|
| Pilot lead | _unassigned_ | every working day |
| Backup | _unassigned_ | when the lead is unavailable |

Contact the farmer within 48 hours, in person or by phone, before doing anything
else with the report. Then mark it handled. Who acknowledged it is taken from the
token, not typed in, so the record cannot be attributed to someone else, and the
endpoint requires an `extension` or `admin` role:

```bash
curl -X POST -H "Authorization: Bearer <officer token>" \
  localhost:8099/feedback/<id>/acknowledge
```

Acknowledging twice returns 404 rather than overwriting, so a second officer
cannot silently take credit for the first one's follow-up.

An unacknowledged `harmful` report older than 48 hours means the pilot pauses.

**2. Was it right?** The disease model has **no cross-collection accuracy
estimate** — both evaluation sets share collections with training. Farmer
corrections are the first honest measurement this project will have. Target at
least 50 corrected diagnoses before drawing any conclusion.

**Before the first field day:** walk one real boundary on real hardware with the
phone in aeroplane mode, then restore signal and confirm the field, its crop and
any photo reach the node. This is the one path that could not be exercised on the
emulator, whose GPS does not deliver fixes, and it is the path every farmer in a
low-signal village takes.

**3. Was it usable?** Not "did they tap the button", but: could they walk a
boundary unaided; did they understand the advisory without an officer reading it
to them; did the phone survive a day in the field.

## What NOT to measure in a first pilot

Yield. A single season on 20 fields with no control group cannot attribute a
yield change to the advice, and claiming otherwise would be dishonest. Measure
whether the advice was correct and usable; leave yield to a designed trial.

## Known limits to state up front

- Crop health is blank until a cloud-free Sentinel-2 pass. In monsoon that can
  be **three weeks**. The app says so; make sure farmers hear it too.
- The disease model covers **rice only**, answers about 44% of photos, and the
  rest need connectivity.
- No chemical advice appears until an agronomist verifies allowlist rows against
  the national register.
- Open-Meteo's free tier is **non-commercial**. A charged service needs their
  paid tier.
- The public OpenStreetMap tile service is for a pilot of this size only, not a
  rollout.

## Stopping conditions

Stop and review if any of these occur:

- one `harmful` report
- three `wrong` diagnoses on the same disease class, which suggests a systematic
  model error rather than bad luck
- any farmer sprays on advice the system should not have given
- crop health shown as healthy while a field is visibly failing

## After the pilot

The corrections collected are the most valuable output — more than any usage
metric. They are locally-representative ground truth, which no public dataset
provides, and they are what a genuine cross-collection accuracy number and a
retrained model both depend on.
