"""Offline self-tests for the protocol port (no hardware needed).

Validates: miniproto roundtrips, CRC-16/ARC against a reference, SPP v1/v2
framing, and the full auth crypto by simulating the watch side.

Run:  pip install pycryptodome   &&   python selftest.py
"""
import hmac
import hashlib
import os
import struct

import miniproto as mp
import spp
import xcrypto as xc

OK = "\033[32mOK\033[0m"
ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"[ OK ] {name}")
    else:
        fail += 1
        print(f"[FAIL] {name}")


# 1) miniproto
for n in [0, 1, 127, 128, 300, 70000, 2 ** 31, 2 ** 32 - 1]:
    b = mp.encode_varint(n)
    v, _ = mp.read_varint(b, 0)
    check(f"varint {n}", v == n)

cmd = mp.f_varint(1, 8) + mp.f_varint(2, 47) + mp.f_message(
    10, mp.f_message(39, mp.f_varint(1, 1234) + mp.f_varint(4, 72)))
d = mp.decode(cmd)
check("decode type", mp.get1(d, 1) == 8)
check("decode subtype", mp.get1(d, 2) == 47)
health = mp.decode(mp.get1(d, 10))
rts = mp.decode(mp.get1(health, 39))
check("decode steps", mp.get1(rts, 1) == 1234)
check("decode hr", mp.get1(rts, 4) == 72)


# 2) CRC-16/ARC vs reference
def ref_arc(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


check("CRC '123456789' == 0xBB3D", spp.crc16_arc(b"123456789") == 0xBB3D)
check("CRC matches reference (random)",
      all(spp.crc16_arc(x) == ref_arc(x) for x in [os.urandom(20) for _ in range(50)]))


# 3) SPP v1 version query bytes
vq = spp.build_v1_version_query()
check("v1 version query bytes", vq == b"\xba\xdc\xfe\x00\xc0\x03\x00\x00\x00\x00\xef")
st, pkt, used = spp.try_decode_v1(vq)
check("v1 decode roundtrip", st == "ok" and pkt["channel"] == 0 and used == len(vq))


# 4) SPP v2 data frame roundtrip
payload = b"hello-watch"
frame = spp.build_v2_data(7, spp.V2_CH_PROTOBUF, spp.V2_OP_ENCRYPTED, payload)
st, pkt, used = spp.try_decode_v2(frame)
check("v2 frame roundtrip",
      st == "ok" and used == len(frame) and pkt["type"] == spp.V2_TYPE_DATA
      and pkt["channel"] == spp.V2_CH_PROTOBUF and pkt["opcode"] == spp.V2_OP_ENCRYPTED
      and pkt["payload"] == payload)
# corrupt a byte -> invalid
bad = bytearray(frame); bad[-1] ^= 0xFF
st2, _, _ = spp.try_decode_v2(bytes(bad))
check("v2 bad CRC detected", st2 == "invalid")


# 5) crypto / handshake simulation
secret = os.urandom(16)
key_hex = secret.hex()
phone = xc.XiaomiAuth(key_hex, phone_name="Win-Test", phone_api_level=33, region="EN")

# CCM + CTR roundtrips
nonce12 = os.urandom(12)
ct = xc.ccm_encrypt(secret, nonce12, b"device-info-blob")
check("CCM roundtrip", xc.ccm_decrypt(secret, nonce12, ct) == b"device-info-blob")
k = os.urandom(16)
check("CTR roundtrip", xc.ctr_crypt(k, k, xc.ctr_crypt(k, k, b"msg123")) == b"msg123")

# key expansion length + determinism
pn = os.urandom(16); wn = os.urandom(16)
e1 = xc.compute_auth_step3_hmac(secret, pn, wn)
e2 = xc.compute_auth_step3_hmac(secret, pn, wn)
check("key expansion 64 bytes + deterministic", len(e1) == 64 and e1 == e2)

# full handshake: phone builds nonce cmd, simulate watch, verify step3
nonce_cmd = phone.build_phone_nonce_command()
nc = mp.decode(nonce_cmd)
auth_b = mp.decode(mp.get1(nc, 3))
phone_nonce = mp.get1(mp.decode(mp.get1(auth_b, 30)), 1)
check("phone nonce in command (16B)", len(phone_nonce) == 16 and phone_nonce == phone.phone_nonce)

watch_nonce = os.urandom(16)
expanded = xc.compute_auth_step3_hmac(secret, phone_nonce, watch_nonce)
watch_dkey = expanded[0:16]  # == phone.decryption_key
watch_hmac = xc.hmac_sha256(watch_dkey, watch_nonce + phone_nonce)

step3 = phone.handle_watch_nonce(watch_nonce, watch_hmac)
check("handshake accepts valid watch hmac", step3 is not None)

s3 = mp.decode(step3)
a3 = mp.decode(mp.get1(s3, 3))
step3_msg = mp.decode(mp.get1(a3, 32))
enc_nonces = mp.get1(step3_msg, 1)
enc_devinfo = mp.get1(step3_msg, 2)
check("encryptedNonces correct",
      enc_nonces == xc.hmac_sha256(phone.encryption_key, phone_nonce + watch_nonce))
# watch decrypts device info with encryption_key + nonce(encNonce+0+0)
n12 = phone.encryption_nonce + struct.pack("<II", 0, 0)
devinfo = xc.ccm_decrypt(phone.encryption_key, n12, enc_devinfo)
di = mp.decode(devinfo)
check("device info region", mp.get1(di, 5) == b"EN")
check("device info unknown3==224", mp.get1(di, 4) == 224)
check("device info phoneName", mp.get1(di, 3) == b"Win-Test")

# wrong key rejected
phone2 = xc.XiaomiAuth(os.urandom(16).hex())
phone2.build_phone_nonce_command()
check("wrong key rejected", phone2.handle_watch_nonce(watch_nonce, watch_hmac) is None)

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
