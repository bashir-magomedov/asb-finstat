from pathlib import Path


async def translate_pdf(path: Path, language: str, emit) -> Path:
    """AI call #4: translate a non-English statement to English.

    TODO(Adel): replace this stub with the real translation pipeline.
    - Model: settings.model_translate (call it via app.openrouter.chat / chat_json)
    - Input: path to the downloaded PDF + detected source language
    - Output: path to the translated document (PDF or text) - extraction (#5)
      receives whatever path you return.
    - Report progress with: await emit("translate", "running"|"done"|"error", "message")
    """
    await emit("translate", "skipped", f"{path.name} ({language}) - translation stub, TODO(Adel)")
    return path
