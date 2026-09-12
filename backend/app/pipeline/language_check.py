from pathlib import Path


async def check_language(path: Path, emit) -> dict:
    """AI call #3: detect the language of a downloaded statement PDF.

    TODO(Adel): replace this stub with the real language detection.
    - Model: settings.model_language_check (call it via app.openrouter.chat / chat_json)
    - Input: path to the downloaded PDF (pypdf + cryptography are already installed
      for reading text out of PDFs, incl. AES-encrypted ones like Siemens')
    - Output: {"language": "<English name>", "is_english": bool} - the runner sends
      non-English files to translate (#4), English ones straight to extract (#5).
    - Report progress with: await emit("language_check", "running"|"done"|"error", "message")
    """
    await emit("language_check", "skipped", f"{path.name} - language check stub, TODO(Adel)")
    return {"language": "unknown", "is_english": False}
