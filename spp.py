"""Xiaomi SPP framing (Bluetooth Classic / serial). Port of Gadgetbridge (AGPLv3).

Two frame formats:
  V1 (used for the initial version handshake, preamble BA DC FE ... EF)
  V2 (preamble A5 A5, length + CRC-16/ARC, session + ACK + data packets)

This module is crypto-free: data-packet payloads are passed/returned raw,
the caller (client.py) applies AES via xcrypto.
"""
import struct

# ----- V1 -----
V1_PREAMBLE = b"\xba\xdc\xfe"
V1_EPILOGUE = b"\xef"
V1_CH_VERSION = 0
V1_CH_PROTO_RX = 1
V1_CH_PROTO_TX = 2
V1_CH_FITNESS = 3
V1_CH_MASS = 5
V1_DATA_PLAIN = 0
V1_DATA_ENCRYPTED = 1
V1_DATA_AUTH = 2
V1_OP_READ = 0
V1_OP_SEND = 2

# ----- V2 -----
V2_PREAMBLE = b"\xa5\xa5"
V2_TYPE_ACK = 1
V2_TYPE_SESSION_CONFIG = 2
V2_TYPE_DATA = 3
V2_CH_PROTOBUF = 1   # encrypted after auth
V2_CH_DATA = 2       # not encrypted
V2_CH_ACTIVITY = 5   # encrypted
V2_OP_PLAINTEXT = 1
V2_OP_ENCRYPTED = 2
V2_SESSION_START_REQ = 1
V2_SESSION_START_RESP = 2


# ========================= V1 =========================
def build_v1_version_query() -> bytes:
    channel = V1_CH_VERSION
    flags = 0x80 | 0x40  # flag + needsResponse
    payload = b""
    size = len(payload) + 3
    return (
        V1_PREAMBLE
        + bytes([channel & 0xF, flags])
        + struct.pack("<H", size)
        + bytes([V1_OP_READ, 0, V1_DATA_PLAIN])
        + payload
        + V1_EPILOGUE
    )


def try_decode_v1(buf: bytes):
    """Returns (status, packet|None, consumed).
    status in {'incomplete','invalid','ok'}."""
    if len(buf) < 11:
        return "incomplete", None, 0
    if buf[0:3] != V1_PREAMBLE:
        return "invalid", None, 1
    channel = buf[3] & 0x0F
    size = struct.unpack("<H", buf[5:7])[0]
    payload_len = size - 3
    if payload_len < 0:
        return "invalid", None, 1
    total = payload_len + 11
    if len(buf) < total:
        return "incomplete", None, 0
    opcode = buf[7]
    frame_serial = buf[8]
    data_type = buf[9]
    payload = buf[10:10 + payload_len]
    epi = buf[10 + payload_len:11 + payload_len]
    if epi != V1_EPILOGUE:
        return "invalid", None, 1
    return "ok", {
        "channel": channel, "opcode": opcode, "frame_serial": frame_serial,
        "data_type": data_type, "payload": payload,
    }, total


# ========================= V2 =========================
def crc16_arc(payload: bytes) -> int:
    """Bit-exact port of Gadgetbridge's calculatePayloadChecksum (CRC-16/ARC)."""
    crc = 0
    for byte in payload:
        for j in range(8):
            crc = (crc << 1) & 0xFFFFFFFF
            if (((crc >> 16) & 1) ^ ((byte >> j) & 1)) == 1:
                crc ^= 0x8005
    rev = int("{:032b}".format(crc & 0xFFFFFFFF)[::-1], 2)
    return (rev >> 16) & 0xFFFF


def encode_v2_frame(packet_type: int, seq: int, payload: bytes) -> bytes:
    return (
        V2_PREAMBLE
        + bytes([packet_type & 0xF, seq & 0xFF])
        + struct.pack("<H", len(payload))
        + struct.pack("<H", crc16_arc(payload))
        + payload
    )


def build_v2_session_start(seq: int = 0) -> bytes:
    payload = bytes([
        V2_SESSION_START_REQ,
        1, 0x03, 0x00, 0x01, 0x00, 0x00,   # VERSION = 1.0.0
        2, 0x02, 0x00, 0x00, 0xFC,         # MAX_PACKET_SIZE = 0xFC00
        3, 0x02, 0x00, 0x20, 0x00,         # TX_WIN = 32
        4, 0x02, 0x00, 0x10, 0x27,         # SEND_TIMEOUT = 10000 ms
    ])
    return encode_v2_frame(V2_TYPE_SESSION_CONFIG, seq, payload)


def build_v2_ack(seq: int) -> bytes:
    return encode_v2_frame(V2_TYPE_ACK, seq, b"")


def build_v2_data(seq: int, raw_channel: int, opcode: int, payload_after_crypto: bytes) -> bytes:
    inner = bytes([raw_channel & 0xF, opcode & 0xFF]) + payload_after_crypto
    return encode_v2_frame(V2_TYPE_DATA, seq, inner)


def try_decode_v2(buf: bytes):
    """Returns (status, packet|None, consumed)."""
    if len(buf) < 8:
        return "incomplete", None, 0
    if buf[0:2] != V2_PREAMBLE:
        return "invalid", None, 1
    ptype = buf[2] & 0xF
    seq = buf[3]
    plen = struct.unpack("<H", buf[4:6])[0]
    given = struct.unpack("<H", buf[6:8])[0]
    total = 8 + plen
    if len(buf) < total:
        return "incomplete", None, 0
    payload = buf[8:total]
    if crc16_arc(payload) != given:
        return "invalid", None, total
    pkt = {"type": ptype, "seq": seq}
    if ptype == V2_TYPE_DATA and len(payload) >= 2:
        pkt["channel"] = payload[0] & 0xF
        pkt["opcode"] = payload[1]
        pkt["payload"] = payload[2:]
    elif ptype == V2_TYPE_SESSION_CONFIG:
        pkt["opcode"] = payload[0] if payload else -1
    return "ok", pkt, total
