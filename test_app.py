"""Prompt-quality and helper tests for PlantCare AI.

Run with:

    python -m unittest test_app -v

The Gemini calls are mocked, so nothing here needs an API key or a network
connection.
"""

from __future__ import annotations

import contextlib
import io
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from PIL import Image

import app
from prompts import (
    IMAGE_ANALYSIS_REQUEST,
    SUMMARY_REQUEST_PROMPT,
    SYSTEM_PROMPT,
    WELCOME_MESSAGE_TEMPLATE,
)

APP_SOURCE = open("app.py", encoding="utf-8").read()

# The wording the live Gemini test produced for a follow-up question. It is
# kept here as the regression fixture for the invented-statistic and
# photo-derived soil-moisture rules.
BAD_FOLLOW_UP_REPLY = (
    "Those two factors cause about 90% of early yellowing, and the soil looks "
    "damp, so water twice a week."
)
GOOD_FOLLOW_UP_REPLY = (
    "Based on what is visible, the older lower leaves are the ones changing "
    "colour, so watering habits and light are worth checking first. Light is "
    "often a contributing factor, but I cannot tell soil moisture from the "
    "photo, so check the top few centimetres with a finger."
)


# --------------------------------------------------------------------------
# Test-only guard: catches the exact claims the prompt now forbids
# --------------------------------------------------------------------------

PERCENTAGE_CLAIM = re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent)")
OUT_OF_CLAIM = re.compile(r"\b(?:one|two|three|\d+)\s+in\s+(?:three|four|five|\d+)\b")
SOIL_MOISTURE_CLAIM = re.compile(
    r"\bsoil\b[^.!?]*\b(?:damp|dry|wet|moist|soggy|waterlogged|bone dry)\b"
)


def find_unsupported_claims(text: str) -> list:
    """Return the unsupported claims in a Gemini reply.

    This lives in the test suite on purpose: it documents what the prompts must
    prevent, and it is not part of the running app.
    """
    claims = []
    if PERCENTAGE_CLAIM.search(text):
        claims.append("percentage")
    if OUT_OF_CLAIM.search(text):
        claims.append("ratio")
    if SOIL_MOISTURE_CLAIM.search(text):
        claims.append("soil moisture")
    return claims


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeSession(dict):
    """Session state that supports both item and attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name, value):
        self[name] = value


def make_streamlit(session_state=None, secrets=None):
    """A tiny stand-in for the Streamlit module used by app.py helpers."""

    class FakeSecrets:
        def __init__(self, values):
            self._values = values

        def __getitem__(self, key):
            if key not in self._values:
                raise KeyError(key)
            return self._values[key]

    @contextlib.contextmanager
    def spinner(_message):
        yield

    return SimpleNamespace(
        session_state=FakeSession() if session_state is None else session_state,
        secrets=FakeSecrets({} if secrets is None else secrets),
        spinner=spinner,
        rerun=lambda: None,
    )


class FakeGeminiChat:
    """Stand-in for a google.genai chat that replays canned replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent_parts = []

    def send_message(self, parts):
        self.sent_parts.append(parts)
        return SimpleNamespace(text=self.replies.pop(0))


def make_image_bytes(fmt: str = "JPEG", size=(64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 160, 90)).save(buffer, format=fmt)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Prompt tests
# --------------------------------------------------------------------------


