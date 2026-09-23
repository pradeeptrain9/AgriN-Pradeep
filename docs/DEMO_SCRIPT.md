# AgriN — demo video script

Target: **3 minutes**. Everything below has been run end to end on the live
node; nothing here is a mock-up or a claim the recording cannot show.

Two rules while recording:

1. **Never say a number the screen does not show.** The whole argument of this
   project is that figures trace to something. Breaking that in the video is
   the one unforced error available to you.
2. **If something degrades on camera, say so and keep going.** A node that
   admits it is missing an SMS provider is the point, not an embarrassment.

---

## Before you press record

Run these in order. The last one matters most: the node sleeps on free
hosting, and the first request after idle takes **30–60 seconds**.

```bash
curl -s https://agrin-node-in.onrender.com/ready | head -c 200
```

- [ ] Node awake (the command above returned JSON, not a hang)
- [ ] Emulator running, AgriN installed, signed in, sitting on **My fields**
- [ ] Phone/emulator on Wi-Fi, notifications silenced, screen not rotating
- [ ] Browser tabs pre-opened, no other tabs, bookmarks bar hidden:
  - `https://agrin-node-in.onrender.com/ready`
  - `https://agrin-node-in.onrender.com/.well-known/agrin-node`
  - `https://github.com/pradeeptrain9/AgriN-Pradeep`
- [ ] Terminal open in the repo, font size up (18pt+), window ~100 columns
- [ ] `node-br` running for the federation beat (see step 5)

---

## The script

Timings are targets, not a straitjacket. Read at a normal pace — do not rush
to fit; cut a beat instead.

### 0:00–0:20 · The problem, in one breath

