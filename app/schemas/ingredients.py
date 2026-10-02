"""Pydantic request / response schemas for the Ingredient Summarizer endpoint.

Flutter ↔ Backend field mapping
───────────────────────────────────────────────────────────────────────────────
The backend receives the *already-extracted* structured fields from the Flutter
app's ``LocalIngredientRepository``.  It returns only a rephrased ``summary``.
The client owns ``allergens``, ``negatedAllergens``, ``expiryDate``, and
``prices`` — these are never changed by this service.

Flutter IngredientAnalysis   │ Python IngredientSummarizeRequest (input)
─────────────────────────────┼────────────────────────────────────────────────
allergens     (List<String>) │ allergens          (list[str])
negatedAllergens             │ negated_allergens  (list[str])
expiryDate    (String?)      │ expiry_date        (str | None)
prices        (List<String>) │ prices             (list[str])
(raw OCR text, not in entity)│ ocr_text           (str)          ← for guard

Python IngredientSummarizeResponse (output)
─────────────────────────────────────────────
summary  (str, ≤ 15 words)   → merged into IngredientAnalysis.summary
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IngredientSummarizeRequest(BaseModel):
    """Incoming ingredient-summarize request.

    The Flutter app sends the already-extracted structured data plus the raw
    OCR text.  The LLM is asked only to *rephrase* the summary — it must not
    add, remove, or alter the structured fields.
    """

    ocr_text: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Raw OCR text as extracted by the Flutter app",
    )
    allergens: list[str] = Field(
        default_factory=list,
        description="Detected allergen names (not negated), from the Flutter local extractor",
    )
    negated_allergens: list[str] = Field(
        default_factory=list,
        description="Allergen names explicitly negated in the text",
    )
    expiry_date: str | None = Field(
        None,
        description="Parsed expiry date string, or null",
    )
    prices: list[str] = Field(
        default_factory=list,
        description="Detected prices as formatted strings",
    )


class IngredientSummarizeResponse(BaseModel):
    """Response — only the rephrased summary.

    Mapping:
      summary → merged into Flutter's IngredientAnalysis.summary

    The Flutter client retains its own locally-extracted allergens /
    negatedAllergens / expiryDate / prices and does NOT replace them with
    anything from this response.
    """

    summary: str = Field(..., description="Rephrased ≤15-word Indonesian summary")
