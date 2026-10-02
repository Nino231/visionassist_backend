"""POST /v1/qa/answer — Visual Q&A proxy endpoint.

Flow
────
1. Validate/sanitize request (handled by Pydantic model).
2. Call Langflow visual_qa flow with question + ocr_text as tweaks.
3. Run the grounding guard on the raw LLM output.
4. Return the shaped QaAnswerResponse (mirrors Flutter QaAnswer entity).

Safety contract is enforced by the grounding guard, not just prompting.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import settings
from ..deps import rate_limit
from ..guards.grounding import check_grounding
from ..langflow_client import LangflowClient, LangflowUnavailableError
from ..schemas.qa import QaAnswerRequest, QaAnswerResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/qa/answer",
    response_model=QaAnswerResponse,
    summary="Visual Q&A — answer a question grounded in OCR text",
    tags=["Q&A"],
)
async def qa_answer(
    body: QaAnswerRequest,
    _: Annotated[None, Depends(rate_limit)],
) -> QaAnswerResponse:
    """Answer a question about text read from an image.

    The answer is always grounded in ``ocr_text``.  If the LLM cannot
    produce a grounded answer (or the question involves medicine dosage with no
    supporting text), the safe fallback phrase is returned.

    Structured logs record method, path, status, and guard-rejection reason
    codes only — ``question`` and ``ocr_text`` are never logged.
    """
    client = LangflowClient(settings)

    # Build per-request tweaks: prompt variables injected into the Langflow
    # flow's prompt template node.
    extra_tweaks: dict[str, str] = {
        "ocr_text": body.ocr_text,
        "question": body.question,
    }

    try:
        raw_answer = await client.run_flow(
            flow_id=settings.langflow_flow_id_qa,
            input_value=body.question,
            extra_tweaks=extra_tweaks,
        )
    except LangflowUnavailableError as exc:
        logger.error("langflow_unavailable", extra={"endpoint": "qa_answer"})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The AI service is temporarily unavailable. Please try again later.",
        ) from exc

    # Deterministic grounding guard — runs after every Langflow call.
    result = check_grounding(
        llm_answer=raw_answer,
        question=body.question,
        ocr_text=body.ocr_text,
        min_overlap=settings.grounding_min_overlap,
    )

    return QaAnswerResponse(
        answer=result.answer,
        is_grounded=result.is_grounded,
        source_sentence=result.source_sentence,
    )
