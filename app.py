"""PlantCare AI — "Snap your plant. Understand it. Care for it."

A Streamlit app that analyses plant photos with Google Gemini, keeps one
persistent conversation per session, and sends a Gemini-written plant-care
report to the user's own WhatsApp number through Twilio.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from google import genai
from google.genai import types
from PIL import Image, UnidentifiedImageError
from twilio.rest import Client as TwilioClient

from prompts import (
    APP_TAGLINE,
    GEMINI_ERROR_MESSAGE,
    IMAGE_ANALYSIS_REQUEST,
    IMAGE_ERROR_MESSAGE,
    PHOTO_TIPS,
    SUMMARY_ERROR_MESSAGE,
    SUMMARY_REQUEST_PROMPT,
    SUGGESTED_QUESTIONS,
    SYSTEM_PROMPT,
    WHATSAPP_ERROR_MESSAGE,
    WELCOME_MESSAGE_TEMPLATE,
)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Single place to change the Gemini model. Gemini 3.8 Flash is a stable
# (GA) Flash-class model with image understanding, text output and multi-turn
# chat, which is what a fast interactive Streamlit app needs.
MODEL_NAME = "gemini-3.8-flash"

GEMINI_SECRET = "GEMINI_API_KEY"
TWILIO_SECRETS = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_WHATSAPP_FROM",
    "TWILIO_CONTENT_SID",
]

ALLOWED_IMAGE_TYPES = ["jpg", "jpeg", "png"]
MAX_UPLOAD_MB = 8
# Phone photos are far larger than Gemini needs, so anything bigger than this
# is scaled down before it is sent (which also cuts token cost).
MAX_IMAGE_SIDE = 1600
# Comfortably inside a WhatsApp template body.
MAX_WHATSAPP_CHARS = 1000
SENT_MESSAGE_STATUSES = {"queued", "accepted", "sending", "sent", "delivered", "read"}

CHAT_INPUT_KEY = "plantcare_chat_input"
CAMERA_EPOCH_KEY = "plantcare_camera_epoch"
CAMERA_PENDING_KEY = "plantcare_pending_photo"
SUGGESTED_PROMPT_KEY = "plantcare_suggested_prompt"
LAST_REPORT_KEY = "plantcare_last_report"
REPORT_NOTICE_KEY = "plantcare_report_notice"
SENT_REPORT_COUNT_KEY = "plantcare_sent_report_count"

LOGGER = logging.getLogger("plantcare")


class ImageValidationError(Exception):
    """Raised when uploaded bytes are not a usable image."""


# --------------------------------------------------------------------------
# Secrets and cached clients
# --------------------------------------------------------------------------


def load_secret(name: str) -> Optional[str]:
    """Return a secret value from st.secrets, or None when it is not set."""
    try:
        value = st.secrets[name]
    except Exception:  # missing secrets file, missing key, or bad access
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def missing_gemini_secrets() -> List[str]:
    return [] if load_secret(GEMINI_SECRET) else [GEMINI_SECRET]


def missing_twilio_secrets() -> List[str]:
    return [name for name in TWILIO_SECRETS if not load_secret(name)]


@st.cache_resource(show_spinner=False)
def get_gemini_client(api_key: str):
    """One Gemini client per key, reused across reruns and sessions."""
    return genai.Client(api_key=api_key)


@st.cache_resource(show_spinner=False)
def get_twilio_client(account_sid: str, auth_token: str):
    """One Twilio client per credential pair, reused across reruns."""
    return TwilioClient(account_sid, auth_token)


def create_chat():
    """Open a fresh Gemini conversation scoped to one plant."""
    client = get_gemini_client(load_secret(GEMINI_SECRET))
    return client.chats.create(
        model=MODEL_NAME,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=2048,
            # Keeps answers snappy for an interactive chat.
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )


def log_api_failure(scope: str, error: Exception) -> None:
    """Log enough to debug locally, never secret values."""
    LOGGER.warning(
        "%s failed: %s (code=%s)",
        scope,
        type(error).__name__,
        getattr(error, "code", "n/a"),
    )


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

SESSION_DEFAULTS: Dict[str, Any] = {
    "onboarded": False,
    "name": "",
    "whatsapp_number": "",
    "chat": None,
    "messages": [],
    "plant_has_been_analyzed": False,
    SUGGESTED_PROMPT_KEY: "",
    CAMERA_PENDING_KEY: None,
    CAMERA_EPOCH_KEY: 0,
    LAST_REPORT_KEY: "",
    REPORT_NOTICE_KEY: "",
    SENT_REPORT_COUNT_KEY: -1,
}


def initialize_session_state() -> None:
    for key, value in SESSION_DEFAULTS.items():
        if key not in st.session_state:
            # Copied, never shared: every session needs its own list.
            st.session_state[key] = copy.deepcopy(value)


def add_message(role: str, kind: str, content: Any, caption: str = "") -> None:
    st.session_state.messages.append(
        {"role": role, "kind": kind, "content": content, "caption": caption}
    )


def count_user_messages() -> int:
    return sum(1 for message in st.session_state.messages if message["role"] == "user")


def start_conversation() -> bool:
    """Create the Gemini chat and show the welcome message for a new session."""
    st.session_state.messages = []
    st.session_state.plant_has_been_analyzed = False
    st.session_state[LAST_REPORT_KEY] = ""
    st.session_state[REPORT_NOTICE_KEY] = ""
    st.session_state[SENT_REPORT_COUNT_KEY] = -1
    try:
        st.session_state.chat = create_chat()
    except Exception as error:
        log_api_failure("Gemini chat creation", error)
        st.session_state.chat = None
        return False
    add_message(
        "assistant",
        "text",
        WELCOME_MESSAGE_TEMPLATE.format(name=st.session_state.name),
    )
    return True


def reset_plant_session() -> None:
    """Start a new plant: fresh Gemini chat, same name and WhatsApp number."""
    st.session_state[SUGGESTED_PROMPT_KEY] = ""
    st.session_state[CAMERA_PENDING_KEY] = None
    start_conversation()


# --------------------------------------------------------------------------
# Small pure helpers
# --------------------------------------------------------------------------


def normalize_phone_number(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Turn user input into an E.164 number.

    Returns (number, error_message); exactly one of the two is set.
    """
    text = str(raw or "").strip().lower()
    if text.startswith("whatsapp:"):
        text = text[len("whatsapp:") :]
    cleaned = re.sub(r"[\s\-().]", "", text)
    if not cleaned:
        return None, "Please enter your WhatsApp number, including the country code."
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    if not re.fullmatch(r"\+[0-9]{8,15}", cleaned):
        return None, (
            "That doesn't look like a complete international number. Use digits "
            "with a leading +, for example +919876543210."
        )
    return cleaned, None


