# Langflow Flow Definitions

This directory contains the exported Langflow flow JSON files for the
VisionAssist Backend.  Each file is version-controlled so that flow changes
go through the same PR review workflow as code changes.

---

## `visual_qa.json` — Visual Q&A Flow

### Node graph (prose)

```
[PromptTemplate] ──► [Configurable LLM] ──► [ChatOutput]
```

**PromptTemplate** (`prompt_node`)  
Accepts two runtime variables:

- `ocr_text` — the text extracted from the product image by the Flutter app's
  on-device ML Kit OCR.
- `question` — the user's spoken question.

Both are injected as Langflow **tweaks** by the FastAPI proxy at call time;
they are never stored or hardcoded in the flow.

The prompt enforces a strict grounding contract:

> *"Answer ONLY using information explicitly stated in the Context. For
> medicine dosage/timing/frequency questions: if the exact answer is not
> present word-for-word in the Context, reply with exactly: NOT_FOUND."*

**Configurable LLM** (`llm_node`)  
Accepts all model-provider credentials as tweaks:

| Tweak | Used when |
|---|---|
| `MODEL_PROVIDER` | always (`watsonx` \| `openai` \| `ollama`) |
| `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_BASE_URL`, `WATSONX_MODEL_ID` | `model_provider=watsonx` |
| `OPENAI_API_KEY`, `OPENAI_MODEL_ID` | `model_provider=openai` |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL_ID` | `model_provider=ollama` |

Temperature is set to `0.0` for deterministic, grounded responses.

**ChatOutput** (`output_parser`)  
Passes the raw LLM text to the Langflow run-response envelope.  The FastAPI
proxy extracts the text and runs it through the grounding guard before
returning a response to the Flutter app.

### Safety contract

The `NOT_FOUND` sentinel in the prompt is the first defence.  The second
(deterministic) defence is the FastAPI grounding guard in
`app/guards/grounding.py`, which independently verifies:

1. Jaccard token overlap ≥ `GROUNDING_MIN_OVERLAP` (default 0.3).
2. No hallucinated dosage numbers absent from the original OCR text.
3. Literal `NOT_FOUND` → safe fallback phrase.

---

## `ingredient_summary.json` — Ingredient Summary Flow

### Node graph (prose)

```
[PromptTemplate] ──► [Configurable LLM] ──► [ChatOutput]
```

**PromptTemplate** (`prompt_node`)  
Accepts five runtime variables injected as tweaks:

- `ocr_text` — raw OCR text (for context).
- `allergens` — comma-separated list of detected allergens (not negated).
- `negated_allergens` — comma-separated list of negated allergens.
- `expiry_date` — parsed expiry date string (or "tidak ditemukan").
- `prices` — comma-separated list of detected prices.

The prompt explicitly instructs the LLM to *rephrase only*:

> *"Write a single, natural Indonesian sentence (≤ 15 words). Use ONLY the
> information given above — do NOT invent new allergens, claims, or details."*

**Configurable LLM** (`llm_node`)  
Same provider-agnostic tweak scheme as the Q&A flow.  Temperature is `0.1` to
allow natural phrasing while remaining faithful to the input.

**ChatOutput** (`output_parser`)  
Returns the rephrased summary text.  The FastAPI proxy runs the ingredient
guard (`app/guards/ingredient_guard.py`) before returning, which rejects:

1. Summaries exceeding 15 words.
2. Summaries containing allergen keywords not present in the input
   `allergens` / `negated_allergens` lists (allergen-leak guard).

If the guard rejects the LLM output, the proxy falls back to a deterministic
summary composed locally from the input fields — the Flutter app's UX is
never interrupted.

---

## Flow import / export workflow

### One-time import (deploy)

The `docker-compose.yml` mounts this directory into the Langflow container
at the path Langflow auto-loads from on startup, so the flows are imported
automatically — no manual UI step is needed for a fresh deployment.

### Edit → export → commit workflow

1. (Optional) Temporarily expose the Langflow UI by uncommenting the
   `ports` line in `docker-compose.yml`:
   ```yaml
   langflow:
     ports:
       - "7860:7860"   # ← uncomment for local editing
   ```
2. Open `http://localhost:7860` in a browser.
3. Edit the flow (e.g. adjust the prompt template text).
4. Export: **⋮ → Export → Download JSON**.
5. Overwrite the relevant file in this directory (`flows/visual_qa.json` or
   `flows/ingredient_summary.json`).
6. Commit the updated JSON in the same PR as any FastAPI changes that depend
   on the updated prompt variables.
7. Re-comment the `ports` line and redeploy.

> **Important**: after editing a flow in the UI, always export and commit the
> JSON.  The flow stored in Langflow's internal SQLite database is ephemeral
> (it is reset when the container is recreated); the JSON file in this
> directory is the canonical source of truth.
