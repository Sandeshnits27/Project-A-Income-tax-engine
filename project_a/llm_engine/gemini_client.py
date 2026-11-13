from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class GeminiError(RuntimeError):
    pass


def generate(prompt: str) -> tuple[str, dict[str, Any], float, str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiError("GEMINI_API_KEY is not set; query-time Gemini calculation was not attempted.")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise GeminiError(f"Gemini returned HTTP {error.code}: {details}") from error
    except urllib.error.URLError as error:
        raise GeminiError(f"Gemini request failed: {error.reason}") from error
    elapsed_ms = (time.perf_counter() - started) * 1000
    try:
        text = response_body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as error:
        raise GeminiError(f"Gemini response did not contain generated text: {response_body}") from error
    usage = response_body.get("usageMetadata", {})
    return text, usage, elapsed_ms, model
