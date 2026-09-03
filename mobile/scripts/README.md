# Boundary geometry verification

`verify-geo.ts` exercises the field-mapping maths without React Native, so it
runs in seconds and needs no emulator. It checks the things that would silently
corrupt a farmer's field boundary:

- ring closure and RFC 7946 counter-clockwise winding
- area agreement with the backend (PostGIS reports 2.342 ha for the fixture;
  the client must agree, or the farmer sees a different number after upload)
- self-intersection detection, checked **before** area — a bow-tie's shoelace
  area is meaningless, so ordering it the other way tells the farmer "field too
  small" when they actually crossed their own path
- the 0.1 ha floor, rejected on the phone rather than by a 422 after upload

Run it with only the turf packages installed:

```bash
npm install --no-save @turf/area @turf/helpers @turf/simplify @turf/boolean-clockwise tsx typescript
npx tsx scripts/verify-geo.ts
```


# Offline tile pack sizing

`verify-tiles.ts` checks the bounding box and tile arithmetic behind offline
basemap packs, without needing the MapLibre native module.

Measured for one 2.3 ha field at zoom 13-17: **51 tiles, ~0.7 MB**. The same
zoom range over a district is **87,536 tiles, ~1.3 GB** — about 1,700x larger.
That gap is the whole reason packs are scoped to a single field plus ~500 m of
context, rather than to a region: a district pack would fill a cheap phone and
would breach the OpenStreetMap tile usage policy, which forbids bulk downloading.

```bash
npx tsx scripts/verify-tiles.ts
```
