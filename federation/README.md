# Two-node federation demo

Two AgriN nodes — `node-in` (India) and `node-br` (Brazil) — with separate
databases and separate Ed25519 identities, exchanging models and statistics.

```bash
./federation/demo.sh                        # start both nodes
backend/.venv/bin/python federation/demo.py # run the exchange
```

Containerised equivalent, for someone without the Python environment:

```bash
docker compose -f federation/demo.yml up --build
```

## What it demonstrates

| # | Property | How |
|---|---|---|
| 1 | Genuinely separate nodes | Different databases, different Ed25519 keys generated independently on first start |
| 2 | Trust is pinned, once | `POST /federation/peers` records the key the peer presents at registration |
| 3 | Models cross borders | node-br pulls node-in's model cards; a card without stated limitations is rejected |
| 4 | Statistics are verified | Aggregates are checked against the **pinned** key, not one supplied with the payload |
| 5 | Tampering fails | Changing a single number invalidates the signature |
| 6 | Replay fails | `node_id` is inside the signed bytes, so a payload cannot be re-attributed |
| 7 | No farmer data moves | Everything node-br receives is scanned for identifiers; none present |

## The security property that matters

Verification uses a key **pinned at registration**, never one that arrives with
the payload. Verifying against a key fetched in the same request proves only
that the sender can sign — which any attacker can do with their own keypair.
`tests/federation/test_client.py` encodes exactly this: a forged envelope
verifies against the attacker's own key and fails against the pinned one.

This is trust-on-first-use, and the code says so rather than dressing it up. The
pin is only as good as that first contact; a production deployment confirms the
key out of band before registering. A peer rotating keys surfaces as a
verification failure rather than being silently absorbed — which is the intended
behaviour, not a bug.

## Policy gate before signatures

A peer that declares `shares_raw_farmer_data: true`, or a k-anonymity threshold
below 5, is refused before any payload is ingested. A valid signature does not
make raw farmer data acceptable to receive.

## Observed output

Running against the live nodes, node-br received node-in's rice disease model
card carrying the limitation *"NOT FIT FOR DEPLOYMENT. Cross-dataset accuracy is
below chance."* That is the model registry working: a peer sees an honest
negative result before deciding whether to adopt, and nobody repeats the
experiment.

Aggregates arrived as one published cell (Ludhiana / rice, 6 fields) with one
cell suppressed for falling under k=5 and group totals withheld, because a total
published beside a suppressed cell would let the suppressed value be recovered by
subtraction.
