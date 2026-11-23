"""Local Ollama client for the ZzzRise sleep-wellbeing coach.

No third-party hosted AI API is used. By default, requests are sent only to
Ollama on this same computer (127.0.0.1). Remote Ollama hosts require an
explicit OLLAMA_ALLOW_REMOTE=true setting.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

SYSTEM_PROMPT = """You are ZzzRise, a kind and supportive sleep-wellbeing information assistant.
You are not a doctor. Never diagnose insomnia or any other condition, assess or
classify medical risk, prescribe medicines or supplements, or claim to treat,
cure, or prevent a medical condition. Do not say that a user has insomnia.
Use only the sleep summary, sleep records and optional journal excerpts provided.
Clearly state when there is too little information to identify a pattern.
Offer a few practical, low-risk general sleep-habit ideas where relevant, such as
keeping a reasonably consistent wake time, having a relaxing wind-down routine,
keeping the room dark, quiet and comfortable, reducing late-day caffeine, getting
daylight and daytime movement, and using the bed mainly for sleep. Avoid rigid
rules, blame, fear, and guarantees. Do not recommend medication or supplements.
If the user reports persistent sleep difficulties, significant daytime impact,
breathing pauses/gasping, dangerous sleepiness (especially while driving), or
severe distress, gently recommend speaking with a qualified healthcare
professional. If there is immediate danger, encourage urgent local help.
Keep the response warm, concise, practical and non-judgmental. Make answers easy
to scan: start with a one-sentence takeaway, then use short headings and 2–4
bullets when useful, and finish with one realistic next step. Avoid long paragraphs,
repeat the most important number plainly, and explain when the data sample is small.
This is general wellbeing information, not medical advice."""


class LocalAIError(RuntimeError):
    """Raised when the local Ollama service cannot answer."""


def _ollama_url() -> str:
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LocalAIError("OLLAMA_BASE_URL must be a valid http(s) Ollama address.")
    host = parsed.hostname.lower()
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    allow_remote = os.getenv("OLLAMA_ALLOW_REMOTE", "false").lower() == "true"
    if host not in local_hosts and not allow_remote:
        raise LocalAIError(
            "For privacy, ZzzRise only connects to Ollama on this computer by default. "
            "Set OLLAMA_ALLOW_REMOTE=true only if you intentionally trust the remote Ollama server."
        )
    return base


async def ask_local_model(
    question: str,
    sleep_summary: dict,
    sleep_records: list[dict],
    journal: list[dict],
) -> str:
    base = _ollama_url()
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip()
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Use this limited context from the signed-in user's own ZzzRise account. "
                    "Do not infer facts that are not present.\n"
                    f"Sleep summary: {sleep_summary}\n"
                    f"Recent completed sleep records: {sleep_records}\n"
                    f"Recent optional journal excerpts: {journal}\n"
                    f"User question: {question}"
                ),
            },
        ],
        "options": {"temperature": 0.35},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
            response = await client.post(f"{base}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.ConnectError as exc:
        raise LocalAIError(
            "Ollama is not running. Start Ollama and download the configured model, then try again."
        ) from exc
    except httpx.TimeoutException as exc:
        raise LocalAIError("The local AI took too long to respond. Try again in a moment.") from exc
    except httpx.HTTPError as exc:
        raise LocalAIError(
            "The local AI could not complete the request. Check that Ollama is running and the model is installed."
        ) from exc
    except (ValueError, TypeError) as exc:
        raise LocalAIError("The local AI returned an unreadable response. Please try again.") from exc

    answer = data.get("message", {}).get("content", "") if isinstance(data, dict) else ""
    if not isinstance(answer, str) or not answer.strip():
        raise LocalAIError("The local AI returned an empty answer. Please try again.")
    return answer.strip()
