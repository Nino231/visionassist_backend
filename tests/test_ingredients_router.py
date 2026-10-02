"""Integration tests for POST /v1/ingredients/summarize.

All Langflow HTTP calls are mocked — no live Langflow/LLM required.

Covers:
  • Valid rephrased summary → 200.
  • LLM summary that leaks a new allergen → guard rejects, fallback returned.
  • LLM summary exceeds 15 words → guard rejects, fallback returned.
  • Langflow unavailable → 200 with local fallback (not 503).
  • Missing API key → 401.
  • Invalid request (empty ocr_text) → 422.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("PROXY_API_KEY", "test-key-abc123")
os.environ.setdefault("LANGFLOW_FLOW_ID_QA", "aaaaaaaa-0000-0000-0000-000000000001")
os.environ.setdefault("LANGFLOW_FLOW_ID_INGREDIENT", "bbbbbbbb-0000-0000-0000-000000000002")

from app.main import app  # noqa: E402

VALID_HEADERS = {"X-API-Key": "test-key-abc123"}

BASE_REQUEST = {
    "ocr_text": "Mengandung kacang. Bebas gluten. Kedaluwarsa 12/2026. Rp 15.000.",
    "allergens": ["Kacang"],
    "negated_allergens": ["Gluten"],
    "expiry_date": "12/2026",
    "prices": ["Rp 15.000"],
}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ────────────────────────────────────────────────────────────────────────────
# Auth & validation
# ────────────────────────────────────────────────────────────────────────────


def test_missing_api_key_returns_401(client: TestClient) -> None:
    response = client.post("/v1/ingredients/summarize", json=BASE_REQUEST)
    assert response.status_code == 401


def test_empty_ocr_text_returns_422(client: TestClient) -> None:
    body = {**BASE_REQUEST, "ocr_text": ""}
    response = client.post("/v1/ingredients/summarize", headers=VALID_HEADERS, json=body)
    assert response.status_code == 422


# ────────────────────────────────────────────────────────────────────────────
# Happy path — valid rephrased summary
# ────────────────────────────────────────────────────────────────────────────


def test_valid_summary_returned(client: TestClient) -> None:
    valid_summary = "Mengandung kacang, bebas gluten, kedaluwarsa Des 2026."

    with patch(
        "app.routers.ingredients.LangflowClient.run_flow",
        new_callable=AsyncMock,
        return_value=valid_summary,
    ):
        response = client.post(
            "/v1/ingredients/summarize",
            headers=VALID_HEADERS,
            json=BASE_REQUEST,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == valid_summary


# ────────────────────────────────────────────────────────────────────────────
# Allergen leak → fallback (not 422)
# ────────────────────────────────────────────────────────────────────────────


def test_allergen_leak_falls_back_to_local_summary(client: TestClient) -> None:
    """LLM injects 'susu' (Susu allergen) not present in input — guard rejects,
    falls back to a locally-composed summary instead of erroring."""
    leaked_summary = "Mengandung kacang dan susu, bebas gluten."

    with patch(
        "app.routers.ingredients.LangflowClient.run_flow",
        new_callable=AsyncMock,
        return_value=leaked_summary,
    ):
        response = client.post(
            "/v1/ingredients/summarize",
            headers=VALID_HEADERS,
            json=BASE_REQUEST,
        )

    assert response.status_code == 200
    data = response.json()
    # Must not contain the leaked allergen
    assert "susu" not in data["summary"].lower()
    # Fallback summary should contain 'kacang' (it is in allergens list)
    assert "kacang" in data["summary"].lower()


# ────────────────────────────────────────────────────────────────────────────
# Summary too long → fallback
# ────────────────────────────────────────────────────────────────────────────


def test_too_long_summary_falls_back(client: TestClient) -> None:
    too_long = (
        "Produk ini mengandung kacang tanah dan baik untuk kesehatan "
        "jika dikonsumsi dalam jumlah yang tepat setiap hari."
    )
    assert len(too_long.split()) > 15

    with patch(
        "app.routers.ingredients.LangflowClient.run_flow",
        new_callable=AsyncMock,
        return_value=too_long,
    ):
        response = client.post(
            "/v1/ingredients/summarize",
            headers=VALID_HEADERS,
            json=BASE_REQUEST,
        )

    assert response.status_code == 200
    data = response.json()
    # Fallback must be ≤ 15 words
    assert len(data["summary"].split()) <= 15


# ────────────────────────────────────────────────────────────────────────────
# Langflow unavailable → 200 with local fallback
# ────────────────────────────────────────────────────────────────────────────


def test_langflow_unavailable_falls_back_gracefully(client: TestClient) -> None:
    """Unlike the Q&A endpoint, ingredient summarize never returns 503 —
    it gracefully falls back to a locally-composed summary."""
    from app.langflow_client import LangflowUnavailableError

    with patch(
        "app.routers.ingredients.LangflowClient.run_flow",
        new_callable=AsyncMock,
        side_effect=LangflowUnavailableError("timeout"),
    ):
        response = client.post(
            "/v1/ingredients/summarize",
            headers=VALID_HEADERS,
            json=BASE_REQUEST,
        )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["summary"], str)
    assert len(data["summary"]) > 0
    # Fallback must be ≤ 15 words
    assert len(data["summary"].split()) <= 15
