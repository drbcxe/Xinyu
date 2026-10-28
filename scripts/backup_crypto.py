"""Streaming authenticated backup components; encryption keys are kept separately."""

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"XINYUBK1"
CHUNK = 256 * 1024
MAX_COMPONENT = 2 * 1024 * 1024 * 1024


def encrypt_chunks(chunks, target: Path, key: bytes, aad: bytes, before_write=None):
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    count, digest = 0, hashlib.sha256()
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        if before_write:
            before_write(len(MAGIC) + len(nonce))
        stream.write(MAGIC + nonce)
        for part in chunks:
            count += len(part)
            if count > MAX_COMPONENT:
                raise ValueError("Backup component exceeds 2 GiB limit")
            digest.update(part)
            if before_write:
                before_write(len(part))
            stream.write(encryptor.update(part))
        if before_write:
            before_write(16)
        stream.write(encryptor.finalize())
        stream.write(encryptor.tag)
        stream.flush()
        os.fsync(stream.fileno())
    return {"bytes": count, "sha256": digest.hexdigest()}


def file_chunks(stream):
    while part := stream.read(CHUNK):
        yield part


def decrypt_to_file(
    source: Path, target: Path, key: bytes, aad: bytes, before_write=None
):
    size = source.stat().st_size
    if size < len(MAGIC) + 28 or size > MAX_COMPONENT + 36:
        raise ValueError("Invalid encrypted component size")
    digest, count = hashlib.sha256(), 0
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with source.open("rb") as stream, os.fdopen(descriptor, "wb") as output:
            if stream.read(8) != MAGIC:
                raise ValueError("Unknown backup format")
            nonce = stream.read(12)
            stream.seek(-16, 2)
            tag = stream.read(16)
            stream.seek(20)
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(aad)
            remaining = size - 36
            while remaining:
                value = stream.read(min(CHUNK, remaining))
                if not value:
                    raise ValueError("Truncated backup")
                remaining -= len(value)
                part = decryptor.update(value)
                digest.update(part)
                count += len(part)
                if before_write:
                    before_write(len(part))
                output.write(part)
            output.write(decryptor.finalize())
        return {"bytes": count, "sha256": digest.hexdigest()}
    except BaseException:
        target.unlink(missing_ok=True)
        raise
