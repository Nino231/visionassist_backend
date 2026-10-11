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

import pytest

from app.guards.grounding import (
    NO_ANSWER_FALLBACK,
    SAFE_FALLBACK,
    answer_coverage,
    check_grounding,
    fallback_for,
    has_hallucinated_dosage,
    has_hallucinated_number,
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
        min_coverage=0.7,
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
        min_coverage=0.7,
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
        min_coverage=0.1,  # low threshold so coverage alone would pass
    )
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK


def test_non_medicine_low_overlap_falls_back() -> None:
    """Non-medicine question with very low overlap → neutral fallback (no pharmacist)."""
    ocr = "Teh hijau organik dari pegunungan Jawa."
    answer = "Dibuat dari bahan kimia pilihan."  # unrelated
    result = check_grounding(
        llm_answer=answer,
        question="Dari mana bahan-bahannya?",
        ocr_text=ocr,
        min_coverage=0.7,
    )
    assert result.is_grounded is False
    assert result.answer == NO_ANSWER_FALLBACK


def test_not_found_sentinel_falls_back() -> None:
    """Literal NOT_FOUND from the flow → safe fallback."""
    result = check_grounding(
        llm_answer="NOT_FOUND",
        question=MEDICINE_Q,
        ocr_text=OCR_MEDICINE,
        min_coverage=0.7,
    )
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK


def test_empty_answer_falls_back() -> None:
    result = check_grounding(
        llm_answer="   ",
        question=MEDICINE_Q,
        ocr_text=OCR_MEDICINE,
        min_coverage=0.7,
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
        min_coverage=0.7,
    )
    assert result.is_grounded is True


# ────────────────────────────────────────────────────────────────────────────
# Refusal phrase depends on the question (pharmacist only for medicine)
# ────────────────────────────────────────────────────────────────────────────

GENERAL_Q = "Jelaskan apa yang saya lihat"
GENERAL_OCR = "daftar allergen di source code"


def test_fallback_for_picks_phrase_by_question_type() -> None:
    assert fallback_for("Berapa dosis obat ini?") == SAFE_FALLBACK
    assert fallback_for(GENERAL_Q) == NO_ANSWER_FALLBACK
    assert "apoteker" not in NO_ANSWER_FALLBACK


def test_not_found_for_general_question_is_neutral() -> None:
    result = check_grounding("NOT_FOUND", GENERAL_Q, GENERAL_OCR, 0.3)
    assert result.is_grounded is False
    assert result.answer == NO_ANSWER_FALLBACK


def test_empty_answer_for_general_question_is_neutral() -> None:
    result = check_grounding("  ", GENERAL_Q, GENERAL_OCR, 0.3)
    assert result.answer == NO_ANSWER_FALLBACK


def test_medicine_not_found_keeps_pharmacist_phrase() -> None:
    result = check_grounding("NOT_FOUND", "Berapa kali sehari diminum?", GENERAL_OCR, 0.3)
    assert result.answer == SAFE_FALLBACK


def test_hallucinated_dosage_always_uses_pharmacist_phrase() -> None:
    """A made-up dose is a medicine matter even if the question wasn't phrased as one."""
    result = check_grounding(
        llm_answer="Minum 500 mg sekarang.",
        question=GENERAL_Q,
        ocr_text="Vitamin C untuk daya tahan tubuh.",
        min_coverage=0.0,
    )
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK


def test_general_question_read_back_of_ocr_text_is_grounded() -> None:
    """The prompt's 'read it back' rule must survive the overlap guard."""
    result = check_grounding(
        llm_answer="Daftar allergen di source code.",
        question=GENERAL_Q,
        ocr_text=GENERAL_OCR,
        min_coverage=0.7,
    )
    assert result.is_grounded is True
    assert result.source_sentence == GENERAL_OCR


# ────────────────────────────────────────────────────────────────────────────
# Coverage formula (replaces Jaccard against the whole OCR text)
# ────────────────────────────────────────────────────────────────────────────