class PromptQualityTests(unittest.TestCase):
    def assertRule(self, needle, text):
        """Prompts are hard-wrapped, so compare on flattened text."""
        flat = " ".join(text.lower().split())
        self.assertIn(" ".join(needle.lower().split()), flat, f"missing rule: {needle!r}")

    def test_system_prompt_forbids_invented_statistics(self):
        for rule in (
            "numbers, statistics and quantities",
            "never invent a number, a percentage, a probability",
            "never present a guess as an estimate",
            # The live failure is named as a forbidden example.
            "about 90%",
            "2 out of 3 plants",
        ):
            self.assertRule(rule, SYSTEM_PROMPT)

    def test_system_prompt_limits_where_numbers_may_come_from(self):
        for rule in (
            "allowed only when the user gave it to you",
            "clearly readable in the photo or in text they shared",
            "describe amount, frequency, size and severity in words instead",
        ):
            self.assertRule(rule, SYSTEM_PROMPT)

    def test_system_prompt_handles_how_common_questions_without_a_figure(self):
        for rule in (
            "when the user asks how common something is",
            "you cannot give a number for it",
            "say what is worth checking first",
        ):
            self.assertRule(rule, SYSTEM_PROMPT)

    def test_system_prompt_forbids_soil_moisture_read_from_a_photo(self):
        self.assertRule("what a photo cannot show (especially soil)", SYSTEM_PROMPT)
        self.assertRule('do not label soil "damp", "dry"', SYSTEM_PROMPT)
        self.assertRule("from a photo alone", SYSTEM_PROMPT)
        self.assertRule("genuinely and unmistakably visible", SYSTEM_PROMPT)
        for word in ("waterlogged", "bone dry"):
            self.assertRule(word, SYSTEM_PROMPT)

    def test_system_prompt_offers_a_soil_check_instead(self):
        for rule in ("press a finger", "drainage holes"):
            self.assertRule(rule, SYSTEM_PROMPT)

    def test_system_prompt_prefers_hedge_wording(self):
        for phrase in (
            "based on what is visible",
            "worth checking first",
            "may be a contributing factor",
            "one thing worth ruling out",
            "i cannot tell from the photo alone",
        ):
            self.assertRule(phrase, SYSTEM_PROMPT)

    def test_accuracy_rules_still_forbid_invented_measurements(self):
        for rule in (
            "never invent symptoms, plant species, diseases, measurements",
            "a photo cannot confirm soil moisture",
            "may indicate",
        ):
            self.assertRule(rule, SYSTEM_PROMPT)

    def test_analysis_request_forbids_figures_and_soil_reading(self):
        for rule in (
            "do not include any percentage, probability or statistic",
            "do not claim how common a cause is",
            "do not state how wet or dry the soil is",
            "ask for a clearer plant photo",
        ):
            self.assertRule(rule, IMAGE_ANALYSIS_REQUEST)

    def test_summary_request_forbids_invented_numbers(self):
        for rule in (
            "never add a percentage, probability, statistic or exact quantity",
            "the report contains no figure",
            "do not invent anything that is missing",
        ):
            self.assertRule(rule, SUMMARY_REQUEST_PROMPT)

    def test_welcome_message_formats_with_the_user_name(self):
        self.assertIn("{name}", WELCOME_MESSAGE_TEMPLATE)
        self.assertTrue(WELCOME_MESSAGE_TEMPLATE.format(name="Aisha").startswith("Hey Aisha"))

    def test_prompts_stay_in_prompts_module(self):
        self.assertNotIn("You are PlantCare AI", APP_SOURCE)
        self.assertIn("SYSTEM_PROMPT", APP_SOURCE)

    def test_no_deprecated_gemini_import(self):
        self.assertNotIn("google.generativeai", APP_SOURCE)


# --------------------------------------------------------------------------
# Helper tests
# --------------------------------------------------------------------------


class HelperTests(unittest.TestCase):
    def test_normalize_phone_number_accepts_common_formats(self):
        for raw in ["+91 98765 43210", "0091-98765-43210", "(91) 9876543210"]:
            number, error = app.normalize_phone_number(raw)
            self.assertIsNone(error)
            self.assertTrue(number.startswith("+91"))

    def test_normalize_phone_number_strips_whatsapp_prefix(self):
        number, error = app.normalize_phone_number("whatsapp:+919876543210")
        self.assertEqual(number, "+919876543210")
        self.assertIsNone(error)

    def test_normalize_phone_number_rejects_incomplete_numbers(self):
        for raw in ["", "   ", "12345", "+9198765"]:
            number, error = app.normalize_phone_number(raw)
            self.assertIsNone(number)
            self.assertTrue(error)

    def test_mask_phone_number_hides_the_middle(self):
        masked = app.mask_phone_number("+919876543210")
        self.assertTrue(masked.startswith("+9"))
        self.assertTrue(masked.endswith("10"))
        self.assertNotIn("9876543", masked)

    def test_to_whatsapp_address_never_doubles_the_prefix(self):
        self.assertEqual(
            app.to_whatsapp_address("+91 98765-43210"), "whatsapp:+919876543210"
        )

    def test_clean_whatsapp_text_is_single_line_plain_text(self):
        cleaned = app.clean_whatsapp_text("## **Watering**\n- check the top of the soil")
        self.assertNotIn("**", cleaned)
        self.assertNotIn("#", cleaned)
        self.assertNotIn("\n", cleaned)

    def test_clean_whatsapp_text_truncates_to_one_variable(self):
        cleaned = app.clean_whatsapp_text("leaf " * 400, max_chars=100)
        self.assertLessEqual(len(cleaned), 100)
        self.assertTrue(cleaned.endswith("..."))

    def test_submission_text_reads_across_streamlit_versions(self):
        self.assertEqual(app.submission_text("  hi  "), "hi")
        self.assertEqual(app.submission_text(SimpleNamespace(text=" hi ")), "hi")
        self.assertEqual(app.submission_text({"text": "hi"}), "hi")
        self.assertEqual(app.submission_text(None), "")

    def test_submission_files_reads_across_streamlit_versions(self):
        uploaded = SimpleNamespace(name="plant.JPG")
        self.assertEqual(app.submission_files(SimpleNamespace(files=[uploaded])), [uploaded])
        self.assertEqual(app.submission_files({"files": [uploaded]}), [uploaded])
        self.assertEqual(app.submission_files(None), [])

    def test_is_supported_image_checks_the_extension(self):
        self.assertTrue(app.is_supported_image(SimpleNamespace(name="a.JPG")))
        self.assertTrue(app.is_supported_image(SimpleNamespace(name="a.png")))
        self.assertFalse(app.is_supported_image(SimpleNamespace(name="a.gif")))
        self.assertFalse(app.is_supported_image(SimpleNamespace(name="")))

    def test_prepare_image_returns_jpeg_and_size(self):
        jpeg_bytes, mime_type, size = app.prepare_image(make_image_bytes())
        self.assertEqual(mime_type, "image/jpeg")
        self.assertEqual(size, (64, 48))
        self.assertTrue(jpeg_bytes.startswith(b"\xff\xd8"))

    def test_prepare_image_rejects_unusable_bytes(self):
        for payload in [b"", b"not an image"]:
            with self.assertRaises(app.ImageValidationError):
                app.prepare_image(payload)

    def test_missing_secrets_report_names_only(self):
        fake = make_streamlit(secrets={})
        with mock.patch.object(app, "st", fake):
            self.assertEqual(app.missing_gemini_secrets(), ["GEMINI_API_KEY"])
            self.assertEqual(
                app.missing_twilio_secrets(),
                [
                    "TWILIO_ACCOUNT_SID",
                    "TWILIO_AUTH_TOKEN",
                    "TWILIO_WHATSAPP_FROM",
                ],
            )

    def test_missing_secrets_ignore_blank_values(self):
        fake = make_streamlit(secrets={"GEMINI_API_KEY": "   "})
        with mock.patch.object(app, "st", fake):
            self.assertEqual(app.missing_gemini_secrets(), ["GEMINI_API_KEY"])
        fake = make_streamlit(secrets={"GEMINI_API_KEY": "key"})
        with mock.patch.object(app, "st", fake):
            self.assertEqual(app.missing_gemini_secrets(), [])

    def test_handle_turn_reports_an_unreadable_image(self):
        fake = make_streamlit()
        with mock.patch.object(app, "st", fake):
            app.initialize_session_state()
            app.handle_turn("", b"not an image")
        contents = [message["content"] for message in fake.session_state["messages"]]
        self.assertEqual(contents, [app.IMAGE_ERROR_MESSAGE])


