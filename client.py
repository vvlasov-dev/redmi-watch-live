"""Redmi Watch 5 Active -> Windows live client (over Bluetooth Classic SPP).

Port of the Gadgetbridge Xiaomi protocol (AGPLv3) to a standalone Windows client.
Connects to the watch's serial port, performs the encrypted handshake, enables
real-time stats and prints / logs / serves live heart rate + steps.

Requires:  pip install pyserial pycryptodome
You MUST supply the watch's 16-byte auth key (see README) and the COM port.

Usage:
    python client.py --list
    python client.py --port COM8 --auth-key 0123...ef  [--csv live.csv] [--serve 8765] [--debug]
"""
import argparse
import json
import sys
import threading
import time

import activity
import miniproto as mp
import spp
from xcrypto import XiaomiAuth

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None


def log(msg, *a):
    print(time.strftime("%H:%M:%S"), msg % a if a else msg, flush=True)


# ---- command builders (type/subtype from Gadgetbridge XiaomiHealthService/SystemService) ----
def build_enable_realtime() -> bytes:
    # Command{ type=8 (Health), subtype=45 (REALTIME_STATS_START) }
    return mp.f_varint(1, 8) + mp.f_varint(2, 45)


def build_disable_realtime() -> bytes:
    return mp.f_varint(1, 8) + mp.f_varint(2, 46)


def build_battery_query() -> bytes:
    # Command{ type=2 (System), subtype=1 (CMD_BATTERY) }
    return mp.f_varint(1, 2) + mp.f_varint(2, 1)


def build_hr_config_get() -> bytes:
    # Command{ type=8 (Health), subtype=10 (CMD_CONFIG_HEART_RATE_GET) }
    return mp.f_varint(1, 8) + mp.f_varint(2, 10)


def build_hr_config_set(cfg) -> bytes:
    # Command{ type=8, subtype=11, health.heartRate=HeartRate{...} } — send the
    # FULL message (partial sets reset omitted fields). advancedMonitoring.enabled
    # is the watch's sleep-stage (REM/deep) detection toggle (GB pref sleepDetection).
    hr = b""
    hr += mp.f_varint(1, 1 if cfg.get("disabled") else 0)          # disabled
    hr += mp.f_varint(2, int(cfg.get("interval", 1)))               # interval 0 smart/1/10/30
    if cfg.get("alarm_high_thr"):
        hr += mp.f_varint(3, 1 if cfg.get("alarm_high_en") else 0)
        hr += mp.f_varint(4, int(cfg["alarm_high_thr"]))
    hr += mp.f_message(5, mp.f_varint(1, 1 if cfg.get("advanced") else 0))   # AdvancedMonitoring.enabled
    hr += mp.f_varint(7, 1)                                          # unknown7=1
    if "alarm_low_thr" in cfg and cfg.get("alarm_low_thr"):
        hr += mp.f_message(8, mp.f_varint(1, 1 if cfg.get("alarm_low_en") else 0)
                              + mp.f_varint(2, int(cfg["alarm_low_thr"])))
    hr += mp.f_varint(9, int(cfg.get("breathing", 1)))              # breathingScore 1 on/2 off
    health = mp.f_message(8, hr)                                     # Health.heartRate
    return mp.f_varint(1, 8) + mp.f_varint(2, 11) + mp.f_message(10, health)


def parse_hr_config(d) -> dict:
    health = mp.get1(d, 10, b"")
    hd = mp.decode(health) if health else {}
    hr = mp.get1(hd, 8, b"")
    h = mp.decode(hr) if hr else {}
    adv = mp.get1(h, 5)
    adv_en = None
    if adv is not None:
        adv_en = mp.get1(mp.decode(adv), 1) == 1
    return {
        "disabled": mp.get1(h, 1) == 1,
        "interval": mp.get1(h, 2),
        "advanced": adv_en,
        "breathing": mp.get1(h, 9),
    }


def build_device_state_get() -> bytes:
    # Command{ type=2 (System), subtype=78 (CMD_DEVICE_STATE_GET) }
    # watch replies (subtype 79) with System.deviceState{ sleepState, wearingState, ... }
    return mp.f_varint(1, 2) + mp.f_varint(2, 78)


