"""Ingredient summary guard — deterministic post-LLM safety layer.

This module implements the ingredient-summary safety contract from Phase 2 §3.
It runs after Langflow returns a rephrased summary and before the response is
returned to the Flutter app.

Safety contract
───────────────
1. Word count must be ≤ 15.
2. The summary must not introduce an allergen keyword that is not already
   present in the request's ``allergens`` or ``negated_allergens`` lists.
   This prevents the LLM from fabricating allergen claims.

When a guard fires the caller should fall back to a locally-composed summary
built from the input fields — never fail the whole request.

Allergen keyword map (synced with Flutter AllergenChecker):
  'Kacang tanah' → peanut / groundnut / arachis / mentega kacang / peanut butter
  'Kacang pohon' → almond / mete / cashew / hazelnut / walnut / pecan / ... / tree nut
  'Gluten'       → gluten / gandum / terigu / wheat / rye / barley / oat / spelt / malt
  'Susu'         → susu / milk / laktosa / lactose / dairy / whey / casein / keju / mentega / krim / yogurt
  'Telur'        → telur / egg / albumin / ovalbumin / lisozim / mayones / mayonnaise
  'Krustasea'    → udang / kepiting / rajungan / lobster / shrimp / crab / prawn / ebi / terasi / petis udang
  'Moluska'      → kerang / tiram / cumi / sotong / gurita / clam / oyster / squid / octopus / mussel
  'Ikan'         → ikan / fish / anchovy / teri / salmon / tuna / tongkol / surimi / kecap ikan / fish sauce
  'Kedelai'      → kedelai / soy / soya / soybean / tofu / tahu / tempe / kecap / miso / edamame / soy lecithin
  'Wijen'        → wijen / sesame / tahini / minyak wijen / sesame oil / sesame extract
  'Sulfit'       → sulfit / sulfite / sulphite / sulfur dioksida / sulfur dioxide / so2 / metabisulfit / ins 220-228
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Allergen keyword map — synced with Flutter AllergenChecker._keywords.
_ALLERGEN_KEYWORDS: dict[str, list[str]] = {
    "Kacang tanah": [
        "kacang tanah", "peanut", "groundnut", "arachis",
        "mentega kacang", "peanut butter",
    ],
    "Kacang pohon": [
        "almond", "kacang mete", "kacang mede", "mete", "cashew",
        "hazelnut", "filbert", "kenari", "walnut", "pecan",
        "brazil nut", "pistachio", "pistasio", "macadamia",
        "queensland nut", "chestnut", "pine nut", "tree nut", "kacang pohon",
    ],
    "Gluten": [
        "gluten", "gandum", "terigu", "wheat", "rye", "gandum hitam",
        "barley", "jelai", "oat", "oats", "havermut", "spelt", "malt",
    ],
    "Susu": [
        "susu", "milk", "laktosa", "lactose", "dairy", "whey", "casein",
        "kasein", "keju", "cheese", "mentega", "butter", "krim", "cream",
        "yogurt", "yoghurt",
    ],
    "Telur": [
        "telur", "egg", "albumin", "ovalbumin", "lisozim",
        "mayones", "mayonnaise",
    ],
    "Krustasea": [
        "udang", "kepiting", "rajungan", "lobster", "shrimp", "crab",
        "prawn", "crustacean", "krustasea", "ebi", "terasi", "petis udang",
    ],
    "Moluska": [
        "kerang", "tiram", "cumi", "sotong", "gurita", "remis", "bekicot",
        "siput", "clam", "oyster", "squid", "octopus", "mussel",
        "mollusc", "mollusk", "moluska",
    ],
    "Ikan": [
        "ikan", "fish", "anchovy", "teri", "salmon", "tuna", "tongkol",
        "surimi", "kecap ikan", "fish sauce",
    ],
    "Kedelai": [
        "kedelai", "soy", "soya", "soybean", "tofu", "tahu", "tempe",
        "tempeh", "kecap", "miso", "edamame", "lesitin kedelai", "soy lecithin",
    ],
    "Wijen": [
        "wijen", "sesame", "tahini", "minyak wijen",
        "sesame oil", "sesame extract",
    ],
    "Sulfit": [
        "sulfit", "sulfite", "sulphite", "sulfur dioksida", "sulfur dioxide",
        "so2", "metabisulfit", "metabisulfite", "bisulfit",
        "ins 220", "ins 221", "ins 222", "ins 223", "ins 224",
        "ins 225", "ins 226", "ins 227", "ins 228",
    ],
}

MAX_SUMMARY_WORDS = 15


def _word_count(text: str) -> int:
    return len(text.strip().split())


def _canonical_allergens_in_text(text: str) -> set[str]:
    """Return the set of canonical allergen names found in *text*."""
    lower = text.lower()
    found: set[str] = set()
    for canonical, keywords in _ALLERGEN_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                found.add(canonical)
                break
    return found


def _normalise_to_canonical(names: list[str]) -> set[str]:
    """Map a list of allergen names (as returned by the Flutter app) to
    canonical form.

    The Flutter app stores canonical names such as "Kacang", "Gluten", etc.
    We also accept keywords directly (e.g. "peanut") by checking the keyword
    map.
    """
    result: set[str] = set()
    for name in names:
        lower = name.lower()
        # Direct canonical match (case-insensitive).
        for canonical in _ALLERGEN_KEYWORDS:
            if canonical.lower() == lower:
                result.add(canonical)
                break
        else:
            # Try keyword match.
            for canonical, keywords in _ALLERGEN_KEYWORDS.items():
                if lower in keywords:
                    result.add(canonical)
                    break
    return result


def check_ingredient_summary(
    summary: str,
    allergens: list[str],
    negated_allergens: list[str],
) -> tuple[bool, str]:
    """Validate a rephrased ingredient summary against the safety contract.

    Parameters
    ----------
    summary:
        Rephrased summary returned by the LLM.
    allergens:
        Detected allergen names (not negated) from the Flutter local extractor.
    negated_allergens:
        Allergen names explicitly negated, from the Flutter local extractor.

    Returns
    -------
    (ok, reason)
        ``ok=True`` means the summary passed all guards.
        ``ok=False`` means it should be discarded; ``reason`` is the guard
        rejection code for structured logging (never contains user content).
    """
    # Guard 1: word count
    if _word_count(summary) > MAX_SUMMARY_WORDS:
        logger.info(
            "ingredient_guard_rejected",
            extra={"reason": "SUMMARY_TOO_LONG", "word_count": _word_count(summary)},
        )
        return False, "SUMMARY_TOO_LONG"

    # Guard 2: allergen leak — does the summary introduce a new allergen
    #           not already in the caller's allergens / negated_allergens?
    known_canonical = _normalise_to_canonical(allergens) | _normalise_to_canonical(negated_allergens)
    summary_canonical = _canonical_allergens_in_text(summary)
    leaked = summary_canonical - known_canonical

    if leaked:
        logger.info(
            "ingredient_guard_rejected",
            extra={"reason": "ALLERGEN_LEAK", "leaked_count": len(leaked)},
        )
        return False, "ALLERGEN_LEAK"

    return True, "OK"


def build_fallback_summary(
    allergens: list[str],
    negated_allergens: list[str],
    expiry_date: str | None,
    prices: list[str],
) -> str:
    """Compose a ≤15-word deterministic summary from the input fields.

    Used when the LLM's rephrased summary fails the guard.
    The format mirrors the example in §2: "Mengandung X, bebas Y, kedaluwarsa Z."
    """
    parts: list[str] = []

    if allergens:
        parts.append("Mengandung " + ", ".join(a.lower() for a in allergens))

    if negated_allergens:
        parts.append("bebas " + ", ".join(a.lower() for a in negated_allergens))

    if expiry_date:
        parts.append(f"kedaluwarsa {expiry_date}")

    if prices:
        parts.append(", ".join(prices))

    if not parts:
        return "Tidak ada informasi relevan ditemukan."

    summary = ", ".join(parts) + "."

    # Trim to 15 words if somehow the fallback is too long (unlikely).
    words = summary.split()
    if len(words) > MAX_SUMMARY_WORDS:
        summary = " ".join(words[:MAX_SUMMARY_WORDS]) + "."

    return summary
