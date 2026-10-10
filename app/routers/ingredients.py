"""POST /v1/ingredients/summarize — Ingredient & Menu Summarizer proxy.

Flow
────
1. Validate/sanitize request (Pydantic).
2. Serialize the structured fields as a JSON string and pass it as
   ``input_value`` to the Langflow ingredient_summary flow (Chat Input →
   Prompt Template architecture).
3. Run the ingredient guard on the rephrased summary.
4. If the guard rejects the summary, fall back to a locally-composed summary
   built from the input fields (never fail the whole request for this).
5. Return IngredientSummarizeResponse with only ``summary``.

The Flutter client owns allergens/negatedAllergens/expiryDate/prices.
This endpoint returns only a rephrased summary — it is never the source
of truth for structured fields.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from ..config import settings
from ..deps import rate_limit
from ..guards.ingredient_guard import (
    build_fallback_summary,
    check_ingredient_summary,
)
from ..langflow_client import LangflowClient, LangflowUnavailableError
from ..schemas.ingredients import IngredientSummarizeRequest, IngredientSummarizeResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/ingredients/summarize",
    response_model=IngredientSummarizeResponse,
    summary="Ingredient / menu summarizer — rephrase summary only",
    tags=["Ingredients"],
)
async def ingredients_summarize(
    body: IngredientSummarizeRequest,
    _: Annotated[None, Depends(rate_limit)],
) -> IngredientSummarizeResponse:
    """Rephrase an ingredient summary using the LLM.

    The LLM is given the already-extracted structured fields and asked only
    to produce a polished ≤15-word Indonesian summary.  The guard ensures
    the rephrased summary cannot introduce allergen claims not present in
    the request.

    If the LLM or the guard fails, a deterministic fallback summary is
    returned instead of an error — the Flutter app's UX is not interrupted.

    Structured logs record method, path, status, and guard-rejection reason
    codes only — ``ocr_text`` is never logged.
    """
    client = LangflowClient(settings)

    # The flow uses Chat Input → Prompt Template architecture.
    # All prompt variables are packed into a single JSON string in input_value,
    # which the Chat Input node forwards as the {payload} variable.
    allergen_str = ", ".join(body.allergens) if body.allergens else "tidak ada"
    negated_str = ", ".join(body.negated_allergens) if body.negated_allergens else "tidak ada"
    expiry_str = body.expiry_date or "tidak ditemukan"
    prices_str = ", ".join(body.prices) if body.prices else "tidak ditemukan"

    input_payload = json.dumps(
        {
            "allergens": allergen_str,
            "negated_allergens": negated_str,
            "expiry_date": expiry_str,
            "prices": prices_str,
            "ocr_text": body.ocr_text,
        },
        ensure_ascii=False,
    )

    llm_summary: str | None = None

    try:
        llm_summary = await client.run_flow(
            flow_id=settings.langflow_flow_id_ingredient,
            input_value=input_payload,
        )
    except LangflowUnavailableError:
        logger.error("langflow_unavailable", extra={"endpoint": "ingredients_summarize"})
        # Do not propagate 503 — fall through to local fallback below.

    if llm_summary is not None:
        ok, reason = check_ingredient_summary(
            summary=llm_summary,
            allergens=body.allergens,
            negated_allergens=body.negated_allergens,
        )
        if ok:
            return IngredientSummarizeResponse(summary=llm_summary.strip())
        # Guard rejected — log reason code, fall through to local fallback.
        logger.info(
            "ingredient_guard_fallback",
            extra={"reason": reason},
        )

    # Local fallback: compose a deterministic summary from the input fields.
    fallback = build_fallback_summary(
        allergens=body.allergens,
        negated_allergens=body.negated_allergens,
        expiry_date=body.expiry_date,
        prices=body.prices,
    )
    return IngredientSummarizeResponse(summary=fallback)
