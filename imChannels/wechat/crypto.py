"""
AES-128-ECB crypto helpers for the WeChat iLink Bot media (CDN) channel.

Media files (documents, voice, images, video) are encrypted with
AES-128-ECB (PKCS#7 padding) before being uploaded to / after being
downloaded from the WeChat CDN.

The ``aes_key`` that travels over the wire appears in three formats, and
all of them must be accepted when decoding:

- A) base64 of the 16 raw key bytes   (e.g. ``ABEiM0RVZneImaq7zN3u/w==``)
- B) base64 of the 32-char hex string (e.g. ``MDAxMTIyMzM0NDU1NjY3Nzg4OTlh...``)
- C) direct 32-char hex string        (e.g. ``00112233445566778899aabbccddeeff``)

Only the Python standard library + ``cryptography`` (for the AES primitive)
are required.
"""

import base64
import binascii
from typing import Optional, Union

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

__all__ = [
    "decode_aes_key",
    "aes_encrypt",
    "aes_decrypt",
    "generate_aes_key",
]

_AES_BLOCK_SIZE = 16


def decode_aes_key(aes_key: Optional[Union[str, bytes]]) -> bytes:
    """Decode an iLink ``aes_key`` (any of the 3 wire formats) to 16 raw bytes.

    Raises ``ValueError`` if the key cannot be decoded to a 16-byte key.
    """
    if aes_key is None:
        raise ValueError("aes_key is None")
    if isinstance(aes_key, bytes):
        raw = aes_key
    else:
        raw = str(aes_key).strip()

    # C) direct base64-of-hex ("double-encoded") -- try base64 of hex string first
    b64_text = raw.decode("ascii", errors="ignore") if isinstance(raw, bytes) else raw
    candidate = _try_base64_decode(b64_text)
    if candidate is not None:
        key = _from_hex_string(candidate)
        if key is not None:
            return key
        # base64 that decodes to raw 16 bytes (format A)
        if len(candidate) == _AES_BLOCK_SIZE:
            return candidate

    # B) base64 of raw 16 bytes, but which we failed above; already handled.
    # C) direct 32-char hex (format C)
    key = _from_hex_string(raw)
    if key is not None:
        return key

    raise ValueError(f"Unable to decode aes_key (len={len(raw)})")


def _try_base64_decode(value: str):
    """Return the bytes decoded from base64, or None on failure."""
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        # Some keys are padded oddly; retry without strict validation
        try:
            return base64.b64decode(value)
        except (binascii.Error, ValueError):
            return None


def _from_hex_string(value: Union[str, bytes]) -> Optional[bytes]:
    """Decode ``value`` as a hex string to bytes if it yields 16 bytes."""
    if isinstance(value, bytes):
        s = value.decode("ascii", errors="ignore")
    else:
        s = value
    s = s.strip()
    if len(s) == _AES_BLOCK_SIZE * 2:
        try:
            result = bytes.fromhex(s)
            if len(result) == _AES_BLOCK_SIZE:
                return result
        except ValueError:
            return None
    return None


def generate_aes_key() -> bytes:
    """Generate a fresh random 16-byte AES key for media upload."""
    import secrets

    return secrets.token_bytes(_AES_BLOCK_SIZE)


def aes_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt ``plaintext`` with AES-128-ECB + PKCS#7 using ``key`` (16 bytes)."""
    padder = padding.PKCS7(_AES_BLOCK_SIZE * 8).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def aes_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt AES-128-ECB + PKCS#7 ``ciphertext`` using ``key`` (16 bytes)."""
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(_AES_BLOCK_SIZE * 8).unpadder()
    return unpadder.update(padded) + unpadder.finalize()
