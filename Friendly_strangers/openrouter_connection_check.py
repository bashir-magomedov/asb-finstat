"""Verify that OpenRouter is reachable without exposing the API key."""

from __future__ import annotations

import os
import sys

from openrouter import OpenRouter


def main() -> int:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print(
            "OPENROUTER_API_KEY is not available to this process. "
            "Start a new terminal after updating ~/.bash_profile, or source it first.",
            file=sys.stderr,
        )
        return 2

    try:
        with OpenRouter(api_key=api_key) as client:
            response = client.chat.send(
                model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": "Reply with exactly: connection verified"}],
                max_tokens=8,
                temperature=0,
            )
    except Exception as exc:
        print(f"OpenRouter connection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    message = response.choices[0].message.content if response.choices else None
    if not message:
        print("OpenRouter returned no message content.", file=sys.stderr)
        return 1

    print("OpenRouter connection verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
