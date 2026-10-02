"""Unit tests for app/guards/ingredient_guard.py.

Covers:
  • Summary within 15 words — passes.
  • Summary exceeding 15 words — rejected.
  • Summary that leaks an allergen not in the input lists — rejected.
  • Summary that uses allergens already in the input lists — passes.
  • Fallback summary builder produces ≤ 15 words.
"""

from __future__ import annotations

from app.guards.ingredient_guard import (
    MAX_SUMMARY_WORDS,
    build_fallback_summary,
    check_ingredient_summary,
)

# ────────────────────────────────────────────────────────────────────────────
# check_ingredient_summary
# ────────────────────────────────────────────────────────────────────────────


def test_valid_summary_passes() -> None:
    """Summary ≤ 15 words and no new allergens — should pass."""
    ok, reason = check_ingredient_summary(
        summary="Mengandung kacang, bebas gluten, kedaluwarsa Des 2026.",
        allergens=["Kacang"],
        negated_allergens=["Gluten"],
    )
    assert ok is True
    assert reason == "OK"


def test_summary_too_long_rejected() -> None:
    """Summary with > 15 words must be rejected."""
    long_summary = (
        "Produk ini mengandung kacang tanah dan dapat menyebabkan "
        "alergi pada sebagian orang yang sensitif terhadap kacang."
    )
    word_count = len(long_summary.split())
    assert word_count > MAX_SUMMARY_WORDS, f"Expected > {MAX_SUMMARY_WORDS} words, got {word_count}"

    ok, reason = check_ingredient_summary(
        summary=long_summary,
        allergens=["Kacang"],
        negated_allergens=[],
    )
    assert ok is False
    assert reason == "SUMMARY_TOO_LONG"


def test_allergen_leak_rejected() -> None:
    """LLM introduces 'susu' allergen not present in input lists — must be rejected."""
    ok, reason = check_ingredient_summary(
        summary="Mengandung kacang dan susu.",
        allergens=["Kacang"],          # Kacang is known
        negated_allergens=[],          # Susu is NOT in any input list
    )
    assert ok is False
    assert reason == "ALLERGEN_LEAK"


def test_known_allergen_in_summary_passes() -> None:
    """Summary mentions an allergen already present in the allergens list — OK."""
    ok, reason = check_ingredient_summary(
        summary="Mengandung kacang dan telur.",
        allergens=["Kacang", "Telur"],
        negated_allergens=[],
    )
    assert ok is True


def test_negated_allergen_in_summary_passes() -> None:
    """Summary mentions an allergen that appears in negated_allergens — OK."""
    ok, reason = check_ingredient_summary(
        summary="Bebas gluten dan susu.",
        allergens=[],
        negated_allergens=["Gluten", "Susu"],
    )
    assert ok is True


def test_no_allergens_clean_summary_passes() -> None:
    """Summary with no allergen mentions — always passes allergen guard."""
    ok, reason = check_ingredient_summary(
        summary="Kedaluwarsa Januari 2027.",
        allergens=[],
        negated_allergens=[],
    )
    assert ok is True


def test_keyword_form_allergen_leak() -> None:
    """LLM uses keyword 'milk' (canonical: Susu) not in input lists — leak."""
    ok, reason = check_ingredient_summary(
        summary="Contains milk and peanut.",
        allergens=["Kacang"],          # peanut/Kacang is known; milk/Susu is not
        negated_allergens=[],
    )
    assert ok is False
    assert reason == "ALLERGEN_LEAK"


# ────────────────────────────────────────────────────────────────────────────
# build_fallback_summary
# ────────────────────────────────────────────────────────────────────────────


def test_fallback_summary_word_count() -> None:
    summary = build_fallback_summary(
        allergens=["Kacang", "Telur"],
        negated_allergens=["Gluten"],
        expiry_date="12/2026",
        prices=["Rp 15.000"],
    )
    assert len(summary.split()) <= MAX_SUMMARY_WORDS


def test_fallback_summary_empty_inputs() -> None:
    summary = build_fallback_summary(
        allergens=[],
        negated_allergens=[],
        expiry_date=None,
        prices=[],
    )
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_fallback_summary_contains_allergen() -> None:
    summary = build_fallback_summary(
        allergens=["Kacang"],
        negated_allergens=[],
        expiry_date=None,
        prices=[],
    )
    assert "kacang" in summary.lower()


def test_fallback_summary_expiry_present() -> None:
    summary = build_fallback_summary(
        allergens=[],
        negated_allergens=[],
        expiry_date="06/2025",
        prices=[],
    )
    assert "06/2025" in summary