def build_fetch_today() -> bytes:
    # Command{ type=8, subtype=1, health{ activitySyncRequestToday{ unknown1=0 } } }
    health = mp.f_message(5, mp.f_varint(1, 0))
    return mp.f_varint(1, 8) + mp.f_varint(2, 1) + mp.f_message(10, health)


def build_fetch_past() -> bytes:
    # Command{ type=8, subtype=2 }
    return mp.f_varint(1, 8) + mp.f_varint(2, 2)


def build_request_file(file_id_raw: bytes) -> bytes:
    # Command{ type=8, subtype=3, health{ activityRequestFileIds = fileId(7b) } }
    health = mp.f_bytes(2, file_id_raw)
    return mp.f_varint(1, 8) + mp.f_varint(2, 3) + mp.f_message(10, health)


def build_ack_file(file_id_raw: bytes) -> bytes:
    # Command{ type=8, subtype=5, health{ activitySyncAckFileIds = fileId(7b) } }
    health = mp.f_bytes(3, file_id_raw)
    return mp.f_varint(1, 8) + mp.f_varint(2, 5) + mp.f_message(10, health)


def build_notification(title: str, body: str, app_name: str = "Claude Code",
                       package: str = "ai.claude.code", nid: int = None) -> bytes:
    # Command{ type=7 (Notification), subtype=0 (SEND),
    #          notification.notification2.notification3{ package, appName, title, body, ts, id } }
    if nid is None:
        nid = int(time.time()) % 2000000000
    ts = time.strftime("%Y%m%dT%H%M%S")
    n3 = (mp.f_string(1, package) + mp.f_string(2, app_name) + mp.f_string(3, title)
          + mp.f_string(4, "") + mp.f_string(5, body) + mp.f_string(6, ts) + mp.f_varint(7, nid))
    notif = mp.f_message(3, mp.f_message(1, n3))   # Notification.notification2(3).notification3(1)
    return mp.f_varint(1, 7) + mp.f_varint(2, 0) + mp.f_message(9, notif)


def build_icon_reply(package: str) -> bytes:
    # Command{ type=7, subtype=15, notification.notificationIconReply(14){package} }
    # tells the watch "yes, I have an icon for this package"
    reply = mp.f_message(14, mp.f_string(1, package))
    return mp.f_varint(1, 7) + mp.f_varint(2, 15) + mp.f_message(9, reply)


def build_upload_request(utype: int, data: bytes) -> bytes:
    import hashlib
    # Command{ type=22, subtype=0, dataUpload.dataUploadRequest(1){type,md5,size} }
    req = mp.f_varint(1, utype) + mp.f_bytes(2, hashlib.md5(data).digest()) + mp.f_varint(3, len(data))
    return mp.f_varint(1, 22) + mp.f_varint(2, 0) + mp.f_message(24, mp.f_message(1, req))


def build_vibrate(start: bool = True) -> bytes:
    # Command{ type=2 (System), subtype=18 (FIND_WATCH), system.findDevice(5) }
    # start alert = 0, stop alert = 1 (per Gadgetbridge)
    return mp.f_varint(1, 2) + mp.f_varint(2, 18) + mp.f_message(4, mp.f_varint(5, 0 if start else 1))


def build_gentle_cue() -> bytes:
    # A silent, content-light notification: the watch gives a short buzz (its
    # notification vibration) without the loud, unstoppable find-device alert.
    # Ideal as a sleep / lucid-dream cue.
    return build_notification(" ", " ", app_name="Sleep")


def build_create_alarm(hour: int, minute: int, repeat_mode: int = 0,
                       repeat_flags: int = 0, enabled: bool = True, smart: int = 2) -> bytes:
    # Command{ type=17, subtype=1, schedule.createAlarm(2)=AlarmDetails }
    #   AlarmDetails{ time(HourMinute)=2, repeatMode=3, repeatFlags=4, enabled=5, smart=7 }
    #   repeatMode: 0 once, 1 daily, 5 weekly;  repeatFlags (weekly bitmask): bit0=Mon..bit6=Sun
    hm = mp.f_varint(1, hour & 0x1F) + mp.f_varint(2, minute & 0x3F)
    details = mp.f_message(2, hm) + mp.f_varint(3, repeat_mode)
    if repeat_mode == 5:
        details += mp.f_varint(4, repeat_flags)
    details += mp.f_varint(5, 1 if enabled else 0) + mp.f_varint(7, smart)
    schedule = mp.f_message(2, details)
    return mp.f_varint(1, 17) + mp.f_varint(2, 1) + mp.f_message(19, schedule)


