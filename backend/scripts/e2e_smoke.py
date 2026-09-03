"""End-to-end walk: OTP -> field -> crop -> soil card -> ingest -> advisory."""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8099"

def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

# wait for server
for _ in range(40):
    try:
        s, h = call("GET", "/health")
        if s == 200: break
    except Exception: pass
    time.sleep(0.5)
print("health:", json.dumps(h))
assert h["database"] == "up", h

PHONE = "+919876500011"
s, r = call("POST", "/auth/otp/request", {"phone": PHONE})
print("otp request:", s, {k: v for k, v in r.items() if k != "dev_code"})
code = r["dev_code"]

s, r = call("POST", "/auth/otp/verify", {"phone": PHONE, "code": code})
print("otp verify:", s, "new_user =", r["is_new_user"])
token = r["access_token"]

s, r = call("GET", "/fields", token=token)
print("fields before:", s, len(r))

# ~2.2 ha rectangle near Ludhiana, Punjab
poly = {"type": "Polygon", "coordinates": [[
    [75.8500, 30.9000], [75.8517, 30.9000], [75.8517, 30.9013],
    [75.8500, 30.9013], [75.8500, 30.9000]]]}
s, field = call("POST", "/fields", {"name": "North plot", "geometry": poly}, token=token)
print("create field:", s, f'{field["area_ha"]} ha', "centroid", [round(c,4) for c in field["centroid"]])
fid = field["id"]

# reject a too-small field
tiny = {"type": "Polygon", "coordinates": [[
    [75.85, 30.90], [75.8502, 30.90], [75.8502, 30.9002], [75.85, 30.9002], [75.85, 30.90]]]}
s, r = call("POST", "/fields", {"name": "tiny", "geometry": tiny}, token=token)
print("reject tiny field:", s, r.get("detail", "")[:80])

s, r = call("POST", f"/fields/{fid}/crop",
            {"crop_code": "wheat", "sowing_date": "2026-06-15", "previous_crop": "soybean"}, token=token)
print("set crop:", s, r["label"], "season", r["expected_season_days"], "days")

s, r = call("PUT", f"/fields/{fid}/soil/card", {
    "ph": 7.9, "organic_carbon_pct": 0.38, "available_n_kg_ha": 245.0,
    "available_p_kg_ha": 12.0, "available_k_kg_ha": 190.0, "texture_hint": "loam"}, token=token)
print("soil card:", s, r["source"], r["confidence"], "texture", r["texture"])

s, r = call("POST", f"/fields/{fid}/refresh", token=token)
print("refresh queued:", s)

# advisory before ingest completes
s, adv = call("GET", f"/fields/{fid}/advisory", token=token)
print("advisory (pre-ingest):", s, "status", adv["status"], "| gaps:", len(adv["gaps"]))

print("\nwaiting for weather ingest...")
for i in range(60):
    time.sleep(2)
    s, adv = call("GET", f"/fields/{fid}/advisory", token=token)
    if adv.get("irrigation"): break
print(f"weather days available: {adv['climate']['weather_days_available']}")

print("\n===================== ADVISORY =====================")
print("crop      :", adv["crop"]["label"], "| stage", adv["crop"]["stage"], "| DAS", adv["crop"]["days_after_sowing"])
print("soil      :", adv["soil"]["source"], f"({adv['soil']['confidence']})", "|", adv["soil"]["texture"])
h = adv["health"]
print("health    :", h["severity"], "| ndvi", h["latest_ndvi"], "| obs", h["observations_used"])
irr = adv["irrigation"]
if irr:
    print("irrigation: irrigate_now =", irr["irrigate_now"],
          "| net", irr["recommended_depth_mm"], "mm | gross", irr["gross_depth_mm"], "mm")
    print("            soil moisture", irr["soil_moisture_pct"], "% | TAW", irr["taw_mm"],
          "mm | RAW", irr["raw_mm"], "mm")
    print("            next irrigation:", irr["forecast_irrigation_date"], f"(in {irr['days_until_irrigation']} d)")
    print("            rain next 7d:", irr["rainfall_next_7d_mm"], "mm | stress days/30:", irr["stress_days_last_30"])
    for n in irr["notes"]: print("            *", n)
n = adv["nutrients"]
print("nutrients : N", n["n"]["low_kg_ha"], "-", n["n"]["high_kg_ha"], "kg/ha",
      "| P2O5", n["p2o5"]["low_kg_ha"], "-", n["p2o5"]["high_kg_ha"],
      "| K2O", n["k2o"]["low_kg_ha"], "-", n["k2o"]["high_kg_ha"])
print("            fertility class:", n["fertility_class"], "| N credits", n["n_credits_kg_ha"], "kg/ha", n["credit_breakdown"])
print("            products kg/ha:", n["products_kg_ha"])
for sp in n["splits"]: print(f"            split @DAS{sp['days_after_sowing']:>3}: N {sp['n_kg_ha']} kg/ha  ({sp['when']})")
print("            regenerative:")
for a in n["regenerative_actions"]: print("              -", a)
print("rotation  :")
for c in adv["rotation"]:
    print(f"   {c['score']:.3f}  {c['label']:<22} water {c['seasonal_water_need_mm']:.0f} mm  {c['components']}")
print("climate   :", adv["climate"])
print("gaps      :")
for g in adv["gaps"]: print("   -", g)

s, q = call("GET", "/quota", token=token)
print("\nquota:", q)
