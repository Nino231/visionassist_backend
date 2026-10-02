"""Unit tests for app/guards/grounding.py.

Covers all the cases required by Phase 2 §8:
  • Correctly grounded medicine answer (passes).
  • Ungrounded medicine question (falls back).
  • Hallucinated dosage number (falls back even with good overlap).
  • Non-medicine low-overlap case (falls back).
  • NOT_FOUND sentinel (falls back).
  • Empty answer (falls back).
"""

from __future__ import annotations

from app.guards.grounding import (
    SAFE_FALLBACK,
    check_grounding,
    has_hallucinated_dosage,
    is_medicine_question,
    jaccard_overlap,
)

# ────────────────────────────────────────────────────────────────────────────
# jaccard_overlap unit tests
# ────────────────────────────────────────────────────────────────────────────


def test_jaccard_identical() -> None:
    assert jaccard_overlap("paracetamol obat", "paracetamol obat") == 1.0


def test_jaccard_no_overlap() -> None:
    assert jaccard_overlap("kacang susu", "ikan tuna salmon") == 0.0


def test_jaccard_partial_overlap() -> None:
    score = jaccard_overlap("diminum tiga kali sehari", "tiga kapsul sehari pagi")
    assert 0.0 < score < 1.0


def test_jaccard_empty_strings() -> None:
    assert jaccard_overlap("", "") == 1.0


def test_jaccard_one_empty() -> None:
    assert jaccard_overlap("hello", "") == 0.0


# ────────────────────────────────────────────────────────────────────────────
# is_medicine_question
# ────────────────────────────────────────────────────────────────────────────


def test_medicine_question_dosis() -> None:
    assert is_medicine_question("Berapa dosis obat ini?") is True


def test_medicine_question_kali_sehari() -> None:
    assert is_medicine_question("Minum berapa kali sehari?") is True


def test_medicine_question_aturan_pakai() -> None:
    assert is_medicine_question("Apa aturan pakai-nya?") is True


def test_non_medicine_question() -> None:
    assert is_medicine_question("Di mana produk ini dibuat?") is False


# ────────────────────────────────────────────────────────────────────────────
# has_hallucinated_dosage
# ────────────────────────────────────────────────────────────────────────────


def test_no_dosage_in_answer() -> None:
    assert has_hallucinated_dosage("Diminum setelah makan.", "Diminum setelah makan.") is False


def test_dosage_present_in_ocr() -> None:
    """Dosage token appears verbatim in OCR text → not a hallucination."""
    ocr = "Paracetamol 500mg. Diminum 3 tablet sehari."
    answer = "Diminum 3 tablet sehari setelah makan."
    assert has_hallucinated_dosage(answer, ocr) is False


def test_dosage_absent_from_ocr() -> None:
    """Dosage token NOT in OCR text → hallucination detected."""
    ocr = "Paracetamol. Diminum setelah makan."
    answer = "Diminum 2 tablet sehari."
    assert has_hallucinated_dosage(answer, ocr) is True


def test_mg_unit_absent_from_ocr() -> None:
    ocr = "Diminum setelah makan."
    answer = "Dosis 500mg per hari."
    assert has_hallucinated_dosage(answer, ocr) is True


# ────────────────────────────────────────────────────────────────────────────
# check_grounding — the main guard
# ────────────────────────────────────────────────────────────────────────────


OCR_MEDICINE = (
    "Paracetamol 500mg. Diminum 3 kali sehari setelah makan. "
    "Jangan melebihi 8 tablet dalam 24 jam."
)
MEDICINE_Q = "Berapa kali minum obat ini sehari?"


def test_grounded_medicine_answer_passes() -> None:
    """LLM returns a well-grounded answer — should pass all guards."""
    llm_answer = "Diminum 3 kali sehari setelah makan."
    result = check_grounding(
        llm_answer=llm_answer,
        question=MEDICINE_Q,
        ocr_text=OCR_MEDICINE,
        min_overlap=0.3,
    )
    assert result.is_grounded is True
    assert result.answer == llm_answer
    assert result.source_sentence is not None


def test_ungrounded_medicine_question_falls_back() -> None:
    """LLM returns an answer unrelated to the OCR text → safe fallback."""
    llm_answer = "Obat ini aman untuk anak-anak."  # completely off-topic
    result = check_grounding(
        llm_answer=llm_answer,
        question=MEDICINE_Q,
        ocr_text=OCR_MEDICINE,
        min_overlap=0.3,
    )
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK
    assert result.source_sentence is None


def test_hallucinated_dosage_falls_back_despite_decent_overlap() -> None:
    """Even if overlap is acceptable, a made-up dosage number must fall back."""
    # OCR has no mention of "2 tablet" — LLM hallucinated it.
    ocr = "Paracetamol diminum setelah makan."
    answer = "Diminum 2 tablet setelah makan."  # "2 tablet" not in OCR
    result = check_grounding(
        llm_answer=answer,
        question="Berapa tablet yang harus diminum?",
        ocr_text=ocr,
        min_overlap=0.1,  # low threshold so overlap alone would pass
    )
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK


def test_non_medicine_low_overlap_falls_back() -> None:
    """Non-medicine question with very low overlap → fallback (not safe phrase necessarily)."""
    ocr = "Teh hijau organik dari pegunungan Jawa."
    answer = "Dibuat dari bahan kimia pilihan."  # unrelated
    result = check_grounding(
        llm_answer=answer,
        question="Dari mana bahan-bahannya?",
        ocr_text=ocr,
        min_overlap=0.3,
    )
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK


def test_not_found_sentinel_falls_back() -> None:
    """Literal NOT_FOUND from the flow → safe fallback."""
    result = check_grounding(
        llm_answer="NOT_FOUND",
        question=MEDICINE_Q,
        ocr_text=OCR_MEDICINE,
        min_overlap=0.3,
    )
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK


def test_empty_answer_falls_back() -> None:
    result = check_grounding(
        llm_answer="   ",
        question=MEDICINE_Q,
        ocr_text=OCR_MEDICINE,
        min_overlap=0.3,
    )
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK


def test_grounded_non_medicine_answer() -> None:
    """Non-medicine question with high overlap → passes."""
    ocr = "Teh hijau organik dari pegunungan Jawa. Dipetik secara alami."
    answer = "Teh ini berasal dari pegunungan Jawa."
    result = check_grounding(
        llm_answer=answer,
        question="Dari mana teh ini berasal?",
        ocr_text=ocr,
        min_overlap=0.3,
    )
    assert result.is_grounded is True
