# Audio capture feasibility — spike result (2026-07-12)

**Goal asked for:** continuous ambient audio recording from the watch →
summarize everything it hears → build a memory from it.

**Verdict: NOT feasible over the current data path. Blocked at the hardware/
protocol level, not by our code.** Do not build the pipeline against this watch;
the honest options are a different capture device or a phone-side app.

## Why

Our link to the Redmi Watch 5 Active is **Bluetooth Classic SPP** (Serial Port
Profile) carrying encrypted **protobuf command frames** — the Gadgetbridge
Xiaomi port. That channel moves small structured messages, not media.

1. **The protocol has no audio surface.** Every command type the port
   implements is enumerated in `core/client.py`:
   `1` auth · `2` system (battery/find/device-state) · `7` notification ·
   `8` health (HR/steps/sleep/sync) · `17` schedule (alarms) · `22` data-upload
   (icon bytes only). There is no voice/mic/stream type, and none exists to add
   — SPP is the wrong transport for a continuous PCM/Opus stream anyway.

2. **The watch mic is call-only (HFP), and tied to the phone.** The Redmi Watch
   5 Active exposes its microphone through the **Hands-Free Profile during an
   active Bluetooth call**, routed to the *paired phone* — not as an ambient
   stream a PC can subscribe to over SPP. HFP is a separate profile, negotiated
   per-call, and while the PC holds the SPP channel the phone isn't even master
   (our one-BT-channel constraint, CONVENTIONS §5).

3. **Gadgetbridge itself doesn't do this.** The upstream project we ported from
   has no ambient-audio capture for Xiaomi watches; there's nothing to port.

## What WOULD work (if audio-memory is still wanted)

The summarize-everything-you-hear + memory idea is sound; it just can't source
audio from *this watch*. Realistic sources, cheapest first:

- **Phone-side capture.** A phone app/record widget → drop files in a watched
  folder → our pipeline transcribes + summarizes + writes memory. The watch
  stays out of the audio path entirely.
- **A dedicated always-on recorder** (cheap BLE/USB lav or a pocket recorder)
  feeding the same watched folder.
- The **PC mic** when at the desk, same folder contract.

In every case the watch's role is at most a *trigger* (a button press → notify
the PC to start/stop), which our watch_io/notification path already supports.

## Decision

Park the watch-audio feature. If we build audio-memory later, design it around a
**watched-folder ingest contract** (`features/audio/` = transcribe → summarize →
memory) that is source-agnostic, and wire whatever capture device is chosen into
that folder. Revisit only if the watch ever exposes a real audio profile.
