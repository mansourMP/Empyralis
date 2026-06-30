import json
import os
import ssl
from urllib import error as urlerror
from urllib import request as urlrequest

import certifi
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_RESPONSES_URL = os.getenv("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses")
ORION_MODEL = os.getenv("ORION_MODEL", "gpt-4.1")


def call_openai_responses(system_prompt: str, user_input: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": ORION_MODEL,
        "instructions": system_prompt,
        "input": user_input,
    }

    req = urlrequest.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    context = ssl.create_default_context(cafile=certifi.where())

    try:
        with urlrequest.urlopen(req, timeout=60, context=context) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"OpenAI responses error ({exc.code}): {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"OpenAI responses call failed: {exc}") from exc

    if isinstance(body.get("output_text"), str) and body["output_text"].strip():
        return body["output_text"].strip()

    output = body.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
        if parts:
            return "\n".join(parts)

    return json.dumps(body)


if __name__ == "__main__":
    default_goal = "Create a concise SMB customer-ops plan: inbox triage, lead follow-up, and booking."
    goal = os.getenv("ORION_GOAL", "").strip() or default_goal
    prompt = (
        "You are Empyralis Operator. Return:\n"
        "1) Execution plan\n"
        "2) Immediate actions\n"
        "3) Risks\n"
    )
    print("Starting Empyralis standalone runner...")
    print(call_openai_responses(prompt, goal))
