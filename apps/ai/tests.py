"""Tests for the Mega User AI tools workspace."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.ai import crypto
from apps.ai.context import build_briefing
from apps.ai.models import AIConnection
from apps.pocs.models import POC, POCMembership, Requirement, UseCase

User = get_user_model()


class MegaUserAccessTests(TestCase):
    """Mega User is a capability gate, not a permission level."""

    def setUp(self):
        self.plain = User.objects.create_user("plain", password="x", is_active=True)
        self.mega = User.objects.create_user(
            "mega", password="x", is_active=True, is_mega_user=True
        )
        self.admin = User.objects.create_user(
            "ai_admin", password="x", is_active=True, role=User.Role.ADMIN
        )

    def test_non_mega_user_is_denied(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(reverse("ai:tools")).status_code, 403)

    def test_mega_user_gets_the_workspace(self):
        self.client.force_login(self.mega)
        self.assertEqual(self.client.get(reverse("ai:tools")).status_code, 200)

    def test_admin_without_the_flag_is_denied(self):
        """Being an admin doesn't imply having connected an AI provider."""
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("ai:tools")).status_code, 403)

    def test_nav_entry_only_for_mega_users(self):
        self.client.force_login(self.plain)
        self.assertNotIn("AI tools", self.client.get(reverse("core:dashboard")).content.decode())
        self.client.force_login(self.mega)
        self.assertIn("AI tools", self.client.get(reverse("core:dashboard")).content.decode())


class MegaThemeTests(TestCase):
    def setUp(self):
        self.mega = User.objects.create_user(
            "theme_mega", password="x", is_active=True, is_mega_user=True
        )
        self.plain = User.objects.create_user("theme_plain", password="x", is_active=True)

    def test_theme_class_applied_for_mega_users(self):
        self.client.force_login(self.mega)
        self.assertIn('overflow-hidden mega"', self.client.get(reverse("core:dashboard")).content.decode())

    def test_no_theme_class_for_other_users(self):
        self.client.force_login(self.plain)
        self.assertNotIn('overflow-hidden mega"', self.client.get(reverse("core:dashboard")).content.decode())

    def test_toggle_returns_to_the_standard_palette(self):
        self.client.force_login(self.mega)
        self.client.post(reverse("ai:toggle_theme"))
        self.mega.refresh_from_db()
        self.assertFalse(self.mega.mega_theme_enabled)
        # Still a Mega User — the AI tools stay available.
        self.assertTrue(self.mega.is_mega_user)
        self.assertEqual(self.client.get(reverse("ai:tools")).status_code, 200)
        self.assertNotIn('overflow-hidden mega"', self.client.get(reverse("core:dashboard")).content.decode())


class APIKeyStorageTests(TestCase):
    def setUp(self):
        self.mega = User.objects.create_user(
            "key_mega", password="x", is_active=True, is_mega_user=True
        )

    def test_key_is_encrypted_at_rest_and_never_rendered(self):
        connection = AIConnection.objects.create(user=self.mega)
        connection.set_api_key("sk-ant-secret-value-1234")
        connection.save()

        connection.refresh_from_db()
        self.assertNotIn("sk-ant-secret-value-1234", connection.encrypted_api_key)
        self.assertEqual(connection.api_key, "sk-ant-secret-value-1234")
        self.assertEqual(connection.key_hint, "…1234")

        self.client.force_login(self.mega)
        page = self.client.get(reverse("ai:tools")).content.decode()
        self.assertNotIn("sk-ant-secret-value-1234", page)
        self.assertIn("…1234", page)

    def test_unreadable_ciphertext_degrades_to_disconnected(self):
        """A SECRET_KEY rotation must not 500 every page — it asks to reconnect."""
        connection = AIConnection.objects.create(
            user=self.mega, encrypted_api_key="not-a-valid-fernet-token"
        )
        self.assertEqual(connection.api_key, "")
        self.assertFalse(connection.is_connected)

    def test_roundtrip(self):
        self.assertEqual(crypto.decrypt(crypto.encrypt("hello")), "hello")

    def test_connect_rejects_a_key_the_provider_refuses(self):
        self.client.force_login(self.mega)
        with patch("apps.ai.client.verify_key", return_value="That API key was rejected."):
            self.client.post(
                reverse("ai:connect"), {"api_key": "sk-bad", "model_id": "claude-opus-5"}
            )
        self.assertFalse(AIConnection.objects.get(user=self.mega).is_connected)

    def test_changing_model_keeps_the_stored_key(self):
        connection = AIConnection.objects.create(user=self.mega)
        connection.set_api_key("sk-ant-keepme-9999")
        connection.save()

        self.client.force_login(self.mega)
        self.client.post(reverse("ai:connect"), {"api_key": "", "model_id": "claude-sonnet-5"})

        connection.refresh_from_db()
        self.assertEqual(connection.model_id, "claude-sonnet-5")
        self.assertEqual(connection.api_key, "sk-ant-keepme-9999")

    def test_disconnect_forgets_the_key(self):
        connection = AIConnection.objects.create(user=self.mega)
        connection.set_api_key("sk-ant-forgetme-0000")
        connection.save()

        self.client.force_login(self.mega)
        self.client.post(reverse("ai:disconnect"))

        connection.refresh_from_db()
        self.assertFalse(connection.is_connected)
        self.assertEqual(connection.key_hint, "")


