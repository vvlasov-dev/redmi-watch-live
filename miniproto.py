"""Minimal protobuf (proto2) wire codec — just enough for the Xiaomi watch protocol.

We avoid a full protobuf dependency by encoding/decoding the handful of messages
we need by field number. Field numbers come from Gadgetbridge's xiaomi.proto.

Wire types: 0=varint, 1=fixed64, 2=length-delimited, 5=fixed32.
"""
import struct


# ---------- low level ----------
def encode_varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field: int, wt: int) -> bytes:
    return encode_varint((field << 3) | wt)


def f_varint(field: int, val: int) -> bytes:
    return _tag(field, 0) + encode_varint(val)


def f_bytes(field: int, val: bytes) -> bytes:
    return _tag(field, 2) + encode_varint(len(val)) + val


def f_string(field: int, val: str) -> bytes:
    return f_bytes(field, val.encode("utf-8"))


def f_float(field: int, val: float) -> bytes:
    return _tag(field, 5) + struct.pack("<f", val)


def f_message(field: int, body: bytes) -> bytes:
    return f_bytes(field, body)


# ---------- decoding ----------
def read_varint(buf: bytes, i: int):
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def decode(buf: bytes) -> dict:
    """Decode a protobuf message into {field_number: [values]}.

    Each value is: int (varint/fixed), or bytes (length-delimited / fixed).
    Length-delimited values are returned as raw bytes; caller decides whether
    to recurse (sub-message), or interpret as string/bytes.
    """
    out = {}
    i = 0
    n = len(buf)
    while i < n:
        key, i = read_varint(buf, i)
        field = key >> 3
        wt = key & 0x7
        if wt == 0:
            val, i = read_varint(buf, i)
        elif wt == 2:
            ln, i = read_varint(buf, i)
            val = buf[i:i + ln]
            i += ln
        elif wt == 5:
            val = buf[i:i + 4]
            i += 4
        elif wt == 1:
            val = buf[i:i + 8]
            i += 8
        else:
            raise ValueError(f"unsupported wire type {wt} for field {field}")
        out.setdefault(field, []).append(val)
    return out


def get1(d: dict, field: int, default=None):
    v = d.get(field)
    return v[0] if v else default
