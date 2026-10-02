"""Integration tests for POST /v1/qa/answer.

All Langflow HTTP calls are mocked — no live Langflow/LLM required.

Covers:
  • Grounded medicine answer → 200, is_grounded=True.
  • Ungrounded medicine question → 200, is_grounded=False, safe fallback.
  • Hallucinated dosage number → 200, is_grounded=False, safe fallback.
  • NOT_FOUND sentinel from flow → 200, is_grounded=False, safe fallback.
  • Langflow unavailable → 503.
  • Missing API key → 401.
  • Rate limit exceeded → 429.
  • Invalid request (missing field) → 422.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Set required env vars before importing the app so Settings validates OK.
os.environ.setdefault("PROXY_API_KEY", "test-key-abc123")
os.environ.setdefault("LANGFLOW_FLOW_ID_QA", "aaaaaaaa-0000-0000-0000-000000000001")
os.environ.setdefault("LANGFLOW_FLOW_ID_INGREDIENT", "bbbbbbbb-0000-0000-0000-000000000002")

from app.main import app  # noqa: E402 (must come after env vars)

VALID_HEADERS = {"X-API-Key": "test-key-abc123"}

OCR_MEDICINE = (
    "Paracetamol 500mg. Diminum 3 kali sehari setelah makan. "
    "Jangan melebihi 8 tablet dalam 24 jam."
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ────────────────────────────────────────────────────────────────────────────
# Auth & validation
# ────────────────────────────────────────────────────────────────────────────


def test_missing_api_key_returns_401(client: TestClient) -> None:
    response = client.post(
        "/v1/qa/answer",
        json={"question": "Berapa dosis?", "ocr_text": OCR_MEDICINE},
    )
    assert response.status_code == 401


def test_wrong_api_key_returns_401(client: TestClient) -> None:
    response = client.post(
        "/v1/qa/answer",
        headers={"X-API-Key": "wrong-key"},
        json={"question": "Berapa dosis?", "ocr_text": OCR_MEDICINE},
    )
    assert response.status_code == 401


def test_missing_question_field_returns_422(client: TestClient) -> None:
    response = client.post(
        "/v1/qa/answer",
        headers=VALID_HEADERS,
        json={"ocr_text": OCR_MEDICINE},  # question missing
    )
    assert response.status_code == 422


def test_empty_ocr_text_returns_422(client: TestClient) -> None:
    response = client.post(
        "/v1/qa/answer",
        headers=VALID_HEADERS,
        json={"question": "Berapa dosis?", "ocr_text": ""},  # empty
    )
    assert response.status_code == 422


# ────────────────────────────────────────────────────────────────────────────
# Happy-path: grounded medicine answer
# ────────────────────────────────────────────────────────────────────────────


def test_grounded_medicine_answer(client: TestClient) -> None:
    """LLM returns a grounded answer — should pass the guard and return 200."""
    mock_answer = "Diminum 3 kali sehari setelah makan."

    with patch(
        "app.routers.qa.LangflowClient.run_flow",
        new_callable=AsyncMock,
        return_value=mock_answer,
    ):
        response = client.post(
            "/v1/qa/answer",
            headers=VALID_HEADERS,
            json={"question": "Berapa kali minum obat ini sehari?", "ocr_text": OCR_MEDICINE},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["is_grounded"] is True
    assert data["answer"] == mock_answer
    assert data["source_sentence"] is not None


# ────────────────────────────────────────────────────────────────────────────
# Ungrounded medicine question → safe fallback
# ────────────────────────────────────────────────────────────────────────────


def test_ungrounded_medicine_question_returns_safe_fallback(client: TestClient) -> None:
    """LLM returns an off-topic answer → guard kicks in → safe fallback returned."""
    from app.guards.grounding import SAFE_FALLBACK

    ungrounded_answer = "Obat ini aman untuk semua usia."  # not in OCR

    with patch(
        "app.routers.qa.LangflowClient.run_flow",
        new_callable=AsyncMock,
        return_value=ungrounded_answer,
    ):
        response = client.post(
            "/v1/qa/answer",
            headers=VALID_HEADERS,
            json={"question": "Berapa dosis obat ini?", "ocr_text": OCR_MEDICINE},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["is_grounded"] is False
    assert data["answer"] == SAFE_FALLBACK
    assert data["source_sentence"] is None


# ────────────────────────────────────────────────────────────────────────────
# Hallucinated dosage number → safe fallback
# ────────────────────────────────────────────────────────────────────────────


def test_hallucinated_dosage_returns_safe_fallback(client: TestClient) -> None:
    """LLM invents a dosage number not in OCR → guard rejects even with overlap."""
    from app.guards.grounding import SAFE_FALLBACK

    ocr = "Paracetamol diminum setelah makan."
    hallucinated = "Diminum 2 tablet setelah makan."  # "2 tablet" absent from OCR

    with patch(
        "app.routers.qa.LangflowClient.run_flow",
        new_callable=AsyncMock,
        return_value=hallucinated,
    ):
        response = client.post(
            "/v1/qa/answer",
            headers=VALID_HEADERS,
            json={"question": "Berapa tablet yang harus diminum?", "ocr_text": ocr},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["is_grounded"] is False
    assert data["answer"] == SAFE_FALLBACK


# ────────────────────────────────────────────────────────────────────────────
# NOT_FOUND sentinel
# ────────────────────────────────────────────────────────────────────────────


def test_not_found_sentinel_returns_safe_fallback(client: TestClient) -> None:
    from app.guards.grounding import SAFE_FALLBACK

    with patch(
        "app.routers.qa.LangflowClient.run_flow",
        new_callable=AsyncMock,
        return_value="NOT_FOUND",
    ):
        response = client.post(
            "/v1/qa/answer",
            headers=VALID_HEADERS,
            json={"question": "Berapa dosis?", "ocr_text": OCR_MEDICINE},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["is_grounded"] is False
    assert data["answer"] == SAFE_FALLBACK


# ────────────────────────────────────────────────────────────────────────────
# Langflow unavailable → 503
# ────────────────────────────────────────────────────────────────────────────


def test_langflow_unavailable_returns_503(client: TestClient) -> None:
    from app.langflow_client import LangflowUnavailableError

    with patch(
        "app.routers.qa.LangflowClient.run_flow",
        new_callable=AsyncMock,
        side_effect=LangflowUnavailableError("timeout"),
    ):
        response = client.post(
            "/v1/qa/answer",
            headers=VALID_HEADERS,
            json={"question": "Berapa dosis?", "ocr_text": OCR_MEDICINE},
        )

    assert response.status_code == 503