class BriefingScopeTests(TestCase):
    """The briefing must never widen what a user can see."""

    def setUp(self):
        self.owner = User.objects.create_user(
            "brief_owner", password="x", is_active=True, is_mega_user=True
        )
        self.outsider = User.objects.create_user(
            "brief_outsider", password="x", is_active=True, is_mega_user=True
        )
        self.admin = User.objects.create_user(
            "brief_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(name="Mine POC", created_by=self.admin, status="active")
        POCMembership.objects.create(poc=self.poc, user=self.owner, role_in_poc="member")
        UseCase.objects.create(
            poc=self.poc, code="UC-1", title="Islanding",
            description="Runs islanded.", created_by=self.admin,
        )
        Requirement.objects.create(
            poc=self.poc, sub_system="BESS", req_gravity="imposes_mvp",
            req_operation="navigation", req_functional="performance",
            req_category="normal_operation", description="Holds SOC above 20%.",
            created_by=self.admin,
        )

    def test_member_sees_their_poc_content(self):
        briefing, scope = build_briefing(self.owner)
        self.assertEqual(scope["pocs"], 1)
        self.assertIn("Mine POC", briefing)
        self.assertIn("Islanding", briefing)
        self.assertIn("Holds SOC above 20%.", briefing)

    def test_non_member_sees_nothing(self):
        briefing, scope = build_briefing(self.outsider)
        self.assertEqual(scope["pocs"], 0)
        self.assertEqual(briefing, "")

    def test_admin_sees_every_poc(self):
        _, scope = build_briefing(self.admin)
        self.assertEqual(scope["pocs"], 1)


class ChatEndpointTests(TestCase):
    def setUp(self):
        self.mega = User.objects.create_user(
            "chat_mega", password="x", is_active=True, is_mega_user=True
        )
        self.connection = AIConnection.objects.create(user=self.mega)
        self.connection.set_api_key("sk-ant-chat-1111")
        self.connection.save()

    def _post(self, message="hello"):
        return self.client.post(
            reverse("ai:chat"),
            data=json.dumps({"message": message}),
            content_type="application/json",
        )

    def test_reply_is_returned_and_remembered(self):
        self.client.force_login(self.mega)
        with patch("apps.ai.client.send_message", return_value=("**Answer**", None)) as send:
            response = self._post("Which requirements lack tests?")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reply"], "**Answer**")
        self.assertIn("<strong>Answer</strong>", payload["reply_html"])
        # The briefing is passed as system context, not glued onto the message.
        self.assertEqual(send.call_args.kwargs["user_message"], "Which requirements lack tests?")
        # Second turn carries the first.
        with patch("apps.ai.client.send_message", return_value=("Second", None)) as send:
            self._post("and now?")
        self.assertEqual(len(send.call_args.kwargs["history"]), 2)

    def test_provider_errors_surface_as_a_message_not_a_500(self):
        self.client.force_login(self.mega)
        with patch("apps.ai.client.send_message", return_value=(None, "Rate limited.")):
            response = self._post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "Rate limited.")

    def test_chat_requires_a_connection(self):
        self.connection.encrypted_api_key = ""
        self.connection.save()
        self.client.force_login(self.mega)
        self.assertEqual(self._post().status_code, 400)

    def test_non_mega_user_cannot_chat(self):
        plain = User.objects.create_user("chat_plain", password="x", is_active=True)
        self.client.force_login(plain)
        self.assertEqual(self._post().status_code, 403)

    def test_reset_clears_the_conversation(self):
        self.client.force_login(self.mega)
        with patch("apps.ai.client.send_message", return_value=("A", None)):
            self._post()
        self.client.post(reverse("ai:reset"))
        with patch("apps.ai.client.send_message", return_value=("B", None)) as send:
            self._post()
        self.assertEqual(send.call_args.kwargs["history"], [])


class AdminGrantsMegaUserTests(TestCase):
    """Only admins can grant the flag — it's on the admin-only user form."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "grant_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.target = User.objects.create_user("grant_target", password="x", is_active=True)

    def test_admin_form_exposes_the_flag(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("accounts:user_edit", args=[self.target.pk])).content.decode()
        self.assertIn("is_mega_user", html)

    def test_admin_can_grant_it(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("accounts:user_edit", args=[self.target.pk]),
            {
                "username": self.target.username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "role": User.Role.TEAM_MEMBER,
                "is_mega_user": "on",
                "is_active": "on",
            },
        )
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_mega_user)

    def test_the_profile_page_does_not_let_a_user_grant_it_to_themselves(self):
        self.client.force_login(self.target)
        self.client.post(
            reverse("accounts:profile"),
            {"first_name": "T", "last_name": "T", "email": "t@example.com", "is_mega_user": "on"},
        )
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_mega_user)
