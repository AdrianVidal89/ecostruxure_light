"""Thin wrapper over the Anthropic Messages API.

Everything provider-specific lives here so the views stay about HTTP and
permissions. Two decisions worth knowing:

* The POC briefing goes in ``system`` with ``cache_control``, not in the user
  turn. Caching is a prefix match, so the briefing must sit ahead of the
  conversation and stay byte-identical across turns — that turns a large,
  otherwise-repeated payload into ~0.1x cache reads from the second turn on.
* Failures are returned, not raised. A wrong API key or a rate limit is a
  normal thing for a user to hit while wiring this up, and it should render as
  a message in the chat rather than a 500 page.
"""

import logging

import anthropic

logger = logging.getLogger(__name__)

MAX_TOKENS = 8000

SYSTEM_PROMPT = """You are the AI assistant built into OSPI Light, a Proof of \
Concept management tool used by Schneider Electric engineering teams.

You are given a briefing containing every POC the person talking to you can \
access — its use cases, requirements, phases and tests. Answer from that \
briefing. It is the current state of their data, not background reading.

How to work:
- Ground every claim in the briefing. Cite the identifiers you are talking \
about (POC name, UC code, requirement id, test code) so the person can find \
them in the tool.
- If the briefing does not contain something, say so plainly rather than \
inferring it. "No requirement in POC X mentions redundancy" is a useful answer; \
a plausible invention is not.
- Cross-checking is the main job: gaps between use cases and requirements, \
requirements no test covers, contradictions between a requirement and its \
validation criteria, tests whose expected result does not match the \
requirement they verify.
- When asked to draft content — a requirement, a use case, a test definition, \
a summary — match the wording, structure and level of detail of the existing \
items in that POC, and hand back something ready to paste into the form.
- Be concise. Lead with the answer; supporting detail after."""


def _client(api_key):
    return anthropic.Anthropic(api_key=api_key)


def send_message(api_key, model_id, briefing, history, user_message):
    """Ask Claude a question against the POC briefing.

    ``history`` is a list of ``{"role": ..., "content": ...}`` dicts from
    earlier turns. Returns ``(reply_text, error_text)`` — exactly one is set.
    """
    system = [{"type": "text", "text": SYSTEM_PROMPT}]
    if briefing:
        # The breakpoint goes on the last system block, so tools + system + the
        # briefing are cached together and every later turn reads them back.
        system.append(
            {
                "type": "text",
                "text": briefing,
                "cache_control": {"type": "ephemeral"},
            }
        )

    messages = list(history) + [{"role": "user", "content": user_message}]

    try:
        response = _client(api_key).messages.create(
            model=model_id,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
        )
    except anthropic.AuthenticationError:
        return None, "That API key was rejected. Check it and reconnect."
    except anthropic.PermissionDeniedError:
        return None, "This API key doesn't have access to the selected model."
    except anthropic.NotFoundError:
        return None, "The selected model isn't available on this API key."
    except anthropic.RateLimitError:
        return None, "Rate limited by the API. Wait a moment and try again."
    except anthropic.APIConnectionError:
        return None, "Couldn't reach the API — check the server's network access."
    except anthropic.APIStatusError as exc:
        logger.warning("AI request failed: %s %s", exc.status_code, exc.message)
        return None, f"The API returned an error ({exc.status_code})."

    # A refusal is a successful HTTP response with empty/partial content, so
    # check it before indexing into the blocks.
    if response.stop_reason == "refusal":
        return None, "The model declined to answer this request."

    text = "".join(b.text for b in response.content if b.type == "text")
    return (text or "(empty response)"), None


def verify_key(api_key, model_id):
    """Cheapest possible round trip to prove a key works. Returns an error or None."""
    try:
        _client(api_key).messages.create(
            model=model_id,
            max_tokens=4,
            messages=[{"role": "user", "content": "Reply with OK."}],
        )
    except anthropic.AuthenticationError:
        return "That API key was rejected."
    except anthropic.PermissionDeniedError:
        return "This API key doesn't have access to the selected model."
    except anthropic.NotFoundError:
        return "The selected model isn't available on this API key."
    except anthropic.APIConnectionError:
        return "Couldn't reach the API — check the server's network access."
    except anthropic.APIStatusError as exc:
        return f"The API returned an error ({exc.status_code})."
    return None
