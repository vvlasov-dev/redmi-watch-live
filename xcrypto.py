"""Xiaomi watch authentication + encryption.

Faithful port of Gadgetbridge's XiaomiAuthService (AGPLv3).
The "miwear-auth" handshake:
  1. phone -> watch: PhoneNonce (16 random bytes)
  2. watch -> phone: WatchNonce {nonce(16), hmac(32)}
  3. derive 64 bytes via HMAC-SHA256 key-expansion ->
         decryptionKey[0:16], encryptionKey[16:32],
         decryptionNonce[32:36], encryptionNonce[36:40]
     verify watch hmac, then send AuthStep3 {encryptedNonces, encryptedDeviceInfo}
  4. watch -> phone: auth OK; further protobuf traffic encrypted.

Post-auth SPP v2 traffic uses AES-CTR with IV == key ("encryptV2/decryptV2").
The AuthStep3 device-info blob uses AES-CCM (mac_len=4).
"""
import hmac
import hashlib
import os
import struct

from Crypto.Cipher import AES
from Crypto.Util import Counter

import miniproto as mp

MIWEAR_AUTH = b"miwear-auth"


def hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def compute_auth_step3_hmac(secret_key: bytes, phone_nonce: bytes, watch_nonce: bytes) -> bytes:
    """Returns 64 bytes (HKDF-like expansion), matching Gadgetbridge."""
    hmac_key = hmac_sha256(phone_nonce + watch_nonce, secret_key)
    out = bytearray()
    tmp = b""
    b = 1
    while len(out) < 64:
        tmp = hmac_sha256(hmac_key, tmp + MIWEAR_AUTH + bytes([b]))
        out.extend(tmp)
        b += 1
    return bytes(out[:64])


def ccm_encrypt(key: bytes, nonce12: bytes, payload: bytes) -> bytes:
    """AES-CCM, 32-bit tag appended to ciphertext (BouncyCastle layout)."""
    c = AES.new(key, AES.MODE_CCM, nonce=nonce12, mac_len=4)
    ct = c.encrypt(payload)
    return ct + c.digest()


def ccm_decrypt(key: bytes, nonce12: bytes, ct_with_tag: bytes, check_mac: bool = True) -> bytes:
    if check_mac:
        ct, tag = ct_with_tag[:-4], ct_with_tag[-4:]
        c = AES.new(key, AES.MODE_CCM, nonce=nonce12, mac_len=4)
        pt = c.decrypt(ct)
        c.verify(tag)
        return pt
    # no-mac mode: drop trailing 4 bytes, decrypt rest without verify
    ct = ct_with_tag[:-4]
    c = AES.new(key, AES.MODE_CCM, nonce=nonce12, mac_len=4)
    return c.decrypt(ct)


def ctr_crypt(key: bytes, iv16: bytes, data: bytes) -> bytes:
    ctr = Counter.new(128, initial_value=int.from_bytes(iv16, "big"))
    c = AES.new(key, AES.MODE_CTR, counter=ctr)
    return c.encrypt(data)  # CTR encrypt == decrypt


class XiaomiAuth:
    """Holds handshake state + session keys."""

    def __init__(self, auth_key_hex: str, phone_name: str = "Windows-PC",
                 phone_api_level: int = 33, region: str = "EN"):
        h = auth_key_hex.strip()
        if h.lower().startswith("0x"):
            h = h[2:]
        self.secret_key = bytes.fromhex(h)
        if len(self.secret_key) != 16:
            raise ValueError("auth key must be 16 bytes (32 hex chars)")
        self.phone_name = phone_name
        self.phone_api_level = phone_api_level
        self.region = (region + "XX")[:2].upper()

        self.phone_nonce = b""
        self.encryption_key = b""
        self.decryption_key = b""
        self.encryption_nonce = b""
        self.decryption_nonce = b""
        self.encrypted = False

    # ---- protobuf message builders (Command type=1 / auth) ----
    def build_phone_nonce_command(self) -> bytes:
        self.phone_nonce = os.urandom(16)
        phone_nonce = mp.f_bytes(1, self.phone_nonce)          # PhoneNonce.nonce
        auth = mp.f_message(30, phone_nonce)                   # Auth.phoneNonce
        cmd = mp.f_varint(1, 1) + mp.f_varint(2, 26) + mp.f_message(3, auth)
        return cmd  # Command{type=1, subtype=26, auth}

    def _build_auth_device_info(self) -> bytes:
        return (
            mp.f_varint(1, 0) +                       # unknown1 = 0
            mp.f_float(2, float(self.phone_api_level)) +  # phoneApiLevel (float)
            mp.f_string(3, self.phone_name) +         # phoneName
            mp.f_varint(4, 224) +                     # unknown3 = 224
            mp.f_string(5, self.region)               # region
        )

    def handle_watch_nonce(self, watch_nonce: bytes, watch_hmac: bytes):
        """Derive keys, verify, and return the AuthStep3 Command bytes (or None)."""
        expanded = compute_auth_step3_hmac(self.secret_key, self.phone_nonce, watch_nonce)
        self.decryption_key = expanded[0:16]
        self.encryption_key = expanded[16:32]
        self.decryption_nonce = expanded[32:36]
        self.encryption_nonce = expanded[36:40]

        confirmation = hmac_sha256(self.decryption_key, watch_nonce + self.phone_nonce)
        if confirmation != watch_hmac:
            return None  # wrong auth key

        encrypted_nonces = hmac_sha256(self.encryption_key, self.phone_nonce + watch_nonce)
        device_info = self._build_auth_device_info()
        # encrypt(payload, i=0): nonce = encNonce(4) + int32(0) + int32(0)
        nonce12 = self.encryption_nonce + struct.pack("<II", 0, 0)
        encrypted_device_info = ccm_encrypt(self.encryption_key, nonce12, device_info)

        step3 = mp.f_bytes(1, encrypted_nonces) + mp.f_bytes(2, encrypted_device_info)
        auth = mp.f_message(32, step3)                        # Auth.authStep3
        cmd = mp.f_varint(1, 1) + mp.f_varint(2, 27) + mp.f_message(3, auth)
        return cmd  # Command{type=1, subtype=27, auth.authStep3}

    # ---- post-auth payload crypto (SPP v2, AES-CTR, IV == key) ----
    def encrypt_v2(self, message: bytes) -> bytes:
        return ctr_crypt(self.encryption_key, self.encryption_key, message)

    def decrypt_v2(self, ciphertext: bytes) -> bytes:
        return ctr_crypt(self.decryption_key, self.decryption_key, ciphertext)
