"""DPAPI-encrypted voiceprint prototype store.

This shares the storage, validation and encryption path with the face registry in
`registry.py`: only the file identity, the DPAPI entropy label and the candidate
type differ, so there is exactly one implementation of the security-relevant code
and no chance of the two drifting apart.

The stored vectors are speaker embeddings, which are biometric data. They never
leave the machine, they are encrypted for the current Windows user, and the file
lives under the Git-ignored `runtime/vision/` directory next to the face gallery.
"""

from __future__ import annotations

from pathlib import Path

from contracts import SpeakerCandidate
from registry import PrototypeRegistry

# Literal magic at the head of the file; distinct from the face registry's so a
# file can never be read as the wrong modality.
_MAGIC = b"DSHVOICE1"
_REGISTRY_FILENAME = "speaker_registry.bin"
_TEMP_PREFIX = ".speaker_registry."
# Its own DPAPI entropy label: not a secret, but it keeps the two galleries from
# being interchangeable even under the same Windows user.
_DPAPI_ENTROPY = b"smart-home/vision/speaker-registry/v1"


class SpeakerRegistry(PrototypeRegistry):
    """Voiceprint prototypes for the enrolled members of the household.

    `pack` names the embedding model that produced the vectors. It is required
    rather than defaulted: a mismatch between the enrolled model and the model
    running live must be an error, never a silently bad similarity score.
    """

    magic = _MAGIC
    filename = _REGISTRY_FILENAME
    temp_prefix = _TEMP_PREFIX
    entropy = _DPAPI_ENTROPY

    def __init__(self, runtime_dir: Path, pack: str) -> None:
        super().__init__(runtime_dir, pack)

    def _candidate(self, person_id: str | None, similarity: float | None, margin: float | None):
        return SpeakerCandidate(person_id=person_id, similarity=similarity, margin=margin)