def build_delete_alarms(ids) -> bytes:
    # Command{ type=17, subtype=4, schedule.deleteAlarm(5)=AlarmDelete{ id(1) repeated } }
    dele = b"".join(mp.f_varint(1, i) for i in ids)
    return mp.f_varint(1, 17) + mp.f_varint(2, 4) + mp.f_message(19, mp.f_message(5, dele))


class LiveClient:
    def __init__(self, port, auth_key, on_sample=None, on_battery=None,
                 on_daily=None, on_sync=None, on_sleep=None, on_details=None,
                 on_device_state=None, on_hr_config=None, should_sync=None, sync_gate=None,
                 stream_gate=None,
                 take_notifications=None, take_commands=None, capture_dir=None,
                 sync_interval=1800, live=True, debug=False):
        self.port_name = port
        self.auth = XiaomiAuth(auth_key)
        self.on_sample = on_sample or (lambda s: None)
        self.on_battery = on_battery or (lambda level, charging: None)
        self.on_daily = on_daily or (lambda s: None)
        self.on_sync = on_sync or (lambda summary: None)
        self.on_sleep = on_sleep or (lambda s: None)
        self.on_details = on_details or (lambda d: None)
        self.on_device_state = on_device_state or (lambda st: None)
        self.on_hr_config = on_hr_config or (lambda c: None)
        self._hr_cfg = {}
        self.stream_gate = stream_gate or (lambda: True)   # False = go dark (sleep)
        self._realtime_on = True
        self.last_kalive = 0
        self.last_devstate = 0
        self.should_sync = should_sync or (lambda: False)  # manual-sync flag check
        self.sync_gate = sync_gate or (lambda: True)       # False = quiet night, no auto-syncs
        self.take_notifications = take_notifications or (lambda: [])  # queued watch notifications
        self.take_commands = take_commands or (lambda: [])            # queued vibrate/alarm commands
        self.sync_interval = sync_interval  # seconds between automatic syncs
        self.live = live                    # True: hold channel + realtime; False: sync-only
        self.debug = debug
        self.ser = None
        self.buf = bytearray()
        self.version = 1
        self.seq = 0
        self.running = False
        self.authenticated = False
        self.last_sync = 0
        self.last_arm = 0        # last time realtime stream was (re)armed
        self.last_rx = 0         # last time any bytes were received (stall detection)
        self._icon_pkg = None    # package the watch last queried an icon for
        self._upload_type = 0    # pending DataUpload type (50 = notification icon)
        self._upload_bytes = None
        self._upload_chunk = 4096
        self.last_battery = None
        self.synced_days = 0
        self.fetcher = activity.ActivityFetcher(
            request_file=lambda raw: self._send_command(build_request_file(raw), is_auth=False),
            ack_file=lambda raw: self._send_command(build_ack_file(raw), is_auth=False),
            on_daily=self._on_daily,
            on_complete=self._on_sync_complete,
            on_sleep=self.on_sleep,
            on_details=self.on_details,
            capture_dir=capture_dir,
            log=log,
        )

    # ---------- health history / sync ----------
    def start_sync(self):
        if self.fetcher.busy():
            return
        self.synced_days = 0
        self.last_sync = time.time()
        log("sync: requesting recorded data ...")
        self._send_command(build_battery_query(), is_auth=False)
        self._send_command(build_fetch_today(), is_auth=False)

    def _on_daily(self, summary):
        self.synced_days += 1
        self.on_daily(summary)

    def _on_sync_complete(self):
        log("sync: complete (%d day summaries)" % self.synced_days)
        self.on_sync({"days": self.synced_days, "battery": self.last_battery})
        if not self.live:
            # sync-only: release the channel so the phone can be master again
            log("sync-only: disconnecting until next interval")
            self.running = False

    # ---------- transport ----------
    def _send_raw(self, data: bytes):
        if self.debug:
            log("TX %s", data.hex())
        self.ser.write(data)

    def _next_seq(self):
        s = self.seq & 0xFF
        self.seq += 1
        return s

    def _send_command(self, cmd_bytes: bytes, is_auth: bool):
        seq = self._next_seq()
        if is_auth:
            frame = spp.build_v2_data(seq, spp.V2_CH_PROTOBUF, spp.V2_OP_PLAINTEXT, cmd_bytes)
        else:
            enc = self.auth.encrypt_v2(cmd_bytes)
            frame = spp.build_v2_data(seq, spp.V2_CH_PROTOBUF, spp.V2_OP_ENCRYPTED, enc)
        self._send_raw(frame)

    def _send_device_command(self, spec):
        kind = spec.get("kind")
        if kind == "vibrate":
            self._send_command(build_vibrate(True), is_auth=False)
            log("vibrate: sent find-watch alert")
        elif kind == "vibrate_stop":
            self._send_command(build_vibrate(False), is_auth=False)
            log("vibrate: stop find-watch alert")
        elif kind == "cue":
            self._send_command(build_gentle_cue(), is_auth=False)
            log("cue: gentle notification buzz")
        elif kind == "hr_config_get":
            self._send_command(build_hr_config_get(), is_auth=False)
            log("hr_config: requested current config")
        elif kind == "advanced_on":
            # enable sleep-stage (REM/deep) detection + frequent HR sampling,
            # preserving the rest of the last-read config
            cfg = dict(self._hr_cfg)
            cfg["advanced"] = True
            cfg["interval"] = 1
            if cfg.get("breathing") in (None, 0):
                cfg["breathing"] = 1
            self._send_command(build_hr_config_set(cfg), is_auth=False)
            log("hr_config: SET advancedMonitoring=ON interval=1 (was advanced=%s)"
                % self._hr_cfg.get("advanced"))
        elif kind == "alarm":
            hour = int(spec.get("hour", 7))
            minute = int(spec.get("minute", 0))
            rep = spec.get("repeat", "once")
            mode, flags = 0, 0
            if rep == "daily":
                mode = 1
            elif rep == "weekdays":
                mode, flags = 5, 0b0011111       # Mon-Fri
            elif rep == "weekends":
                mode, flags = 5, 0b1100000       # Sat+Sun
            self._send_command(build_create_alarm(hour, minute, mode, flags,
                                                  enabled=bool(spec.get("enabled", True))), is_auth=False)
            log("alarm: set %02d:%02d repeat=%s", hour, minute, rep)
        elif kind == "delete_alarms":
            ids = spec.get("ids") or list(range(1, 11))
            self._send_command(build_delete_alarms(ids), is_auth=False)
            log("alarm: delete %s", ids)

    # ---------- notification icon upload (DataUpload over the Data channel) ----------
    def _send_data_chunk(self, chunk: bytes):
        seq = self._next_seq()
        enc = self.auth.encrypt_v2(chunk)
        self._send_raw(spp.build_v2_data(seq, spp.V2_CH_DATA, spp.V2_OP_ENCRYPTED, enc))

    def _handle_icon_request(self, pixel_format, size):
        try:
            import watchicon
            data = watchicon.convert(pixel_format, watchicon.claude_icon(size), size)
        except Exception as e:
            log("icon convert error: %s", e)
            return
        if not data:
            log("icon: unsupported pixel format %s", pixel_format)
            return
        self._upload_type = 50   # TYPE_NOTIFICATION_ICON
        self._upload_bytes = data
        log("icon: converted %d bytes (fmt=%s size=%s), requesting upload", len(data), pixel_format, size)
        self._send_command(build_upload_request(self._upload_type, data), is_auth=False)

    def _do_upload(self, resume):
        import hashlib
        import struct
        import zlib
        data = self._upload_bytes
        if not data:
            return
        md5 = hashlib.md5(data).digest()
        buf1 = bytes([0, self._upload_type]) + md5 + struct.pack("<i", len(data)) + data[resume:]
        payload = buf1 + struct.pack("<I", zlib.crc32(buf1) & 0xFFFFFFFF)
        part_size = max(1, self._upload_chunk - 4)
        total = (len(payload) + part_size - 1) // part_size
        for i in range(total):
            start = i * part_size
            chunk = struct.pack("<HH", total, i + 1) + payload[start:start + part_size]
            self._send_data_chunk(chunk)
        log("icon: uploaded %d bytes in %d chunk(s)", len(payload), total)
        self._upload_bytes = None
        self._upload_type = 0

    # ---------- main loop ----------
    def run(self):
        if serial is None:
            raise RuntimeError("pyserial not installed: pip install pyserial")
        log("Opening %s ...", self.port_name)
        self.ser = serial.Serial(self.port_name, baudrate=115200, timeout=0.2)
        self.running = True
        connect_ts = time.time()
        self.last_rx = connect_ts     # last time ANY bytes arrived
        log("Connected. Requesting SPP protocol version ...")
        self._send_raw(spp.build_v1_version_query())
        try:
            while self.running:
                chunk = self.ser.read(4096)
                if chunk:
                    self.last_rx = time.time()
                    if self.debug:
                        log("RX %s", chunk.hex())
                    self.buf.extend(chunk)
                    self._process()
                # stall detection: the BT link can drop while the COM port stays
                # nominally open (half-open) — nothing errors, so force a reconnect
                # when data stops. This is what the outer service loop retries on.
                now = time.time()
                # during the DARK sleep window we intentionally stop streaming, so
                # silence is EXPECTED — verify the link with a cheap device-state
                # ping (no HR streaming) instead of treating quiet as a stall.
                dark = self.authenticated and not self.stream_gate()
                if dark and now - self.last_kalive > 60:
                    self.last_kalive = now
                    try:
                        self._send_command(build_device_state_get(), is_auth=False)
                    except Exception:
                        pass
                stall_limit = 180 if dark else 30
                if self.authenticated and now - self.last_rx > stall_limit:
                    log("link stalled (no data %ds) — reconnecting", stall_limit)
                    self.running = False
                    break
                if not self.authenticated and now - connect_ts > 20:
                    log("handshake timeout (20s) — reconnecting")
                    self.running = False
                    break
                # keep realtime alive — but DON'T spam: re-arm only when the
                # stream actually went quiet (blind 10s re-arm 24/7 is suspected
                # of stressing the watch firmware into weekly reboots).
                # And go DARK during deep sleep: while streaming realtime the
                # watch (advancedMonitoring is ON, confirmed) still can't build
                # REM/deep stages — likely because we hog its HR sensor. Stop
                # streaming so it runs its own sleep-HR analysis; re-arm near wake.
                if not self.stream_gate() and self._realtime_on:
                    try:
                        self._send_command(build_disable_realtime(), is_auth=False)
                    except Exception:
                        pass
                    self._realtime_on = False
                    log("realtime: OFF (sleep — leaving the watch to stage REM itself)")
                if (self.authenticated and self.live and self.stream_gate()
                        and now - self.last_rx > 12
                        and time.time() - self.last_arm >= 15):
                    self.last_arm = time.time()
                    self._realtime_on = True
                    try:
                        self._send_command(build_enable_realtime(), is_auth=False)
                    except Exception:
                        pass
                # push any queued notifications to the watch
                if self.authenticated:
                    for note in (self.take_notifications() or []):
                        try:
                            self._send_command(build_notification(note.get("title", "Claude Code"),
                                                                   note.get("body", ""),
                                                                   note.get("app", "Claude Code")), is_auth=False)
                            log("sent watch notification: %s" % note.get("title"))
                        except Exception as e:
                            log("notify send error: %s" % e)
                # device-state GET removed: on this watch it returns only an
                # ack (no fields) — polling it every 45s was pure chatter.
                # Real asleep/worn changes arrive as sub-79 pushes (handled).
                # queued device commands (vibrate / set alarm) from the UI
                if self.authenticated:
                    for spec in (self.take_commands() or []):
                        try:
                            self._send_device_command(spec)
                        except Exception as e:
                            log("device command error: %s", e)
                # manual sync requested from the UI
                if self.authenticated and not self.fetcher.busy() and self.should_sync():
                    log("manual sync requested")
                    self.start_sync()
                # periodic auto-sync of recorded history (gated: quiet night —
                # every fetch gets logged by the watch as a micro-awakening and
                # kills its stage detection; 14/14 correlation 2026-07-07)
                if (self.authenticated and self.sync_interval
                        and not self.fetcher.busy()
                        and self.sync_gate()
                        and time.time() - self.last_sync >= self.sync_interval):
                    self.start_sync()
        finally:
            self.close()

    def close(self):
        self.running = False
        try:
            if self.authenticated and self.ser:
                self._send_command(build_disable_realtime(), is_auth=False)
        except Exception:
            pass
        if self.ser:
            self.ser.close()

    def _process(self):
        while True:
            if self.version == 1:
                status, pkt, consumed = spp.try_decode_v1(bytes(self.buf))
            else:
                status, pkt, consumed = spp.try_decode_v2(bytes(self.buf))
            if status == "incomplete":
                return
            if status == "invalid":
                del self.buf[:max(consumed, 1)]
                continue
            del self.buf[:consumed]
            try:
                self._handle(pkt)
            except Exception as e:
                log("handle error: %s", e)

    def _handle(self, pkt):
        if self.version == 1:
            if pkt["channel"] == spp.V1_CH_VERSION:
                payload = pkt["payload"]
                ver = payload[0] if payload else 0
                log("Watch SPP version byte = %d", ver)
                if ver >= 2:
                    self.version = 2
                    log("Switching to SPP v2, starting session ...")
                    self._send_raw(spp.build_v2_session_start(0))
                else:
                    log("This watch uses SPP v1 for data; this build implements v2 only. Stopping.")
                    self.running = False
            else:
                log("Unexpected v1 packet on channel %d", pkt["channel"])
            return

        t = pkt["type"]
        if t == spp.V2_TYPE_SESSION_CONFIG:
            log("Session established, starting encrypted handshake ...")
            self._send_command(self.auth.build_phone_nonce_command(), is_auth=True)
        elif t == spp.V2_TYPE_ACK:
            pass
        elif t == spp.V2_TYPE_DATA:
            self._send_raw(spp.build_v2_ack(pkt["seq"]))
            data = pkt["payload"]
            if pkt["opcode"] == spp.V2_OP_ENCRYPTED:
                data = self.auth.decrypt_v2(data)
            if pkt["channel"] == spp.V2_CH_PROTOBUF:
                self._handle_command(data)
            elif pkt["channel"] == spp.V2_CH_ACTIVITY:
                # streamed history file chunk
                self.fetcher.add_chunk(data)

    def _handle_command(self, cmd_bytes):
        d = mp.decode(cmd_bytes)
        ctype = mp.get1(d, 1)
        subtype = mp.get1(d, 2)
        if self.debug:
            log("CMD type=%s subtype=%s", ctype, subtype)

        if ctype == 1:  # auth
            auth_b = mp.get1(d, 3, b"")
            ad = mp.decode(auth_b) if auth_b else {}
            if subtype == 26 and 31 in ad:  # watchNonce
                wn = mp.decode(ad[31][0])
                nonce = mp.get1(wn, 1, b"")
                whmac = mp.get1(wn, 2, b"")
                step3 = self.auth.handle_watch_nonce(nonce, whmac)
                if step3 is None:
                    log("AUTH FAILED — wrong auth key (watch HMAC mismatch). Stopping.")
                    self.running = False
                    return
                log("Watch nonce OK, sending auth step 3 ...")
                self._send_command(step3, is_auth=True)
            elif subtype == 27 or mp.get1(ad, 8) == 1:
                self.authenticated = True
                self.auth.encrypted = True
                if self.live:
                    log("AUTHENTICATED. Enabling real-time stats ...")
                    self._send_command(build_enable_realtime(), is_auth=False)
                    self.last_arm = time.time()
                else:
                    log("AUTHENTICATED (sync-only mode).")
                # kick off an initial history + battery sync right away
                self.start_sync()
        elif ctype == 2:  # system
            if subtype in (78, 79):  # device state (asleep / worn) — GET reply or push
                system = mp.get1(d, 4, b"")
                sysd = mp.decode(system) if system else {}
                asleep = worn = None
                bds = mp.get1(sysd, 48)   # BasicDeviceState (GET reply): isWorn=3, isUserAsleep=4 (bool)
                ds = mp.get1(sysd, 49)    # DeviceState (push): wearingState=2, sleepState=3 (1/2)
                if bds is not None:
                    b = mp.decode(bds)
                    worn = mp.get1(b, 3) == 1
                    asleep = mp.get1(b, 4) == 1
                elif ds is not None:
                    s = mp.decode(ds)
                    worn = mp.get1(s, 2) == 1
                    asleep = mp.get1(s, 3) == 1
                if asleep is not None or worn is not None:
                    st = {"asleep": bool(asleep), "worn": bool(worn)}
                    log("device state: asleep=%s worn=%s", st["asleep"], st["worn"])
                    self.on_device_state(st)
                return
            if subtype == 1:  # battery
                system = mp.get1(d, 4, b"")
                sysd = mp.decode(system) if system else {}
                power = mp.get1(sysd, 2, b"")
                powd = mp.decode(power) if power else {}
                bat = mp.get1(powd, 1, b"")
                batd = mp.decode(bat) if bat else {}
                level = mp.get1(batd, 1)
                state = mp.get1(batd, 2)  # 1 charging, 2 not charging
                if level is not None:
                    charging = (state == 1)
                    self.last_battery = {"level": level, "charging": charging}
                    log("battery = %s%% (%s)", level, "charging" if charging else "discharging")
                    self.on_battery(level, charging)
        elif ctype == 7:  # notification (icon query / request)
            notif_b = mp.get1(d, 9, b"")
            nd = mp.decode(notif_b) if notif_b else {}
            if subtype == 16:  # watch asks: do you have an icon for this package?
                q = mp.get1(nd, 16)
                pkg = mp.get1(mp.decode(q), 1, b"") if q else b""
                pkg = pkg.decode("utf-8", "ignore") if isinstance(pkg, (bytes, bytearray)) else (pkg or "")
                self._icon_pkg = pkg or "ai.claude.code"
                log("icon: watch queried icon for %s", self._icon_pkg)
                self._send_command(build_icon_reply(self._icon_pkg), is_auth=False)
            elif subtype == 15:  # watch requests the icon bytes (pixelFormat + size)
                req = mp.get1(nd, 15)
                if req is not None:
                    rd = mp.decode(req)
                    status = mp.get1(rd, 1, 0)
                    pf = mp.get1(rd, 2, 0)
                    size = mp.get1(rd, 3, 0)
                    log("icon: watch requests fmt=%s size=%s status=%s", pf, size, status)
                    if status == 0 and size:
                        self._handle_icon_request(pf, size)
        elif ctype == 22:  # data upload ack
            if subtype == 0:
                du = mp.get1(d, 24, b"")
                dud = mp.decode(du) if du else {}
                ack = mp.get1(dud, 2)
                ackd = mp.decode(ack) if ack else {}
                resume = mp.get1(ackd, 4, 0) or 0
                cs = mp.get1(ackd, 5)
                if cs:
                    self._upload_chunk = cs
                log("icon: upload ack resume=%s chunkSize=%s", resume, self._upload_chunk)
                self._do_upload(resume)
        elif ctype == 8:  # health
            if subtype == 10:  # heart-rate config GET response
                cfg = parse_hr_config(d)
                self._hr_cfg = cfg
                log("HR config: advancedMonitoring(REM/deep)=%s interval=%s breathing=%s disabled=%s"
                    % (cfg["advanced"], cfg["interval"], cfg["breathing"], cfg["disabled"]))
                self.on_hr_config(cfg)
                return
            if subtype == 11:  # heart-rate config SET ack
                log("HR config SET ack")
                return
            if subtype in (1, 2):  # activity fetch today / past -> list of file IDs
                health = mp.get1(d, 10, b"")
                hd = mp.decode(health) if health else {}
                ids = mp.get1(hd, 2, b"")  # activityRequestFileIds
                self.fetcher.handle_file_ids(subtype, ids or b"")
                if subtype == 1:
                    # after today's IDs, ask for the past ones too
                    self._send_command(build_fetch_past(), is_auth=False)
            elif subtype == 47:
                health = mp.get1(d, 10, b"")
                hd = mp.decode(health) if health else {}
                rts_b = mp.get1(hd, 39)
                if rts_b is None:
                    return
                rts = mp.decode(rts_b)
                sample = {
                    "ts": time.time(),
                    "steps": mp.get1(rts, 1, 0),
                    "calories": mp.get1(rts, 2, 0),
                    "heartRate": mp.get1(rts, 4, 0),
                    "standingHours": mp.get1(rts, 6, 0),
                }
                self.on_sample(sample)


