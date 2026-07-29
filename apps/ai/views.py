"""AI tools workspace — Mega Users only."""

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import client, context
from .models import AIConnection

# Conversation state lives in the session, not the database: these are working
# questions about data the user is already looking at, and persisting them would
# mean persisting POC content into a second place with its own retention story.
SESSION_KEY = "ai_chat_history"
MAX_HISTORY_TURNS = 20


def _require_mega(request):
    if not request.user.is_authenticated:
        raise PermissionDenied
    if not request.user.is_mega_user:
        raise PermissionDenied


def _connection(user):
    connection, _ = AIConnection.objects.get_or_create(user=user)
    return connection


def tools(request):
    """The chat workspace."""
    _require_mega(request)
    connection = _connection(request.user)
    _, scope = context.build_briefing(request.user)
    return render(
        request,
        "ai/tools.html",
        {
            "connection": connection,
            "model_choices": AIConnection.Model.choices,
            "scope": scope,
            "history": request.session.get(SESSION_KEY, []),
        },
    )


@require_POST
def connect(request):
    """Save (or replace) the user's API key and model choice."""
    _require_mega(request)
    connection = _connection(request.user)

    model_id = request.POST.get("model_id") or AIConnection.Model.OPUS
    if model_id not in dict(AIConnection.Model.choices):
        messages.error(request, "Unknown model.")
        return redirect("ai:tools")
    connection.model_id = model_id

    raw_key = (request.POST.get("api_key") or "").strip()
    if raw_key:
        error = client.verify_key(raw_key, model_id)
        if error:
            messages.error(request, error)
            return redirect("ai:tools")
        connection.set_api_key(raw_key)
        messages.success(request, "API key verified and connected.")
    else:
        # No key in the POST means "just change the model" — don't wipe the
        # stored key, and don't claim a connection the user didn't make.
        if connection.is_connected:
            messages.success(request, "Model updated.")
        else:
            messages.error(request, "Paste an API key to connect.")
            return redirect("ai:tools")

    connection.save()
    return redirect("ai:tools")


@require_POST
def disconnect(request):
    """Forget the stored key."""
    _require_mega(request)
    connection = _connection(request.user)
    connection.encrypted_api_key = ""
    connection.key_hint = ""
    connection.save()
    request.session.pop(SESSION_KEY, None)
    messages.success(request, "API key removed.")
    return redirect("ai:tools")


@require_POST
def chat(request):
    """One turn of conversation. Returns JSON for the page's fetch()."""
    _require_mega(request)
    connection = _connection(request.user)
    if not connection.is_connected:
        return JsonResponse({"error": "No API key connected."}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Malformed request."}, status=400)

    user_message = (payload.get("message") or "").strip()
    if not user_message:
        return JsonResponse({"error": "Empty message."}, status=400)

    briefing, scope = context.build_briefing(request.user)
    history = request.session.get(SESSION_KEY, [])

    reply, error = client.send_message(
        api_key=connection.api_key,
        model_id=connection.model_id,
        briefing=briefing,
        history=history,
        user_message=user_message,
    )
    if error:
        return JsonResponse({"error": error}, status=502)

    history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": reply},
    ]
    # Keep the tail — the briefing carries the facts, so older turns are the
    # cheapest thing to drop when the conversation runs long.
    request.session[SESSION_KEY] = history[-(MAX_HISTORY_TURNS * 2):]
    request.session.modified = True

    AIConnection.objects.filter(pk=connection.pk).update(last_used_at=timezone.now())
    # Rendered server-side through the app's own Markdown pipeline (escaping
    # mode on), so a reply formats like every other Markdown surface here and
    # the page never has to trust model output as HTML.
    from apps.core.templatetags.core_extras import markdownify

    return JsonResponse(
        {"reply": reply, "reply_html": markdownify(reply), "scope": scope}
    )


@require_POST
def reset(request):
    """Clear the conversation."""
    _require_mega(request)
    request.session.pop(SESSION_KEY, None)
    return JsonResponse({"ok": True})


@require_POST
def toggle_theme(request):
    """Switch the Mega User (red) interface on or off for this user."""
    _require_mega(request)
    user = request.user
    user.mega_theme_enabled = not user.mega_theme_enabled
    user.save(update_fields=["mega_theme_enabled"])
    return redirect(request.META.get("HTTP_REFERER") or reverse("ai:tools"))