def mask_phone_number(number: str) -> str:
    """Show only enough of a number to confirm it, never the whole thing."""
    digits = str(number or "").lstrip("+")
    if len(digits) <= 4:
        return "+" + "*" * len(digits)
    return "+" + digits[0] + "*" * (len(digits) - 3) + digits[-2:]


def to_whatsapp_address(number: str) -> str:
    """Return the destination in Twilio's whatsapp:+E164 form, never doubled."""
    digits = re.sub(r"[^0-9]", "", str(number or ""))
    return "whatsapp:+" + digits


def clean_whatsapp_text(text: str, max_chars: int = MAX_WHATSAPP_CHARS) -> str:
    """Make Gemini output safe and tidy for a WhatsApp template variable."""
    cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"^\s{0,4}[-*•–—]+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace("**", "").replace("*", "")
    cleaned = cleaned.replace("_", "").replace("`", "")
    cleaned = re.sub(r"^\s*#+\s*", "", cleaned, flags=re.MULTILINE)
    lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.split("\n")]
    # WhatsApp template content variables must be single-line, so sections are
    # joined with " | " instead of keeping line breaks.
    joined = " | ".join(line for line in lines if line)
    joined = re.sub(r"\s{2,}", " ", joined).strip()
    if len(joined) > max_chars:
        joined = joined[: max_chars - 4].rsplit(" ", 1)[0].rstrip() + " ..."
    return joined


