"""Q&A grounding guard — deterministic post-LLM safety layer.

This module implements the grounding contract described in Phase 2 §3.
It runs *after* Langflow returns an answer and *before* the response is sent
to the Flutter app.  No LLM is involved — the checks are purely algorithmic.

Safety contract (non-negotiable, mirrors Phase 1 LocalQaRepository):
─────────────────────────────────────────────────────────────────────
1. Coverage: the share of the answer's content words that appear in ocr_text
   must be ≥ GROUNDING_MIN_COVERAGE (default 0.7), otherwise fall back.
   (Jaccard against the *whole* OCR text was dropped: it punished short,
   correct answers such as "10/2027" on a long label.)
2. If the question matches a medicine-trigger keyword AND no overlapping
   sentence is found, fall back.
3. If the LLM answer contains a dosage/quantity pattern (e.g. "3 tablet",
   "500 mg") that does NOT appear verbatim in ocr_text, fall back — even if
   the coverage is otherwise acceptable.  Any other number in the answer
   (date, price, quantity) must likewise appear in ocr_text.
4. A literal "NOT_FOUND" response from the flow is treated as ungrounded.
5. The "ask the pharmacist" phrase is the same Indonesian phrase used by Phase 1
   and is used for medicine questions (and for any hallucinated dosage).  For
   every other question the neutral NO_ANSWER_FALLBACK is returned instead,
   because telling someone to ask a pharmacist about e.g. a shop sign is wrong.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Constants (ported from Phase 1 LocalQaRepository & TextNormalizer)
# ────────────────────────────────────────────────────────────────────────────

SAFE_FALLBACK = "Saya tidak yakin, silakan tanyakan apoteker."

# Neutral phrase for non-medicine questions the text cannot answer.
NO_ANSWER_FALLBACK = "Saya tidak menemukan jawabannya di teks yang terbaca."

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


# Function words ignored when measuring coverage so that padding an answer with
# "yang", "dan", "the" ... can neither raise nor lower the score.  Words that
# change meaning (tidak, bukan, tanpa, bebas, no, not, free) are NOT listed.
_STOPWORDS = frozenset(
    """
    yang di ke dari dan atau ini itu adalah ialah dengan untuk pada dalam
    ya saya anda kamu akan juga ada sebuah suatu oleh sebagai
    the a an is are of in on at to and or it this that yes there
    """.split()
)

_WORD = re.compile(r"\w+")
_DIGITS = re.compile(r"\d+")


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def answer_coverage(answer: str, ocr_text: str) -> float:
    """Share (0.0–1.0) of the answer's content words that occur in ``ocr_text``.

    Unlike Jaccard this does not depend on how long the OCR text is.  An answer
    with no content words scores 0.0 (nothing in it can be verified).
    """
    words = _content_words(answer)
    if not words:
        return 0.0
    source = set(_WORD.findall(ocr_text.lower()))
    return sum(1 for w in words if w in source) / len(words)


def has_hallucinated_number(answer: str, ocr_text: str) -> bool:
    """Return True if the answer contains a number that ocr_text does not.

    Compares digit runs, so "Rp 15.000" matches "Rp15.000" and "10/2027"
    matches "EXP 10/2027", but a made-up "12/2028" or "8 tablet" is caught.
    """
    known = set(_DIGITS.findall(ocr_text))
    return any(run not in known for run in _DIGITS.findall(answer))


def is_medicine_question(question: str) -> bool:
    """Return True if the question matches any medicine-trigger pattern."""
    return any(rx.search(question) for rx in _MEDICINE_TRIGGERS)


def fallback_for(question: str) -> str:
    """Pick the refusal phrase: pharmacist for medicine, neutral otherwise."""
    return SAFE_FALLBACK if is_medicine_question(question) else NO_ANSWER_FALLBACK


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
    # Split after sentence punctuation + whitespace (not inside "15.000") or at line breaks.
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+|\n+", ocr_text) if s.strip()]
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
    min_coverage: float = 0.7,
) -> GroundingResult:
    """Run all grounding guards against an LLM-generated answer.

    Returns a :class:`GroundingResult`.  If any guard fires the answer is
    replaced with a fallback phrase (see :func:`fallback_for`) and
    ``is_grounded`` is False.

    Guard order (stops at first rejection):
    1. Literal NOT_FOUND sentinel from the flow.
    2. Empty or whitespace-only answer.
    3. Hallucinated dosage-number check (most dangerous — checked before coverage).
    4. Any other number not present in ocr_text.
    5. Coverage < min_coverage → falls back.
    """
    # Guard 1: NOT_FOUND sentinel
    if llm_answer.strip().upper() == "NOT_FOUND":
        logger.info("grounding_guard_rejected", extra={"reason": "NOT_FOUND_SENTINEL"})
        return GroundingResult(fallback_for(question), False, None)

    # Guard 2: empty answer
    if not llm_answer.strip():
        logger.info("grounding_guard_rejected", extra={"reason": "EMPTY_ANSWER"})
        return GroundingResult(fallback_for(question), False, None)

    # Guard 3: hallucinated dosage number
    if has_hallucinated_dosage(llm_answer, ocr_text):
        logger.info("grounding_guard_rejected", extra={"reason": "HALLUCINATED_DOSAGE"})
        return GroundingResult(SAFE_FALLBACK, False, None)

    # Guard 4: any other made-up number (date, price, quantity)
    if has_hallucinated_number(llm_answer, ocr_text):
        logger.info("grounding_guard_rejected", extra={"reason": "HALLUCINATED_NUMBER"})
        return GroundingResult(fallback_for(question), False, None)

    # Guard 5: coverage of the answer's content words by the OCR text
    coverage = answer_coverage(llm_answer, ocr_text)
    if coverage < min_coverage:
        reason = "LOW_COVERAGE_MEDICINE" if is_medicine_question(question) else "LOW_COVERAGE"
        logger.info("grounding_guard_rejected", extra={"reason": reason, "coverage": round(coverage, 3)})
        return GroundingResult(fallback_for(question), False, None)

    # Passed all guards — find supporting sentence for transparency.
    source = find_best_matching_sentence(llm_answer, ocr_text)
    return GroundingResult(llm_answer.strip(), True, source)
