# Walking a real field with a real phone

This is the one path that cannot be verified any other way. An emulator serves
perfect 3-6 m GPS fixes the instant they are asked for. A real walk has drift,
canopy shadow, a phone in a pocket, a screen that sleeps, and someone who stops
to talk halfway down the boundary.

`docs/PILOT.md` requires this run before anyone is enrolled.

## Why the phone talks to the node over USB

The app refuses plain HTTP to anything but a loopback address, and Android
enforces the same rule independently through the network security config. That
is deliberate -- farm data should not cross a village wifi in clear text -- and
it means you cannot simply point the phone at `http://192.168.1.x:8099`.

`adb reverse` gives the phone a real loopback route to your machine over the USB
cable, so nothing has to be loosened. The catch is that the tunnel dies when you
unplug, which is fine: the walk itself is meant to happen with no connection.

## Before you leave the desk

**1. Put the phone in developer mode.** Settings → About phone → tap *Build
number* seven times. Then Settings → System → Developer options → enable *USB
debugging*.

**2. Plug it in and authorise the computer.** A dialog appears on the phone the
first time; tick "always allow".

```bash
adb devices
```

You want one line ending in `device`. `unauthorized` means the dialog was not
accepted yet.

**3. Install the right build.** Check what the phone is:

```bash
adb shell getprop ro.product.cpu.abi
```

`arm64-v8a` for almost any phone made since about 2016, `armeabi-v7a` for older
budget hardware. Install the matching APK:

```bash
adb install -r mobile/android/app/build/outputs/apk/release/app-arm64-v8a-release.apk
```

**4. Start the node and open the tunnel.**

```bash
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8099
```

```bash
adb reverse tcp:8099 tcp:8099
```

**Re-run the `adb reverse` line every time you replug the phone.** It does not
survive a disconnect, and its absence looks exactly like the node being down.

**5. Point the app at the node.** Open AgriN → *Change* on the sign-in screen →
enter `http://localhost:8099` → Save.

**6. Sign in.** Enter the phone number. In development the code is not sent by
SMS -- it is printed by the node. Read it from the terminal running uvicorn, or:

```bash
grep -o '"dev_code":"[0-9]*"' /tmp/agrin_api.log | tail -1
```

**7. Accept the consent screen.** Read it as a farmer would; that text is the
thing being tested as much as the code.

**8. Prime the offline caches while you still have the tunnel.** Open any field,
then *Add your crop* / *Change crop*. This mirrors the crop registry onto the
phone. Skip it and the crop screen will dead-end in the field.

**9. Grant permissions properly.** Location must be **Precise** and at least
*While using the app*. Then find the battery settings for AgriN and set it to
**Unrestricted** / disable optimisation -- Android will otherwise kill the
foreground service mid-walk on many manufacturers' builds, which is exactly the
failure this test exists to catch.

## The walk

Unplug. Put the phone in aeroplane mode, or simply walk somewhere without
coverage -- both are the real condition.

1. **Map a new field** → **Start walking**.
2. Walk the boundary at a normal pace, phone in hand or pocket. Do not cut
   corners; the point is to see what the track looks like when a person walks it.
3. **Deliberately let the screen sleep for part of it.** That is what the
   foreground service and wake lock are for, and it is the most likely thing to
   be broken on a real device.
4. Stop for thirty seconds somewhere, as a farmer would. Watch whether the
   stationary points pile up or are filtered.
5. Return to your starting corner. The app says *"You are back at the start.
   Press Finish."* when it detects the loop closing.
6. **Finish** → name the field → **Save**. It must say *"Saved on your phone"*,
   not an error.
7. Still offline: open the field, **Add the crop**, pick the crop and sowing
   date, save. This is the step that dead-ended before the crop registry was
   mirrored.
8. If there is a diseased leaf to hand, photograph it. On-device diagnosis needs
   no signal.

## What to write down

The numbers matter more than "it worked":

| Observation | Why |
|---|---|
| GPS accuracy shown during the walk | The app rejects readings worse than 15 m. If a real field sits at 12-20 m, that threshold is wrong and points will be silently skipped. |
| "N weak readings skipped" count | Same question, from the other side. |
| Points recorded, and metres walked | Against the real perimeter, if it is known. |
| Area the app computed | Against the known area of the plot. This is the number a farmer's fertiliser dose is multiplied by, so an error here scales into every recommendation. |
| Whether the screen sleeping interrupted tracking | The service and wake lock exist for exactly this. |
| Battery used over the walk | A boundary walk that costs 20% of a battery is not usable on a shared handset. |
| Whether anything crashed | Two crashes on this path were only found by running it. |

## Coming back

Plug the phone in, then:

```bash
adb reverse tcp:8099 tcp:8099
```

Open the app and go to the field list -- that screen is where the queue drains.
Within a few seconds the "waiting to be sent" line should disappear and the
field should lose its *Not sent yet* mark.

Check the node agrees, and that there is exactly **one** of it:

```bash
docker compose exec -T db psql -U agrin -d agrin -c \
  "select f.name, round(f.area_ha::numeric,2) ha, c.crop_code, c.sowing_date
     from fields f left join crop_cycles c on c.field_id = f.id
    order by f.created_at desc limit 5;"
```

A duplicated row means the outbox is replaying an item it never dequeued -- that
bug existed and was fixed, and this is how it showed itself.

## If something goes wrong

Get the crash before it scrolls away:

```bash
adb logcat -d | grep -A 30 "FATAL EXCEPTION" | tail -40
```

Both crashes found on this path so far were native Android issues invisible to
every test: a `PendingIntent` built in a way Android 14 forbids, and a missing
`WAKE_LOCK` permission. Neither produced a useful message in the app -- it
simply disappeared.