def prepare_image(data: bytes) -> Tuple[bytes, str, Tuple[int, int]]:
    """Validate an image and return (jpeg_bytes, mime_type, (width, height)).

    Raises ImageValidationError when the bytes are not a readable image.
    """
    if not data:
        raise ImageValidationError("empty upload")
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            picture = image if image.mode in ("RGB", "L") else image.convert("RGB")
            picture.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            size = picture.size
            buffer = BytesIO()
            picture.save(buffer, format="JPEG", quality=90)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ImageValidationError("unreadable image") from error
    return buffer.getvalue(), "image/jpeg", size


def is_supported_image(uploaded_file: Any) -> bool:
    name = str(getattr(uploaded_file, "name", "") or "").lower()
    return name.endswith(tuple("." + item for item in ALLOWED_IMAGE_TYPES))


def submission_text(submission: Any) -> str:
    """Read the text out of a chat_input submission across Streamlit versions."""
    if submission is None:
        return ""
    if isinstance(submission, str):
        return submission.strip()
    text = getattr(submission, "text", None)
    if text is None:
        try:
            text = submission.get("text")
        except AttributeError:
            text = None
    return str(text or "").strip()


def submission_files(submission: Any) -> List[Any]:
    if submission is None:
        return []
    files = getattr(submission, "files", None)
    if files is None:
        try:
            files = submission.get("files")
        except AttributeError:
            files = None
    return list(files or [])


# --------------------------------------------------------------------------
# Gemini conversation
# --------------------------------------------------------------------------


def ensure_chat():
    """Return the session's Gemini chat, creating it only when missing."""
    if st.session_state.chat is None:
        st.session_state.chat = create_chat()
    return st.session_state.chat


def ask_gemini(
    chat: Any,
    prompt_text: str,
    image: Optional[Tuple[bytes, str]] = None,
) -> str:
    """Send one turn to Gemini through the existing conversation."""
    parts: List[Any] = []
    if image is not None:
        data, mime_type = image
        parts.append(types.Part.from_bytes(data=data, mime_type=mime_type))
    parts.append(types.Part.from_text(text=prompt_text))
    response = chat.send_message(parts)
    return str(response.text or "").strip()


def handle_turn(text: str, image_bytes: Optional[bytes]) -> None:
    """Record a user turn, ask Gemini, and record the reply."""
    caption = text.strip()
    prepared: Optional[Tuple[bytes, str]] = None
    st.session_state[REPORT_NOTICE_KEY] = ""

    if image_bytes is not None:
        try:
            jpeg_bytes, mime_type, _size = prepare_image(image_bytes)
        except ImageValidationError:
            # The bytes are unusable, so nothing is stored for redisplay -
            # st.image() would fail on them on every later rerun.
            if caption:
                add_message("user", "text", caption)
            add_message("assistant", "text", IMAGE_ERROR_MESSAGE)
            return
        prepared = (jpeg_bytes, mime_type)
        add_message("user", "image", image_bytes, caption=caption)
        prompt = f"{caption}\n\n{IMAGE_ANALYSIS_REQUEST}".strip()
        st.session_state.plant_has_been_analyzed = True
    else:
        prompt = caption
        add_message("user", "text", caption)

    try:
        chat = ensure_chat()
        with st.spinner("PlantCare AI is looking at your plant..."):
            reply = ask_gemini(chat, prompt, prepared)
    except Exception as error:  # never let one API failure kill the app
        log_api_failure("Gemini turn", error)
        add_message("assistant", "text", GEMINI_ERROR_MESSAGE)
        return

    add_message("assistant", "text", reply or GEMINI_ERROR_MESSAGE)