LONG_LABEL = (
    "Mengandung susu dan telur. Diproduksi oleh PT Contoh Makmur, Jakarta. "
    "Netto 250g. EXP 10/2027. Harga Rp15.000. Simpan di tempat sejuk dan kering."
)


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("Kapan kedaluwarsa?", "10/2027"),
        ("Berapa harganya?", "Rp15.000"),
        ("Berapa netto-nya?", "250g"),
        ("Apa saja isinya?", "Mengandung susu dan telur."),
        ("Siapa produsennya?", "PT Contoh Makmur"),
        ("Di mana disimpan?", "Simpan di tempat sejuk dan kering."),
        ("Apakah mengandung susu?", "Ya, mengandung susu."),
    ],
)
def test_correct_short_answers_on_a_long_label_are_grounded(question: str, answer: str) -> None:
    """Regression: Jaccard vs the whole label rejected all of these (0.00-0.18)."""
    result = check_grounding(answer, question, LONG_LABEL, 0.7)
    assert result.is_grounded is True, (question, answer)
    assert result.answer == answer


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("Apakah mengandung kacang?", "Tidak mengandung kacang."),  # invented claim
        ("Apakah mengandung susu?", "Tidak mengandung susu."),  # flipped meaning
        ("Kapan kedaluwarsa?", "12/2028"),  # made-up date
        ("Berapa harganya?", "Rp25.000"),  # made-up price
        ("Apa warnanya?", "Kemasannya berwarna merah muda."),  # not in the text
        ("Apa ini?", "Tidak."),  # nothing verifiable
    ],
)
def test_invented_answers_on_a_long_label_are_rejected(question: str, answer: str) -> None:
    result = check_grounding(answer, question, LONG_LABEL, 0.7)
    assert result.is_grounded is False, (question, answer)
    assert result.answer in (SAFE_FALLBACK, NO_ANSWER_FALLBACK)


def test_answer_coverage_ignores_ocr_length() -> None:
    short = "EXP 10/2027"
    assert answer_coverage("10/2027", short) == answer_coverage("10/2027", LONG_LABEL) == 1.0


def test_answer_coverage_counts_content_words_only() -> None:
    # "yang", "di" and "dan" are function words: padding with them changes nothing.
    assert answer_coverage("susu dan yang di telur", LONG_LABEL) == 1.0
    assert answer_coverage("kacang dan yang di telur", LONG_LABEL) == 0.5


def test_answer_coverage_of_empty_or_function_only_answer_is_zero() -> None:
    assert answer_coverage("", LONG_LABEL) == 0.0
    assert answer_coverage("yang dan di", LONG_LABEL) == 0.0


def test_negation_words_are_not_ignored() -> None:
    assert answer_coverage("tidak", "Mengandung susu") == 0.0
    assert answer_coverage("tanpa gula", "Tanpa gula tambahan") == 1.0


def test_hallucinated_number_compares_digit_runs() -> None:
    assert has_hallucinated_number("Rp 15.000", "Harga Rp15.000") is False
    assert has_hallucinated_number("10/2027", LONG_LABEL) is False
    assert has_hallucinated_number("Rp25.000", LONG_LABEL) is True
    assert has_hallucinated_number("Diminum 8 kali", "Diminum 3 kali sehari") is True
    assert has_hallucinated_number("tidak ada angka", LONG_LABEL) is False
    # "3" must not match inside "13": whole runs only.
    assert has_hallucinated_number("3", "Berat 13 gram") is True


def test_hallucinated_number_with_berapa_question_uses_pharmacist_phrase() -> None:
    result = check_grounding("Rp25.000", "Berapa harganya?", "Harga Rp15.000", 0.0)
    assert result.is_grounded is False
    assert result.answer == SAFE_FALLBACK  # "berapa" is a medicine trigger word


def test_hallucinated_number_neutral_phrase_when_not_a_medicine_question() -> None:
    result = check_grounding("Diproduksi 2031", "Kapan diproduksi?", "Diproduksi 2025", 0.0)
    assert result.is_grounded is False
    assert result.answer == NO_ANSWER_FALLBACK


def test_threshold_still_configurable() -> None:
    """A stricter threshold rejects an answer with one unverifiable content word."""
    answer = "Mengandung susu pasteurisasi."  # 2 of 3 content words in the label
    assert check_grounding(answer, "Isinya?", LONG_LABEL, 0.6).is_grounded is True
    assert check_grounding(answer, "Isinya?", LONG_LABEL, 0.9).is_grounded is False