# ---------- optional live web dashboard ----------
DASH_HTML = """<!DOCTYPE html><html><head><meta charset=utf-8>
<title>Redmi Watch 5 — live</title><style>
body{background:#0f1117;color:#e7e9ee;font-family:Segoe UI,Arial,sans-serif;text-align:center;margin:0}
.wrap{max-width:520px;margin:0 auto;padding:40px 20px}
h1{font-size:16px;color:#9aa0ad;font-weight:600}
.hr{font-size:96px;font-weight:800;color:#ff6a3d;line-height:1}
.hr small{font-size:22px;color:#9aa0ad}
.row{display:flex;gap:16px;margin-top:24px}
.card{flex:1;background:#171a23;border:1px solid #2a2f3d;border-radius:14px;padding:16px}
.card .v{font-size:30px;font-weight:700}.card .l{color:#9aa0ad;font-size:12px;text-transform:uppercase}
.t{color:#9aa0ad;font-size:12px;margin-top:18px}
</style></head><body><div class=wrap>
<h1>Redmi Watch 5 Active — данные в реальном времени</h1>
<div class=hr id=hr>--<small> уд/мин</small></div>
<div class=row>
<div class=card><div class=v id=steps>--</div><div class=l>Шаги</div></div>
<div class=card><div class=v id=cal>--</div><div class=l>Калории</div></div>
</div><div class=t id=t>ожидание данных…</div></div>
<script>
async function tick(){try{const r=await fetch('/data');const d=await r.json();
if(d.heartRate){document.getElementById('hr').innerHTML=d.heartRate+'<small> уд/мин</small>';}
document.getElementById('steps').textContent=d.steps??'--';
document.getElementById('cal').textContent=d.calories??'--';
if(d.ts){document.getElementById('t').textContent='обновлено '+new Date(d.ts*1000).toLocaleTimeString();}
}catch(e){}}
setInterval(tick,1000);tick();
</script></body></html>"""