def send_report_to_whatsapp() -> None:
    """Summarize this conversation with Gemini and send it via Twilio."""
    chat = st.session_state.chat
    if chat is None:
        st.session_state[REPORT_NOTICE_KEY] = "summary_failed"
        st.rerun()

    with st.spinner("Preparing your plant-care report..."):
        try:
            summary = ask_gemini(chat, SUMMARY_REQUEST_PROMPT)
        except Exception as error:
            log_api_failure("Gemini summary", error)
            summary = ""

    if not summary:
        st.session_state[REPORT_NOTICE_KEY] = "summary_failed"
        st.rerun()

    st.session_state[LAST_REPORT_KEY] = summary

    with st.spinner("Sending to WhatsApp..."):
        try:
            send_whatsapp(
                st.session_state.whatsapp_number, st.session_state.name, summary
            )
        except Exception as error:
            log_api_failure("Twilio send", error)
            st.session_state[REPORT_NOTICE_KEY] = "send_failed"
            st.rerun()

    st.session_state[SENT_REPORT_COUNT_KEY] = count_user_messages()
    st.session_state[REPORT_NOTICE_KEY] = "sent"
    # Rerunning redraws the panel in its locked state, so the button cannot be
    # clicked twice for the same conversation.
    st.rerun()


def send_whatsapp(to_number: str, user_name: str, summary: str) -> None:
    """Send the report with the approved Twilio WhatsApp content template."""
    account_sid = load_secret("TWILIO_ACCOUNT_SID")
    auth_token = load_secret("TWILIO_AUTH_TOKEN")
    whatsapp_from = load_secret("TWILIO_WHATSAPP_FROM")
    content_sid = load_secret("TWILIO_CONTENT_SID")
    if not all([account_sid, auth_token, whatsapp_from, content_sid]):
        raise RuntimeError("Twilio is not configured")

    client = get_twilio_client(account_sid, auth_token)
    message = client.messages.create(
        content_sid=content_sid,
        to=to_whatsapp_address(to_number),
        from_=whatsapp_from,
        content_variables=json.dumps(
            {
                "1": clean_whatsapp_text(user_name, max_chars=40),
                "2": clean_whatsapp_text(summary),
            }
        ),
    )

    status = str(getattr(message, "status", "") or "").lower()
    if status not in SENT_MESSAGE_STATUSES:
        raise RuntimeError(f"Twilio did not accept the message: {status or 'no status'}")


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### 🌱 PlantCare AI")
        st.markdown(f"_{APP_TAGLINE}_")
        st.divider()

        st.markdown("📸 **For a better analysis**")
        for tip in PHOTO_TIPS:
            st.markdown(f"- {tip}")

        st.divider()

        if st.session_state.onboarded:
            render_camera_capture()
            st.divider()

        st.markdown("🔒 **Privacy**")
        st.caption(
            "Photos stay in this browser session and are sent straight to Gemini. "
            "PlantCare AI never stores your pictures."
        )
        st.caption(f"Model: `{MODEL_NAME}`")

        if st.session_state.onboarded:
            st.divider()
            st.button(
                "🔄 Analyze New Plant",
                width="stretch",
                on_click=reset_plant_session,
            )
            st.button(
                "🚪 Start over",
                width="stretch",
                type="tertiary",
                on_click=clear_session,
            )


