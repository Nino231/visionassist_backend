"""Q&A grounding guard — deterministic post-LLM safety layer.

This module implements the grounding contract described in Phase 2 §3.
It runs *after* Langflow returns an answer and *before* the response is sent
to the Flutter app.  No LLM is involved — the checks are purely algorithmic.

Safety contract (non-negotiable, mirrors Phase 1 LocalQaRepository):
─────────────────────────────────────────────────────────────────────
1. Token-overlap (Jaccard) between the LLM answer and ocr_text must be ≥
   GROUNDING_MIN_OVERLAP (default 0.3), otherwise fall back.
2. If the question matches a medicine-trigger keyword AND no overlapping
   sentence is found, fall back.
3. If the LLM answer contains a dosage/quantity pattern (e.g. "3 tablet",
   "500 mg") that does NOT appear verbatim in ocr_text, fall back — even if
   the Jaccard overlap is otherwise acceptable.
4. A literal "NOT_FOUND" response from the flow is treated as ungrounded.
5. The safe-fallback phrase is the same Indonesian phrase used by Phase 1.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Constants (ported from Phase 1 LocalQaRepository & TextNormalizer)
# ────────────────────────────────────────────────────────────────────────────

SAFE_FALLBACK = "Saya tidak yakin, silakan tanyakan apoteker."

# Medicine-trigger keywords — ported from Phase 1 LocalQaRepository.
# Used to decide whether a low-overlap answer is dangerous (medicine context)
# and should fall back to the safety phrase.
_MEDICINE_TRIGGERS: list[re.Pattern[str]] = [
    re.compile(r"\bdosis\b", re.IGNORECASE),
    re.compile(r"\bdosage\b", re.IGNORECASE),
    re.compile(r"\bdiminum\b", re.IGNORECASE),
    re.compile(r"\btake\b", re.IGNORECASE),
    re.compile(r"\bkali sehari\b", re.IGNORECASE),
    re.compile(r"\bfrekuensi\b", re.IGNORECASE),
    re.compile(r"\bfrequency\b", re.IGNORECASE),
    re.compile(r"\bberapa\b", re.IGNORECASE),
    re.compile(r"\bhow many\b", re.IGNORECASE),
    re.compile(r"\btablet\b", re.IGNORECASE),
    re.compile(r"\bkapsul\b", re.IGNORECASE),
    re.compile(r"\baturan pakai\b", re.IGNORECASE),
]

# Dosage / quantity pattern: a number followed by a medical unit.
# Must appear *verbatim* in ocr_text for the answer to be considered safe.
_DOSAGE_PATTERN = re.compile(
    r"\d+\s*(?:mg|ml|mcg|gr|gram|tablet|tab|kapsul|kap|kali|x)\b",
    re.IGNORECASE,
)


# ────────────────────────────────────────────────────────────────────────────
# Text normalisation (mirrors Phase 1 TextNormalizer)
# ────────────────────────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    """Lower-case, collapse whitespace, strip non-word characters."""
    text = text.lower()
    text = re.sub(r"[^\w\s.?!]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokenize(text: str) -> set[str]:
    """Return word-token set from normalised text."""
    normalized = _normalize(text)
    if not normalized:
        return set()
    return set(normalized.split())


def jaccard_overlap(a: str, b: str) -> float:
    """Jaccard token similarity, range 0.0–1.0.

    Mirrors ``TextNormalizer.similarity()`` from Phase 1.
    """
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union


# ────────────────────────────────────────────────────────────────────────────
# Individual guards
# ────────────────────────────────────────────────────────────────────────────


def is_medicine_question(question: str) -> bool:
    """Return True if the question matches any medicine-trigger pattern."""
    return any(rx.search(question) for rx in _MEDICINE_TRIGGERS)


def has_hallucinated_dosage(answer: str, ocr_text: str) -> bool:
    """Return True if the answer contains a dosage/unit token not in ocr_text.

    A dosage match like "3 tablet" is only safe if it appears verbatim (case-
    insensitive) in the source OCR text.  Any new dosage number is a
    hallucination and triggers the fallback regardless of overlap score.
    """
    for match in _DOSAGE_PATTERN.finditer(answer):
        token = match.group(0).strip()
        # Verbatim check (case-insensitive) in the raw ocr_text.
        if not re.search(re.escape(token), ocr_text, re.IGNORECASE):
            return True
    return False


def find_best_matching_sentence(answer: str, ocr_text: str) -> str | None:
    """Return the ocr_text sentence with highest token overlap to the answer.

    Used to populate ``source_sentence`` in the response.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.?!\n])\s*", ocr_text) if s.strip()]
    best: str | None = None
    best_score = 0.0
    for sentence in sentences:
        score = jaccard_overlap(answer, sentence)
        if score > best_score:
            best_score = score
            best = sentence
    return best if best_score > 0 else None


# ────────────────────────────────────────────────────────────────────────────
# Main guard entry point
# ────────────────────────────────────────────────────────────────────────────


class GroundingResult:
    """Result of the grounding check."""

    __slots__ = ("answer", "is_grounded", "source_sentence")

    def __init__(
        self,
        answer: str,
        is_grounded: bool,
        source_sentence: str | None,
    ) -> None:
        self.answer = answer
        self.is_grounded = is_grounded
        self.source_sentence = source_sentence


def check_grounding(
    llm_answer: str,
    question: str,
    ocr_text: str,
    min_overlap: float = 0.3,
) -> GroundingResult:
    """Run all grounding guards against an LLM-generated answer.

    Returns a :class:`GroundingResult`.  If any guard fires the answer is
    replaced with the safe fallback and ``is_grounded`` is False.

    Guard order (stops at first rejection):
    1. Literal NOT_FOUND sentinel from the flow.
    2. Empty or whitespace-only answer.
    3. Hallucinated dosage-number check (most dangerous — checked before overlap).
    4. Overlap < min_overlap AND medicine question → always falls back.
    5. Overlap < min_overlap for any question → falls back.
    """
    # Guard 1: NOT_FOUND sentinel
    if llm_answer.strip().upper() == "NOT_FOUND":
        logger.info("grounding_guard_rejected", extra={"reason": "NOT_FOUND_SENTINEL"})
        return GroundingResult(SAFE_FALLBACK, False, None)

    # Guard 2: empty answer
    if not llm_answer.strip():
        logger.info("grounding_guard_rejected", extra={"reason": "EMPTY_ANSWER"})
        return GroundingResult(SAFE_FALLBACK, False, None)

    # Guard 3: hallucinated dosage number
    if has_hallucinated_dosage(llm_answer, ocr_text):
        logger.info("grounding_guard_rejected", extra={"reason": "HALLUCINATED_DOSAGE"})
        return GroundingResult(SAFE_FALLBACK, False, None)

    # Guard 4/5: Jaccard overlap check
    overlap = jaccard_overlap(llm_answer, ocr_text)
    if overlap < min_overlap:
        reason = "LOW_OVERLAP_MEDICINE" if is_medicine_question(question) else "LOW_OVERLAP"
        logger.info("grounding_guard_rejected", extra={"reason": reason, "overlap": round(overlap, 3)})
        return GroundingResult(SAFE_FALLBACK, False, None)

    # Passed all guards — find supporting sentence for transparency.
    source = find_best_matching_sentence(llm_answer, ocr_text)
    return GroundingResult(llm_answer.strip(), True, source)