# --------------------------------------------------------------------------
# Mocked Gemini conversation
# --------------------------------------------------------------------------


class MockedFollowUpTests(unittest.TestCase):
    def test_follow_up_answer_is_checked_against_the_prompt_rules(self):
        self.assertEqual(
            find_unsupported_claims(BAD_FOLLOW_UP_REPLY),
            ["percentage", "soil moisture"],
        )
        self.assertEqual(find_unsupported_claims(GOOD_FOLLOW_UP_REPLY), [])

    def test_follow_up_reuses_the_existing_chat(self):
        chat = FakeGeminiChat([GOOD_FOLLOW_UP_REPLY])
        fake = make_streamlit()
        with mock.patch.object(app, "st", fake):
            app.initialize_session_state()
            fake.session_state["chat"] = chat
            with mock.patch.object(app, "create_chat") as new_chat:
                app.handle_turn("Why are the lower leaves yellow?", None)
        new_chat.assert_not_called()
        self.assertEqual(len(chat.sent_parts), 1)
        messages = fake.session_state["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "Why are the lower leaves yellow?")
        self.assertEqual(messages[1]["content"], GOOD_FOLLOW_UP_REPLY)
        self.assertEqual(find_unsupported_claims(messages[1]["content"]), [])

    def test_follow_up_image_turn_sends_the_photo_once(self):
        chat = FakeGeminiChat([GOOD_FOLLOW_UP_REPLY])
        fake = make_streamlit()
        with mock.patch.object(app, "st", fake):
            app.initialize_session_state()
            fake.session_state["chat"] = chat
            app.handle_turn("Why are the lower leaves yellow?", make_image_bytes())
        parts = chat.sent_parts[0]
        self.assertEqual(len(parts), 2)
        self.assertEqual(len([part for part in parts if part.inline_data]), 1)
        sent_text = "\n".join(getattr(part, "text", "") or "" for part in parts)
        self.assertIn("Analyze this plant photo", sent_text)
        self.assertTrue(fake.session_state["plant_has_been_analyzed"])

    def test_create_chat_sends_the_hardened_system_prompt(self):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return "chat-object"

        client = SimpleNamespace(chats=SimpleNamespace(create=fake_create))
        with mock.patch.object(app, "get_gemini_client", return_value=client):
            with mock.patch.object(app, "load_secret", return_value="a-key"):
                self.assertEqual(app.create_chat(), "chat-object")
        instruction = captured["config"].system_instruction
        self.assertEqual(instruction, SYSTEM_PROMPT)
        for rule in ("never invent a number", "from a photo alone", "worth checking first"):
            self.assertIn(rule, " ".join(instruction.lower().split()))


if __name__ == "__main__":
    unittest.main(verbosity=2)