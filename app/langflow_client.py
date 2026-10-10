"""Thin async HTTP client that wraps the Langflow ``/api/v1/run/{flow_id}`` API.

Responsibilities
────────────────
1. Build the provider-agnostic ``tweaks`` payload from ``Settings``.
   Credentials never leave this module — they are placed in ``tweaks`` and
   sent only to the Langflow service on the internal Docker network.
2. Execute the Langflow run request with a configurable timeout + one retry.
3. Map HTTP / connection errors to ``LangflowUnavailableError`` so the router
   can return ``503`` without knowing transport details.
4. Extract the raw text output from Langflow's response envelope.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Custom exceptions
# ────────────────────────────────────────────────────────────────────────────


class LangflowUnavailableError(Exception):
    """Raised when Langflow cannot be reached or times out."""


# ────────────────────────────────────────────────────────────────────────────
# Tweaks builder — provider-agnostic
# ────────────────────────────────────────────────────────────────────────────


def _build_provider_tweaks(settings: Settings) -> dict[str, Any]:
    """Return Langflow global-variable tweaks for the configured LLM provider.

    The flow's LLM node reads these tweaks at runtime so no flow-graph edits
    are needed to switch providers.  Only the credentials for the selected
    provider are included — unused keys are omitted to keep the payload lean.

    For ``google``: the API key is stored as a Langflow Global Variable
    (``GOOGLE_API_KEY``) and read directly by the Langflow LLM node.
    No credential is forwarded from this backend — only the model ID is sent.
    """
    base: dict[str, Any] = {"MODEL_PROVIDER": settings.model_provider}

    if settings.model_provider == "watsonx":
        base.update(
            {
                "WATSONX_API_KEY": settings.watsonx_api_key,
                "WATSONX_PROJECT_ID": settings.watsonx_project_id,
                "WATSONX_BASE_URL": settings.watsonx_base_url,
                "WATSONX_MODEL_ID": settings.watsonx_model_id,
            }
        )
    elif settings.model_provider == "openai":
        base.update(
            {
                "OPENAI_API_KEY": settings.openai_api_key,
                "OPENAI_MODEL_ID": settings.openai_model_id,
            }
        )
    elif settings.model_provider == "ollama":
        base.update(
            {
                "OLLAMA_BASE_URL": settings.ollama_base_url,
                "OLLAMA_MODEL_ID": settings.ollama_model_id,
            }
        )
    elif settings.model_provider == "google":
        # GOOGLE_API_KEY is a Langflow Global Variable set in the Langflow UI.
        # The backend only informs the flow which model to use.
        base.update({"GOOGLE_MODEL_ID": settings.google_model_id})

    return base


# ────────────────────────────────────────────────────────────────────────────
# Response parser
# ────────────────────────────────────────────────────────────────────────────


def _extract_text_output(data: dict[str, Any]) -> str:
    """Extract the first plain-text output from a Langflow run response.

    Langflow's run response shape (v1):
    {
      "outputs": [
        {
          "outputs": [
            {
              "results": {
                "message": { "text": "..." }
              }
            }
          ]
        }
      ]
    }

    Falls back through several known shapes so the client is resilient to
    minor Langflow version differences.
    """
    try:
        # Primary path: outputs[0].outputs[0].results.message.text
        return str(data["outputs"][0]["outputs"][0]["results"]["message"]["text"])
    except (KeyError, IndexError, TypeError):
        pass

    try:
        # Alternate: outputs[0].outputs[0].artifacts.message
        return str(data["outputs"][0]["outputs"][0]["artifacts"]["message"])
    except (KeyError, IndexError, TypeError):
        pass

    try:
        # Fallback: outputs[0].outputs[0].outputs.message.message
        return str(
            data["outputs"][0]["outputs"][0]["outputs"]["message"]["message"]
        )
    except (KeyError, IndexError, TypeError):
        pass

    raise ValueError(f"Cannot extract text output from Langflow response: {list(data.keys())}")


# ────────────────────────────────────────────────────────────────────────────
# Public client
# ────────────────────────────────────────────────────────────────────────────


class LangflowClient:
    """Async HTTP client for the Langflow run API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.langflow_base_url.rstrip("/")
        self._timeout = settings.langflow_timeout
        self._provider_tweaks = _build_provider_tweaks(settings)

    async def run_flow(
        self,
        flow_id: str,
        input_value: str,
        extra_tweaks: dict[str, Any] | None = None,
    ) -> str:
        """Call ``POST /api/v1/run/{flow_id}`` and return the text output.

        Parameters
        ----------
        flow_id:
            Langflow flow UUID (from ``LANGFLOW_FLOW_ID_QA`` or
            ``LANGFLOW_FLOW_ID_INGREDIENT``).
        input_value:
            Primary text input passed as ``input_value`` in the request body.
            For Q&A this is the question; for ingredients it is the OCR text.
        extra_tweaks:
            Per-request tweaks (e.g. prompt variables like ``ocr_text``,
            ``question``).  Merged on top of the provider tweaks.

        Returns
        -------
        str
            The raw text output from Langflow.

        Raises
        ------
        LangflowUnavailableError
            On timeout, connection failure, or non-2xx response.
        """
        url = f"{self._base_url}/api/v1/run/{flow_id}"

        tweaks: dict[str, Any] = {**self._provider_tweaks, **(extra_tweaks or {})}

        payload: dict[str, Any] = {
            "input_value": input_value,
            "input_type": "chat",
            "output_type": "chat",
            "tweaks": tweaks,
        }

        last_exc: Exception | None = None
        for attempt in range(2):  # one retry
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload)

                if response.status_code == 200:
                    return _extract_text_output(response.json())

                logger.warning(
                    "langflow_non_200",
                    extra={
                        "attempt": attempt + 1,
                        "status_code": response.status_code,
                        "flow_id": flow_id,
                    },
                )
                last_exc = ValueError(f"Langflow returned HTTP {response.status_code}")

            except httpx.TimeoutException as exc:
                logger.warning(
                    "langflow_timeout",
                    extra={"attempt": attempt + 1, "flow_id": flow_id},
                )
                last_exc = exc
            except httpx.RequestError as exc:
                logger.warning(
                    "langflow_connection_error",
                    extra={"attempt": attempt + 1, "flow_id": flow_id},
                )
                last_exc = exc

        raise LangflowUnavailableError(
            f"Langflow unreachable after 2 attempts for flow {flow_id}"
        ) from last_exc

    async def health_check(self) -> bool:
        """Return ``True`` if Langflow's health endpoint responds 2xx."""
        url = f"{self._base_url}/health"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
            return response.status_code < 300
        except (httpx.TimeoutException, httpx.RequestError):
            return False
