"""Application settings loaded from environment variables / .env file.

All secrets (API keys, project IDs) live here and are never forwarded to
clients or written to structured logs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pydantic settings — validated at startup from env / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Rename protected namespace so 'model_provider' field is not flagged.
        protected_namespaces=("settings_",),
    )

    # ------------------------------------------------------------------ #
    # Proxy auth
    # ------------------------------------------------------------------ #
    proxy_api_key: str = Field(..., description="Shared secret sent by the Flutter app as X-API-Key")

    # ------------------------------------------------------------------ #
    # Langflow
    # ------------------------------------------------------------------ #
    langflow_base_url: str = Field("http://langflow:7860", description="Base URL of the self-hosted Langflow service")
    langflow_flow_id_qa: str = Field(..., description="UUID of the visual_qa flow in Langflow")
    langflow_flow_id_ingredient: str = Field(..., description="UUID of the ingredient_summary flow in Langflow")
    langflow_timeout: float = Field(15.0, description="HTTP timeout in seconds for Langflow calls")

    # ------------------------------------------------------------------ #
    # LLM provider
    # ------------------------------------------------------------------ #
    model_provider: Literal["watsonx", "openai", "ollama", "google"] = Field(
        "google",
        description="Which LLM backend to use. Injected as a Langflow tweak.",
    )

    # watsonx.ai
    watsonx_api_key: str = Field("", description="watsonx.ai API key (required when model_provider=watsonx)")
    watsonx_project_id: str = Field("", description="watsonx.ai project ID")
    watsonx_base_url: str = Field("https://us-south.ml.cloud.ibm.com", description="watsonx.ai endpoint")
    watsonx_model_id: str = Field("ibm/granite-13b-chat-v2", description="watsonx.ai model ID")

    # OpenAI
    openai_api_key: str = Field("", description="OpenAI API key (required when model_provider=openai)")
    openai_model_id: str = Field("gpt-4o-mini", description="OpenAI model name")

    # Ollama
    ollama_base_url: str = Field("http://host.docker.internal:11434", description="Ollama base URL")
    ollama_model_id: str = Field("llama3", description="Ollama model name")

    # Google Generative AI
    # API key is stored as a Langflow Global Variable (GOOGLE_API_KEY) and
    # read directly by the LLM node — it is NOT forwarded from this backend.
    google_model_id: str = Field("gemini-2.0-flash", description="Google Generative AI model name")

    # ------------------------------------------------------------------ #
    # Safety / grounding
    # ------------------------------------------------------------------ #
    grounding_min_overlap: float = Field(
        0.3,
        ge=0.0,
        le=1.0,
        description="Jaccard overlap threshold below which a Q&A answer is rejected",
    )

    # ------------------------------------------------------------------ #
    # Rate limiting
    # ------------------------------------------------------------------ #
    rate_limit_per_minute: int = Field(60, gt=0, description="Max requests per API key per minute")

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field("INFO")


# Module-level singleton — imported by the rest of the application.
settings = Settings()  # type: ignore[call-arg]
