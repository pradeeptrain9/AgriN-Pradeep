"""Drive the two-node federation exchange and print what actually happened.

Run federation/demo.sh first to bring both nodes up.

What this proves, in order:
  1. two nodes exist with DIFFERENT signing identities
  2. node-br discovers node-in and pins its public key
  3. node-br pulls model cards, rejecting any without stated limitations
  4. node-br pulls aggregates and verifies them against the PINNED key
  5. a tampered aggregate is rejected
  6. a payload replayed under another node's name is rejected
  7. neither node ever exposes a farmer record
"""

import json
import sys
import urllib.error
import urllib.request

IN = "http://127.0.0.1:8099"
BR = "http://127.0.0.1:8100"


def call(method: str, url: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> int:
    rule("1. Two nodes, two identities")
    _, a = call("GET", f"{IN}/federation/.well-known/agrin-node")
    _, b = call("GET", f"{BR}/federation/.well-known/agrin-node")
    print(f"  {a['node_id']:<8} ({a['country']})  key {a['public_key'][:28]}...")
    print(f"  {b['node_id']:<8} ({b['country']})  key {b['public_key'][:28]}...")
    if a["public_key"] == b["public_key"]:
        print("  FAIL: both nodes share a key; this is not a federation")
        return 1
    print("  keys differ: these are genuinely separate nodes")

    rule("2. node-br discovers node-in and pins its key")
    status, pinned = call("POST", f"{BR}/federation/peers", {"base_url": IN})
    if status != 201:
        print(f"  FAIL: {pinned}")
        return 1
    print(f"  pinned {pinned['peer']['node_id']} ({pinned['peer']['country']})")
    print(f"  trusted: {pinned['trusted']}   warnings: {pinned['warnings'] or 'none'}")
    print(f"  peer declares it shares raw farmer data: "
          f"{pinned['peer']['shares_raw_farmer_data']}")
    print(f"  peer declares minimum k-anonymity: {pinned['peer']['minimum_k_anonymity']}")

    rule("3-4. node-br pulls models and aggregates, verifying against the pinned key")
    status, pull = call("POST", f"{BR}/federation/peers/node-in/pull")
    if status != 200:
        print(f"  FAIL: {pull}")
        return 1
    print(f"  models received: {len(pull['models'])}")
    for model in pull["models"]:
        print(f"    {model['model_id']} v{model['version']}  "
              f"accuracy {model['reported_accuracy']}  "
              f"weights {'yes' if model['weights_available'] else 'card only'}")
        print(f"      limitation: {model['limitations'][0][:64]}")
    if pull["rejected"]:
        print(f"  rejected: {pull['rejected']}")

    print(f"\n  signature verified against pinned key: {pull['signature_verified']}")
    agg = pull.get("aggregates") or {}
    cells = agg.get("cells", [])
    print(f"  aggregate cells received: {len(cells)}")
    for cell in cells:
        print(f"    {cell['district']} / {cell['crop_code']}: "
              f"{cell['field_count']} fields, mean {cell['mean']}")
    print(f"  cells suppressed by k-anonymity: {agg.get('suppressed_cell_count')}")
    print(f"  totals withheld: {agg.get('totals_suppressed')}")

    rule("5-6. Tampering and replay are rejected")
    sys.path.insert(0, "../backend")
    from app.federation.signing import open_envelope

    _, envelope = call("GET", f"{IN}/federation/aggregates")
    key = a["public_key"]
    print(f"  untouched envelope verifies      : {open_envelope(envelope, key)}")

    tampered = json.loads(json.dumps(envelope))
    if tampered["payload"].get("cells"):
        tampered["payload"]["cells"][0]["mean"] = 0.999
        print(f"  one number changed verifies      : {open_envelope(tampered, key)}")
    replayed = json.loads(json.dumps(envelope))
    replayed["node_id"] = "node-br"
    print(f"  replayed as another node verifies: {open_envelope(replayed, key)}")

    rule("7. No farmer data crosses the wire")
    rendered = json.dumps(pull)
    leaked = [k for k in ("field_id", "user_id", "phone", "geometry", "centroid",
                          "latitude", "longitude", "image_path")
              if k in rendered]
    print(f"  identifiers found in everything node-br received: {leaked or 'NONE'}")

    _, conformance = call("GET", f"{IN}/federation/conformance")
    print(f"\n  node-in publishes {len(conformance['required_behaviours'])} required "
          f"behaviours and {len(conformance['prohibited'])} prohibitions,")
    print("  so conformance can be checked over the wire rather than by reading a doc.")

    print(f"\n{'=' * 70}\nDemo complete.\n{'=' * 70}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
