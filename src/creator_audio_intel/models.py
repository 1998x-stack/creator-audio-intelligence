"""Value objects for structured, auditable pipeline data."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class VideoCandidate:
    video_id: str
    url: str
    title: str = ""
    published_at: str | None = None


@dataclass(frozen=True)
class Segment:
    segment_id: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class CaptureResult:
    wav_path: str
    sha256: str
    duration_ms: int
    rms: float
    qc_ok: bool
    qc_reason: str
    peak: int = 0
    silence_ratio: float = 0.0
    clipped_fraction: float = 0.0


ClaimKind = Literal["fact", "causal", "opinion", "recommendation", "forecast"]
