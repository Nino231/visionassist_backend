"""Pydantic request / response schemas for the Visual Q&A endpoint.

Flutter ↔ Backend field mapping
───────────────────────────────
Flutter QaAnswer          │ Python QaAnswerResponse
──────────────────────────┼──────────────────────────────
answer        (String)    │ answer        (str)
isGrounded    (bool)      │ is_grounded   (bool)
sourceSentence (String?)  │ source_sentence (str | None)
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QaAnswerRequest(BaseModel):
    """Incoming Visual Q&A request.

    Mirrors the data the Flutter ``LocalQaRepository.answer()`` call would need
    so that the remote path is a drop-in replacement.
    """

    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="User's question about the scanned image",
    )
    ocr_text: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="OCR text extracted from the image by the Flutter app",
    )


class QaAnswerResponse(BaseModel):
    """Response mirrors Flutter's ``QaAnswer`` domain entity field-for-field.

    Mapping:
      answer          → QaAnswer.answer
      is_grounded     → QaAnswer.isGrounded
      source_sentence → QaAnswer.sourceSentence  (null when not grounded)
    """

    answer: str
    is_grounded: bool
    source_sentence: str | None = None