> **[On screen: the app's My fields screen]**
>
> "A farmer with two hectares of rice gets advice from a shop that sells
> pesticide, or from a broadcast written for a whole district. AgriN gives
> advice for *their* field, from satellite, soil and weather — and it never
> invents a number.
>
> It's a digital public good: Apache-2.0, one node per state, and states share
> models and statistics, never farmer data."

### 0:20–0:55 · A real field, real data

> **[Tap the field. Let the detail screen load.]**
>
> "This field was drawn on Google satellite imagery — the app says so, because
> a drawn boundary is an estimate and the area multiplies every per-hectare
> figure below it.
>
> **[Point at the green summary card]**
>
> This sentence is written by Gemini. Every figure in it was computed first by
> a deterministic engine — FAO-56 Penman-Monteith for water, crop-specific
> nitrogen schedules for fertiliser. The model is allowed to rephrase. It is
> not allowed to do arithmetic.
>
> **[Scroll to Weather]**
>
> Real forecast. Shaded days were measured, the rest is forecast, and the
> screen says which — a farmer must not be shown a forecast as though it were
> a measurement."

### 0:55–1:35 · The safety chain — the strongest beat

> **[Tap Check a leaf → Choose → pick the leaf-in-hand photo]**
>
> "The phone runs its own model first. No network."
>
> **[Result appears]**
>
> "Rice blast, 71% confident, checked on your phone. That photograph never
> left the device.
>
> **[Point at the actions]**
>
> And notice what it does *not* say. No chemical, no dose. This node's
> pesticide table ships empty, and advice only serves from rows an agronomist
> has verified against the registered product label. Until someone does that,
> a farmer gets cultural and preventive advice and nothing else.
>
> That is deliberate. A wrong dose costs a farmer a season."

*(Optional, if you have a spare 15 seconds — this is the single best moment in
the demo. Use `zz-forces-cloud.jpg`.)*

> "Here's a healthy plant. The on-device model wants to call it hispa, at 0.66
> confidence. The gate refuses it — the entropy is too high — and escalates to
> Gemini, which answers: healthy leaf. The small model was about to tell a
> farmer their healthy crop was infested. The gate stopped it."

### 1:35–2:05 · It grades itself

> **[Browser: /ready]**
>
> "The node publishes its own honest self-assessment. Every check names the
> consequence for a farmer, not an internal state.
>
> It reports **two blockers**. There is no SMS provider, so no farmer can sign
> in yet. And the fixed demo code that let *you* sign in — it refuses to call
> that acceptable now that the node holds someone else's data.
>
> It says both in public, rather than waiting to be found out."

### 2:05–2:40 · Federation — states sharing, live

> **[Terminal]**
>
> ```
> AGRIN_PEER_URL=https://agrin-node-in.onrender.com \
>   backend/.venv/bin/python federation/demo.py
> ```
>
> "This is a second node — Brazil, its own database, its own signing key —
> pulling from the deployed India node over the internet.
>
> **[As output scrolls]**
>
> It discovers the node, pins its public key, and verifies every payload
> against the key it pinned — never against a key that arrives with the
> payload, or the signature proves nothing.
>
> It pulls three model cards, each with stated limitations, including one
> marked *not fit for deployment* so a peer doesn't repeat a failed experiment.
>
> Aggregates: five cells suppressed by k-anonymity, nothing published. That's
> correct for a node with five fields. Replay under another node's name is
> rejected, and no identifier crosses the wire."

### 2:40–3:00 · Close

> **[GitHub repo]**
>
> "Apache-2.0, CC-BY-4.0 for data. AGROVOC crop codes, UCUM units, GeoJSON,
> Ed25519 signatures — boring standards on purpose, so a second implementation
> can interoperate without asking us.
>
> 797 tests. One live node. The next state to join needs one environment
> variable: their own ISO code."

---

## Recording, step by step

### Option A — one screen, phone mirrored (recommended)

**1. Mirror the emulator into a window you can place.**
The Android emulator is already a window on your Mac. Resize it to roughly a
third of the screen, left side. Put the browser and terminal on the right.

**2. Record with QuickTime (built in, no install).**

- QuickTime Player → File → **New Screen Recording**
- Click the arrow next to the record button → **Microphone: MacBook
  Microphone** (or a headset — much better)
- Click **Record**, drag a box around the region you want, or record the whole
  screen
- Stop with **⌘⌃Esc**, or the stop icon in the menu bar

**3. Better audio, free:** record in a small room with soft furnishings, mouth
about a hand's width from the mic, and do a 10-second test first. Listen back
before you record the real thing. Bad audio loses more viewers than bad video.

### Option B — device-only recording (sharper phone footage)

`adb` records the emulator screen directly at full resolution, with a hard
**180-second cap** — which is why the script targets three minutes.

```bash
adb shell screenrecord --bit-rate 8000000 /sdcard/agrin.mp4
```

Press **Ctrl-C** to stop, then:

```bash
adb pull /sdcard/agrin.mp4 ~/Desktop/agrin-phone.mp4
```

No audio. You'd narrate over it afterwards, which is more editing work. Use
Option A unless the phone footage quality really matters.

### 4. Trim and export

QuickTime does enough: **Edit → Trim** (⌘T), drag the yellow handles, then
**File → Export As → 1080p**.

If you want titles or to splice two takes, iMovie is free and already
installed — drag the clip in, cut with ⌘B.

### 5. Upload

- YouTube → Create → Upload video
- Visibility: **Unlisted** — anyone with the link can watch, it will not appear
  in search
- Title: `AgriN — interoperable digital agriculture network (demo)`
- Paste the description below
- Copy the link into the submission form

**Description to paste:**

```
AgriN is an interoperable digital agriculture network: satellite, soil and
weather driven agro-advisories, on-device crop disease diagnosis with a cloud
fallback, and federated nodes that share models and k-anonymised statistics —
never farmer data.

Live node:  https://agrin-node-in.onrender.com/ready
Code:       https://github.com/pradeeptrain9/AgriN-Pradeep
Licence:    Apache-2.0 (code), CC-BY-4.0 (data)

Every figure a farmer sees is computed by a deterministic engine — FAO-56
Penman-Monteith for irrigation, crop-specific schedules for fertiliser. Gemini
rephrases them into plain language and is blocked from introducing any number
the engine did not produce.
```

---

## If something goes wrong on camera

| What happens | What to say |
|---|---|
| First tap takes ~40 s | "Free hosting — the node sleeps when idle. A farmer's node wouldn't." |
| Advice appears but reads flat | The model fell back to the template. Say: "The language model is unavailable, so it served the deterministic wording. Same numbers." |
| Crop health says Not known | "No cloud-free satellite image since sowing. It says so rather than guessing." |
| Diagnosis says inconclusive | That is the gate working. "It refused to guess and told me to ask an extension officer." |

None of these need a re-take. A system that degrades honestly *on camera* is
worth more than one that needs everything to go right.

---

## Setting up node-br for the federation beat

Once, before recording:

```bash
docker compose up -d db
```

```bash
cd backend && AGRIN_ENV=dev NODE_ID=node-br NODE_COUNTRY=BR DATABASE_URL="postgresql+asyncpg://agrin:agrin@localhost:5433/agrin_br" .venv/bin/python -m app.db.migrate
```

```bash
cd backend && AGRIN_ENV=dev NODE_ID=node-br NODE_COUNTRY=BR DATABASE_URL="postgresql+asyncpg://agrin:agrin@localhost:5433/agrin_br" .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100
```

Leave that running in its own tab. Then the demo line in the script works.

If it reports a pinned key that has changed, that is the node refusing a
stale pin — re-pin deliberately:

```bash
curl -s -X POST http://127.0.0.1:8100/federation/peers -H 'Content-Type: application/json' -d '{"base_url":"https://agrin-node-in.onrender.com","accept_key_change":true}'
```