def render_camera_capture() -> None:
    """Camera capture, parked in the sidebar to keep the chat page clean."""
    with st.expander("📷 Take a photo with your camera"):
        st.caption(
            "Handy on a phone. Your browser will ask for camera permission, and "
            "it only works on secure (https) pages."
        )
        # Every capture gets a fresh widget key. That is the supported way to
        # clear a camera widget, and it guarantees one capture is analysed once
        # and is never re-sent to Gemini on a later rerun.
        widget_key = f"plantcare_camera_{st.session_state[CAMERA_EPOCH_KEY]}"
        photo = st.camera_input(
            "Frame your plant, then tap the shutter",
            key=widget_key,
        )
        if photo is not None:
            st.session_state[CAMERA_PENDING_KEY] = photo.getvalue()
            st.session_state[CAMERA_EPOCH_KEY] = (
                int(st.session_state[CAMERA_EPOCH_KEY]) + 1
            )
            st.session_state.pop(widget_key, None)


def render_setup_notice() -> None:
    missing = missing_gemini_secrets()
    st.title("🌱 PlantCare AI")
    st.markdown(f"_{APP_TAGLINE}_")
    st.error("PlantCare AI is not configured yet.")
    st.markdown(
        "Missing setting(s): "
        + ", ".join(f"`{name}`" for name in missing)
    )
    st.markdown(
        """
**Local setup**

1. Copy the example file to a real secrets file:
   `cp .streamlit/secrets.toml.example .streamlit/secrets.toml`
   (Windows PowerShell: `Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml`)
2. Fill in your own values for the keys listed above.
3. Restart the app with `streamlit run app.py`.

**Streamlit Community Cloud**

Add the same keys in **App -> Settings -> Secrets**, then redeploy.

Never commit `.streamlit/secrets.toml` — it is ignored by Git on purpose.
"""
    )


def render_onboarding() -> None:
    st.title("🌱 PlantCare AI")
    st.markdown(f"**{APP_TAGLINE}**")
    st.markdown(
        "Take or upload a clear photo of your plant and chat with PlantCare AI "
        "about what you see and how to care for it."
    )
    st.markdown(
        "- 📸 Get a possible identification and what is visible on the plant\n"
        "- 💧 Practical guidance on watering, light and everyday care\n"
        "- 📲 Send the whole conversation to your own WhatsApp as a short report"
    )
    st.divider()

    with st.form("plantcare_onboarding", clear_on_submit=False):
        name = st.text_input("Your name", placeholder="e.g. Aisha")
        whatsapp_number = st.text_input(
            "WhatsApp number (with country code)",
            placeholder="+91XXXXXXXXXX",
            help="Include your country code so the report can reach you.",
        )
        submitted = st.form_submit_button(
            "Start Growing 🌱", type="primary", width="stretch"
        )

    if not submitted:
        st.caption(
            "PlantCare AI gives image-based guidance, not a professional "
            "diagnosis. Photos are used only for this conversation."
        )
        return

    clean_name = name.strip()
    normalized_number, error = normalize_phone_number(whatsapp_number)
    if not clean_name:
        st.error("Please enter your name so we can greet you properly.")
        return
    if error:
        st.error(error)
        return

    st.session_state.name = clean_name
    st.session_state.whatsapp_number = normalized_number
    if not start_conversation():
        st.error(GEMINI_ERROR_MESSAGE)
        return
    st.session_state.onboarded = True
    st.rerun()


def render_report_panel() -> None:
    st.markdown("### 📲 Send Plant Care Report")
    last_report = st.session_state.get(LAST_REPORT_KEY) or ""
    if last_report:
        with st.expander("Preview your last report"):
            st.text(last_report)

    user_messages = count_user_messages()
    missing_twilio = missing_twilio_secrets()
    already_sent = st.session_state.get(SENT_REPORT_COUNT_KEY) == user_messages
    disabled = user_messages == 0 or bool(missing_twilio) or already_sent

    if user_messages == 0:
        hint = "Ask about your plant first — the report is built from our chat."
    elif missing_twilio:
        hint = "WhatsApp is not configured yet (missing: " + ", ".join(
            missing_twilio
        ) + ")."
    elif already_sent:
        hint = "Report sent for this conversation. Add another message to refresh it."
    else:
        hint = f"Ready to send to {mask_phone_number(st.session_state.whatsapp_number)}."

    if st.button(
        "📲 Send Plant Care Report to WhatsApp",
        type="primary",
        disabled=disabled,
        width="stretch",
    ):
        send_report_to_whatsapp()
    st.caption(hint)

    notice = st.session_state.get(REPORT_NOTICE_KEY)
    if notice == "sent":
        st.success("Sent! Check your WhatsApp 📲")
    elif notice == "summary_failed":
        st.error(SUMMARY_ERROR_MESSAGE)
    elif notice == "send_failed":
        st.error(WHATSAPP_ERROR_MESSAGE)


