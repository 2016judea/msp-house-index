"""Seal the paid bundle at rest: AES-256-GCM, file = nonce(12) | tag(16) | ciphertext.

Why: Vercel serves every uploaded file statically, api/_full/ included. The
vercel.json redirect on /api/_full/:path* was the only gate, and it matched the
literal underscore only: /api/%5Ffull/rows.json.gz returned the full 7,454-home
index (1.3 MB) and detail.json.gz (11.7 MB) to anyone, no password (2026-10-18).
Sealed, a file reached by any route is ciphertext; only /api/data holds the key
(BUNDLE_KEY, set on the project by scripts/deploy.py). Mirrored in site/api/_lib.js open().
"""
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"MSPSEAL1"


def new_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def seal(data: bytes, key: bytes) -> bytes:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, data, MAGIC)  # ciphertext || tag(16)
    return MAGIC + nonce + ct[-16:] + ct[:-16]


def unseal(blob: bytes, key: bytes) -> bytes:
    assert blob[:8] == MAGIC, "not a sealed file"
    nonce, tag, ct = blob[8:20], blob[20:36], blob[36:]
    return AESGCM(key).decrypt(nonce, ct + tag, MAGIC)