def start_dashboard(port, latest, host="127.0.0.1"):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/data"):
                body = json.dumps(latest).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                body = DASH_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    srv = HTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("Dashboard: http://%s:%d", host, port)


def main():
    ap = argparse.ArgumentParser(description="Redmi Watch 5 Active live client")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--port", help="serial/COM port of the watch (e.g. COM8)")
    ap.add_argument("--auth-key", help="16-byte auth key as hex (32 chars)")
    ap.add_argument("--csv", help="append samples to this CSV file")
    ap.add_argument("--serve", type=int, nargs="?", const=8765, help="serve live web dashboard on this port")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.list:
        if serial is None:
            print("pyserial not installed")
            return
        for p in list_ports.comports():
            print(f"{p.device}\t{p.description}")
        return

    if not args.port or not args.auth_key:
        ap.error("--port and --auth-key are required (or use --list)")

    latest = {"ts": 0, "heartRate": 0, "steps": 0, "calories": 0}
    csv_file = open(args.csv, "a", encoding="utf-8") if args.csv else None
    if csv_file and csv_file.tell() == 0:
        csv_file.write("timestamp,heartRate,steps,calories\n")

    def on_sample(s):
        latest.update(s)
        log("HR=%s bpm  steps=%s  cal=%s", s["heartRate"], s["steps"], s["calories"])
        if csv_file:
            csv_file.write(f"{int(s['ts'])},{s['heartRate']},{s['steps']},{s['calories']}\n")
            csv_file.flush()

    if args.serve:
        start_dashboard(args.serve, latest)

    client = LiveClient(args.port, args.auth_key, on_sample=on_sample, debug=args.debug)
    try:
        client.run()
    except KeyboardInterrupt:
        log("Stopping ...")
        client.close()


if __name__ == "__main__":
    main()