def render_suggested_questions() -> None:
    if count_user_messages() > 0:
        return
    st.markdown("**Not sure where to start? Try one of these:**")
    columns = st.columns(len(SUGGESTED_QUESTIONS))
    for column, question in zip(columns, SUGGESTED_QUESTIONS):
        if column.button(question, width="stretch", key=f"suggestion_{question}"):
            st.session_state[SUGGESTED_PROMPT_KEY] = question
            st.rerun()


def read_chat_submission() -> Optional[Dict[str, Any]]:
    """Render the chat input and return the turn the user just submitted.

    A turn is ``{"text": str, "image": bytes | None}``. ``None`` means there
    is nothing new to send to Gemini.
    """
    suggested_prompt = str(st.session_state.get(SUGGESTED_PROMPT_KEY) or "")
    pending_photo = st.session_state.get(CAMERA_PENDING_KEY)

    submission = st.chat_input(
        "Ask about your plant, or attach a photo of it...",
        key=CHAT_INPUT_KEY,
        accept_file=True,
        file_type=ALLOWED_IMAGE_TYPES,
        max_upload_size=MAX_UPLOAD_MB,
    )

    if suggested_prompt:
        st.session_state[SUGGESTED_PROMPT_KEY] = ""
        return {"text": suggested_prompt, "image": None}

    if pending_photo is not None:
        st.session_state[CAMERA_PENDING_KEY] = None
        return {"text": "", "image": pending_photo}

    text = submission_text(submission)
    files = submission_files(submission)
    if not text and not files:
        return None

    uploaded_image = None
    for uploaded_file in files:
        if is_supported_image(uploaded_file):
            uploaded_image = uploaded_file.getvalue()
            break
    if files and uploaded_image is None:
        st.warning(IMAGE_ERROR_MESSAGE)
        return None

    return {"text": text, "image": uploaded_image}


def render_history() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["kind"] == "image":
                st.image(message["content"], caption=message.get("caption") or None)
            else:
                st.markdown(message["content"])


def render_chat_page() -> None:
    st.title("🌱 PlantCare AI")
    st.markdown("Your AI companion for understanding and caring for plants.")
    st.markdown(
        f"Logged in as **{st.session_state.name}** · report goes to "
        f"**{mask_phone_number(st.session_state.whatsapp_number)}**"
    )
    st.divider()

    # The turn is handled before the rest of the page is drawn, so the report
    # button and the suggested questions always match the conversation below.
    turn = read_chat_submission()
    if turn is not None:
        handle_turn(turn["text"], turn["image"])

    render_report_panel()
    render_suggested_questions()
    st.divider()
    render_history()


def clear_session() -> None:
    """Forget the user and return to the onboarding screen."""
    for key, value in SESSION_DEFAULTS.items():
        st.session_state[key] = copy.deepcopy(value)
    st.session_state[CHAT_INPUT_KEY] = ""


def main() -> None:
    st.set_page_config(
        page_title="PlantCare AI",
        page_icon="🌱",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    initialize_session_state()
    render_sidebar()

    if missing_gemini_secrets():
        render_setup_notice()
        return
    if not st.session_state.onboarded:
        render_onboarding()
        return
    render_chat_page()


if __name__ == "__main__":
    main()
