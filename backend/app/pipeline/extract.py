from pathlib import Path


async def extract_statements(path: Path, emit) -> dict | None:
    """AI call #5: extract the financial statements from an English report.

    TODO(Sophie): replace this stub with the real extraction.
    - Model: settings.model_extract (call it via app.openrouter.chat / chat_json)
    - Input: path to an English-language annual report / financial statement
    - Output: structured statements, e.g. {"income_statement": ..., "balance_sheet": ...,
      "cash_flow": ...} - this ends up in the final "pipeline done" event on the frontend.
    - Report progress with: await emit("extract", "running"|"done"|"error", "message")
    """
    await emit("extract", "skipped", f"{path.name} - extraction stub, TODO(Sophie)")
    return None
