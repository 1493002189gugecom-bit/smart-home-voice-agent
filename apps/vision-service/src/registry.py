"""DPAPI-encrypted, atomically replaced biometric prototype registry.

Storage format (single file under the Git-ignored runtime directory):

```text
b"DSHFACE1" | uint32 little-endian payload length | DPAPI ciphertext
```

The ciphertext is the UTF-8 JSON encoding of exactly these keys:
`schema_version`, `person_ids`, `pack`, `embedding_dim`, `prototypes`. Prototypes
are float32 vectors serialized as hex so no biometric data appears as readable
text on disk.

Privacy rules enforced here (design spec section 10, plan Task 4 step 3):

- embeddings never appear in an exception message, log line or `repr`;
- every write goes to a temporary sibling, is `fsync`-ed, then `os.replace`-d;
- `delete` removes every prototype for the person;
- when the platform cannot provide current-user encryption the registry stays
  in memory and refuses to persist rather than writing unprotected features.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import re
import struct
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from contracts import FaceCandidate, valid_person_id


# Literal magic at the head of the registry file.
_MAGIC = b"DSHFACE1"
# Payload schema version written into the JSON body.
_SCHEMA_VERSION = 1
# Little-endian uint32 ciphertext length between the magic and the ciphertext.
_HEADER_SIZE = len(_MAGIC) + 4
# Registry file name inside the runtime directory.
_REGISTRY_FILENAME = "face_registry.bin"
# Prefix of the temporary sibling used for atomic replacement.
_TEMP_PREFIX = ".face_registry."
# Upper bound for the whole file, guarding against a corrupt or hostile file.
_MAX_FILE_BYTES = 4 * 1024 * 1024
# Upper bound for stored prototypes per person.
_MAX_PROTOTYPES_PER_PERSON = 32
# Accepted model pack names; a mismatch must never be ranked silently.
_PACK_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")
# Current Windows user, no UI prompt; never machine-wide scope.
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
# Optional entropy label mixed into the DPAPI key; not a secret.
_DPAPI_ENTROPY = b"smart-home/vision/face-registry/v1"


class RegistryError(RuntimeError):
    """Base class for registry problems; messages carry no biometric data."""


class RegistryCryptoError(RegistryError):
    """Raised when DPAPI encryption or decryption fails."""


class RegistryCorrupt(RegistryError):
    """Raised when the stored file is unreadable or inconsistent."""


class RegistryUnavailable(RegistryError):
    """Raised when persistence is impossible on this platform."""


@dataclass(frozen=True)
class _RegistryPayload:
    """Exact on-disk contract; extra or missing keys are corruption."""

    schema_version: int
    person_ids: tuple[str, ...]
    pack: str
    embedding_dim: int
    prototypes: dict[str, tuple[tuple[float, ...], ...]]


class _DataBlob(ctypes.Structure):
    """Win32 `DATA_BLOB`."""

    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


_DATABLOB_PTR = ctypes.POINTER(_DataBlob)
_crypt32: object | None = None
_crypt32_attempted = False


def _load_crypt32() -> object | None:
    """Return the loaded crypt32 library, or `None` off Windows."""

    global _crypt32, _crypt32_attempted
    if _crypt32_attempted:
        return _crypt32
    _crypt32_attempted = True
    if os.name != "nt":
        return None
    try:  # pragma: no cover - platform specific
        win_dll = getattr(ctypes, "WinDLL")
        _crypt32 = win_dll("crypt32", use_last_error=True)
    except (OSError, AttributeError):
        _crypt32 = None
    return _crypt32


def _set_argtypes(library: object, names: tuple[str, ...]) -> None:
    """Best-effort pointer argtypes so 64-bit handles are never truncated."""

    for name in names:
        function = getattr(library, name, None)
        if function is None:
            continue
        try:
            function.argtypes = [
                _DATABLOB_PTR, ctypes.c_void_p, _DATABLOB_PTR, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_uint32, _DATABLOB_PTR,
            ]
            function.restype = ctypes.c_int
        except (AttributeError, TypeError):  # pragma: no cover - defensive
            continue


def _local_free(pointer: object) -> None:
    """Release a buffer allocated by crypt32; failure is ignored on purpose."""

    try:  # pragma: no cover - platform specific
        kernel32 = getattr(ctypes, "windll", None)
        if kernel32 is None:
            return
        local_free = kernel32.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(pointer, ctypes.c_void_p))
    except Exception:  # pragma: no cover - platform specific
        return


def dpapi_protect(plaintext: bytes, entropy: bytes = _DPAPI_ENTROPY) -> bytes:
    """Encrypt `plaintext` for the current Windows user with DPAPI.

    `entropy` is a public label mixed into the key, not a secret. It keeps the
    face and voiceprint files from being interchangeable even for the same user.
    The default preserves the original face-registry ciphertext byte for byte.
    """

    label = entropy or _DPAPI_ENTROPY
    library = _load_crypt32()
    if library is None:
        raise RegistryUnavailable("DPAPI is unavailable on this platform")
    _set_argtypes(library, ("CryptProtectData",))
    buffer = (ctypes.c_ubyte * max(1, len(plaintext))).from_buffer_copy(
        plaintext if plaintext else b"\x00"
    )
    blob_in = _DataBlob(cbData=len(plaintext), pbData=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_buffer = (ctypes.c_ubyte * len(label)).from_buffer_copy(label)
    blob_entropy = _DataBlob(
        cbData=len(label),
        pbData=ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    blob_out = _DataBlob()
    ok = library.CryptProtectData(
        ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    )
    if not ok:  # pragma: no cover - platform specific
        raise RegistryCryptoError("DPAPI encryption failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _local_free(blob_out.pbData)


def dpapi_unprotect(ciphertext: bytes, entropy: bytes = _DPAPI_ENTROPY) -> bytes:
    """Decrypt `ciphertext` previously produced by `dpapi_protect`."""

    label = entropy or _DPAPI_ENTROPY
    library = _load_crypt32()
    if library is None:
        raise RegistryUnavailable("DPAPI is unavailable on this platform")
    _set_argtypes(library, ("CryptUnprotectData",))
    buffer = (ctypes.c_ubyte * max(1, len(ciphertext))).from_buffer_copy(
        ciphertext if ciphertext else b"\x00"
    )
    blob_in = _DataBlob(cbData=len(ciphertext), pbData=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_buffer = (ctypes.c_ubyte * len(label)).from_buffer_copy(label)
    blob_entropy = _DataBlob(
        cbData=len(label),
        pbData=ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    blob_out = _DataBlob()
    ok = library.CryptUnprotectData(
        ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    )
    if not ok:  # pragma: no cover - platform specific
        raise RegistryCryptoError("DPAPI decryption failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _local_free(blob_out.pbData)


def _normalize(values: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0.0 or not math.isfinite(norm):
        raise ValueError("embedding must have a finite non-zero norm")
    return tuple(value / norm for value in values)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Cosine similarity of two already-normalized vectors."""

    return sum(a * b for a, b in zip(left, right))


def similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Unit-length cosine similarity, usable by callers that need one pair."""

    return _cosine(_normalize(tuple(left)), _normalize(tuple(right)))


class PrototypeRegistry:
    """Current-user encrypted prototype store with atomic replacement.

    On a non-Windows host, or when DPAPI cannot be loaded, the registry operates
    in memory only: `replace` and `delete` still change the in-process state, but
    nothing is written and `persistent` stays False.

    Biometrics differ only in their file identity, their DPAPI entropy label and
    the candidate type they rank into, so those are class attributes instead of
    copy-pasted implementations. Faces and voiceprints therefore share exactly one
    storage, validation and encryption path.
    """

    # Overridden per biometric; the defaults are the face registry's.
    magic = _MAGIC
    filename = _REGISTRY_FILENAME
    temp_prefix = _TEMP_PREFIX
    entropy = _DPAPI_ENTROPY

    def __init__(self, runtime_dir: Path, pack: str) -> None:
        if not isinstance(pack, str) or not _PACK_PATTERN.match(pack):
            raise ValueError("pack must be a short alphanumeric model pack name")
        self.runtime_dir = Path(runtime_dir)
        self.pack = pack
        self.path = self.runtime_dir / self.filename
        self._lock = threading.RLock()
        self._prototypes: dict[str, tuple[tuple[float, ...], ...]] = {}
        self._embedding_dim: int | None = None
        self._loaded = False
        self._persistent = os.name == "nt" and _load_crypt32() is not None
        self.last_message: str | None = None

    # ------------------------------------------------------------- properties

    @property
    def persistent(self) -> bool:
        """True only when writes are actually encrypted and stored."""

        return self._persistent

    @property
    def embedding_dim(self) -> int | None:
        return self._embedding_dim

    def _public(self, person_id: str) -> bool:
        return valid_person_id(person_id)

    def _require_public(self, person_id: str) -> None:
        if not self._public(person_id):
            raise ValueError("person_id is invalid")

    # ------------------------------------------------------------------- load

    def _ensure_dir(self) -> None:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RegistryError("runtime directory is not writable") from exc

    def load(self) -> None:
        """Read and decrypt the registry once; safe to call repeatedly."""

        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            self._prototypes = {}
            self._embedding_dim = None
            if not self._persistent or not self.path.is_file():
                return
            try:
                raw = self.path.read_bytes()
            except OSError as exc:
                raise RegistryError("registry file is unreadable") from exc
            header_size = len(self.magic) + 4
            if len(raw) > _MAX_FILE_BYTES:
                raise RegistryCorrupt("registry file is implausibly large")
            if len(raw) < header_size or not raw.startswith(self.magic):
                raise RegistryCorrupt("registry file header is invalid")
            length = int.from_bytes(raw[len(self.magic):header_size], "little")
            ciphertext = raw[header_size:]
            if length != len(ciphertext):
                raise RegistryCorrupt("registry file length does not match its header")
            try:
                plaintext = dpapi_unprotect(ciphertext, self.entropy)
            except RegistryUnavailable as exc:
                raise RegistryCorrupt("registry cannot be decrypted on this host") from exc
            except RegistryCryptoError as exc:
                raise RegistryCorrupt("registry decryption failed for the current user") from exc
            payload = self._decode(plaintext)
            self._prototypes = dict(payload.prototypes)
            self._embedding_dim = payload.embedding_dim
            self.last_message = None

    def _decode(self, plaintext: bytes) -> _RegistryPayload:
        try:
            body = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RegistryCorrupt("registry payload is not valid JSON") from exc
        if not isinstance(body, dict) or set(body) != {
            "schema_version", "person_ids", "pack", "embedding_dim", "prototypes",
        }:
            raise RegistryCorrupt("registry payload keys are invalid")
        if body["schema_version"] != _SCHEMA_VERSION:
            raise RegistryCorrupt("registry schema version is unsupported")
        if body["pack"] != self.pack:
            raise RegistryCorrupt("registry was written for a different model pack")
        person_ids = body["person_ids"]
        if not isinstance(person_ids, list) or any(self._public(str(item)) is False for item in person_ids):
            raise RegistryCorrupt("registry person ids are invalid")
        dim = body["embedding_dim"]
        if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
            raise RegistryCorrupt("registry embedding dimension is invalid")
        raw_prototypes = body["prototypes"]
        if not isinstance(raw_prototypes, dict) or set(raw_prototypes) != set(str(item) for item in person_ids):
            raise RegistryCorrupt("registry prototype index is inconsistent")
        prototypes: dict[str, tuple[tuple[float, ...], ...]] = {}
        for person_id, items in raw_prototypes.items():
            if not isinstance(items, list) or len(items) > _MAX_PROTOTYPES_PER_PERSON:
                raise RegistryCorrupt("registry prototype count is invalid")
            vectors: list[tuple[float, ...]] = []
            for item in items:
                vector = self._decode_vector(item, dim)
                vectors.append(vector)
            prototypes[str(person_id)] = tuple(vectors)
        return _RegistryPayload(
            schema_version=_SCHEMA_VERSION,
            person_ids=tuple(str(item) for item in person_ids),
            pack=self.pack,
            embedding_dim=dim,
            prototypes=prototypes,
        )

    def _decode_vector(self, item: object, dim: int) -> tuple[float, ...]:
        if not isinstance(item, str) or len(item) != dim * 8:
            raise RegistryCorrupt("registry prototype encoding is invalid")
        try:
            packed = bytes.fromhex(item)
        except ValueError as exc:
            raise RegistryCorrupt("registry prototype encoding is invalid") from exc
        values = struct.unpack(f"<{dim}f", packed)
        if any(not math.isfinite(value) for value in values):
            raise RegistryCorrupt("registry prototype contains a non-finite value")
        return _normalize(tuple(float(value) for value in values))

    def _encode_vector(self, vector: tuple[float, ...]) -> str:
        return struct.pack(f"<{len(vector)}f", *vector).hex()

    # ------------------------------------------------------------------ writes

    def _payload(self) -> bytes:
        person_ids = sorted(self._prototypes)
        dim = self._dimension_for_write()
        body = {
            "schema_version": _SCHEMA_VERSION,
            "person_ids": person_ids,
            "pack": self.pack,
            "embedding_dim": dim,
            "prototypes": {
                person_id: [self._encode_vector(vector) for vector in self._prototypes[person_id]]
                for person_id in person_ids
            },
        }
        return json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def _dimension_for_write(self) -> int:
        if self._embedding_dim is not None:
            return self._embedding_dim
        for vectors in self._prototypes.values():
            for vector in vectors:
                return len(vector)
        return 0

    def _write_locked(self) -> None:
        """Encrypt and atomically replace the registry file."""

        if not self.persistent:
            self.last_message = "registry persistence unavailable; kept in memory"
            return
        self._ensure_dir()
        payload = self._payload()
        if len(payload) > _MAX_FILE_BYTES:
            raise RegistryError("registry payload is too large")
        try:
            ciphertext = dpapi_protect(payload, self.entropy)
        except RegistryUnavailable as exc:
            raise RegistryCryptoError("DPAPI is unavailable; refusing to store unprotected features") from exc
        temporary = self.runtime_dir / f"{self.temp_prefix}{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(self.magic)
                    handle.write(len(ciphertext).to_bytes(4, "little"))
                    handle.write(ciphertext)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                descriptor = -1
            os.replace(temporary, self.path)
            self._fsync_dir()
        except OSError as exc:
            raise RegistryError("registry write failed") from exc
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:  # pragma: no cover - best effort cleanup
                    pass
        self.last_message = None

    def _fsync_dir(self) -> None:
        """Best-effort directory fsync; unsupported on Windows, so ignore errors."""

        try:
            descriptor = os.open(self.runtime_dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:  # pragma: no cover - platform specific
            pass
        finally:
            os.close(descriptor)

    # -------------------------------------------------------------------- API

    def replace(self, person_id: str, prototypes: list[tuple[float, ...]]) -> None:
        """Replace every prototype of `person_id` in one encrypted write."""

        self._require_public(person_id)
        if len(prototypes) > _MAX_PROTOTYPES_PER_PERSON:
            raise ValueError("too many prototypes for one person")
        normalized: list[tuple[float, ...]] = []
        for vector in prototypes:
            values = tuple(float(item) for item in vector)
            if not values:
                raise ValueError("prototype must not be empty")
            normalized.append(_normalize(values))
        if normalized:
            dim = len(normalized[0])
            if any(len(vector) != dim for vector in normalized):
                raise ValueError("all prototypes must share one dimension")
        with self._lock:
            self.load()
            previous = self._prototypes.get(person_id)
            dims = {self._embedding_dim} if self._embedding_dim else set()
            dims.update(len(vector) for vectors in self._prototypes.values() for vector in vectors)
            dims.update(len(vector) for vector in normalized)
            dims.discard(None)
            if len(dims) > 1:
                raise ValueError("prototype dimension does not match the registry")
            self._prototypes[person_id] = tuple(normalized)
            if normalized:
                self._embedding_dim = len(normalized[0])
            try:
                self._write_locked()
            except Exception:
                if previous is None:
                    self._prototypes.pop(person_id, None)
                else:
                    self._prototypes[person_id] = previous
                raise

    def delete(self, person_id: str) -> bool:
        """Remove every prototype for `person_id`; False when nothing was stored."""

        self._require_public(person_id)
        with self._lock:
            self.load()
            previous = self._prototypes.pop(person_id, None)
            if previous is None:
                return False
            try:
                self._write_locked()
            except Exception:
                self._prototypes[person_id] = previous
                raise
            return True

    def _candidate(self, person_id: str | None, similarity: float | None, margin: float | None):
        """Build the ranked value; each biometric owns its own candidate type."""

        raise NotImplementedError

    def rank(self, embedding: tuple[float, ...]) -> list:
        """Return the best person, score and runner-up margin without policy.

        Acceptance thresholds belong to the caller's loaded configuration. A
        missing runner-up yields ``margin=None`` and must not make the sole
        registered person impossible to recognize.
        """

        query = _normalize(tuple(float(item) for item in embedding))
        with self._lock:
            self.load()
            scores: list[tuple[str, float]] = []
            for person_id, vectors in self._prototypes.items():
                # `None`, not -1.0: -1.0 is a legitimate cosine value, and using it
                # as the "no score yet" sentinel reported a perfectly anti-correlated
                # embedding as "nobody is enrolled".
                best: float | None = None
                for vector in vectors:
                    if len(vector) != len(query):
                        continue
                    value = _cosine(query, vector)
                    if best is None or value > best:
                        best = value
                if best is not None:
                    scores.append((person_id, best))
        scores.sort(key=lambda item: (-item[1], item[0]))
        if not scores:
            return [self._candidate(None, None, None)]
        top_person, top_score = scores[0]
        runner_up = scores[1][1] if len(scores) > 1 else None
        margin = None if runner_up is None else top_score - runner_up
        return [self._candidate(top_person, top_score, margin)]

    def prototypes(self, person_id: str) -> list[tuple[float, ...]]:
        """Return stored prototypes for `person_id`, or an empty list."""

        self._require_public(person_id)
        with self._lock:
            self.load()
            return list(self._prototypes.get(person_id, ()))

    def persons(self) -> list[str]:
        """Return sorted person ids that currently hold at least one prototype."""

        with self._lock:
            self.load()
            return sorted(person_id for person_id, vectors in self._prototypes.items() if vectors)

    def counts(self) -> dict[str, int]:
        """Prototype count per person; safe for logs and status endpoints."""

        with self._lock:
            self.load()
            return {person_id: len(vectors) for person_id, vectors in self._prototypes.items()}

    def forget_all(self) -> None:
        """Drop every in-memory prototype without touching the stored file."""

        with self._lock:
            self._loaded = True
            self._prototypes = {}
            self._embedding_dim = None


class FaceRegistry(PrototypeRegistry):
    """Face prototypes: the original file, magic and entropy label."""

    def _candidate(self, person_id: str | None, similarity: float | None, margin: float | None):
        return FaceCandidate(person_id=person_id, similarity=similarity, margin=margin)
