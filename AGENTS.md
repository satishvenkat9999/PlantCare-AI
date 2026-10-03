# AGENTS.md — PlantCare AI

Project rules for anyone (human or AI agent) working in this repository.

## 1. What this project is

🌱 **PlantCare AI** — *"Snap your plant. Understand it. Care for it."*

A Streamlit app where a user onboards with a name and WhatsApp number, sends or
uploads a plant photo, chats with Google Gemini about the plant, and finally
sends a Gemini-written plant-care summary to their own WhatsApp number through
Twilio.

Core flow:

```
Onboarding
  -> Gemini chat session (persistent)
  -> image / text input
  -> Gemini plant analysis
  -> contextual follow-up questions
  -> Gemini summary of the same conversation
  -> Twilio sends the summary to WhatsApp
```

## 2. Hard technology rules

Use **only** this stack:

- Python (3.9+ compatible code)
- Streamlit (UI + `st.session_state` + `st.secrets`)
- Google Gemini via the modern SDK: `from google import genai` / `from google.genai import types`
- Twilio Python SDK (`from twilio.rest import Client`)

Never introduce: React, FastAPI, Flask, Node.js, MongoDB, Firebase, OpenCV,
MediaPipe, custom ML training, a separate frontend, or another messaging
provider. Do not add a database, an uploaded-image storage folder, generated
model files, or an API-key file.

Forbidden API: `import google.generativeai as genai` (deprecated).
Required: `from google import genai`.

## 3. Secrets rules

Required Streamlit secret names (read from `st.secrets`, never hardcoded):

| Secret | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Google AI Studio key |
| `TWILIO_ACCOUNT_SID` | Twilio account |
| `TWILIO_AUTH_TOKEN` | Twilio auth |
| `TWILIO_WHATSAPP_FROM` | Approved WhatsApp sender |
| `TWILIO_CONTENT_SID` | Approved WhatsApp content template |

- Only `.streamlit/secrets.toml.example` is tracked. Never create or commit
  `.streamlit/secrets.toml`.
- Never print, log, or surface a secret value in UI, errors, comments, docs,
  or tests. Only secret *names* may be shown.
- Never write real keys into `README.md`.

## 4. Accuracy / safety rules (product-critical)

PlantCare AI gives **image-based guidance, never a professional diagnosis**.

1. Never claim certainty the image does not support. Use "possible",
   "may indicate", "possible cause", "could be consistent with".
2. Separate what is *observed* from what is *inferred* from what *cannot be
   determined*.
3. Never invent symptoms, species, diseases, measurements, chemical
   concentrations, lab results, or certifications.
4. Never give exact watering volumes/schedules as facts.
5. Reject blurry, dark, empty, or non-plant images politely and ask for a
   clearer photo.
6. No dangerous chemical-mixing instructions or unsafe pesticide dosages.
7. Redirect non-plant questions back to plant care.

These rules live in `prompts.py` (`SYSTEM_PROMPT`), not in `app.py`.

## 5. Prompt rules

- All Gemini prompts/constants belong in `prompts.py`:
  `SYSTEM_PROMPT`, `WELCOME_MESSAGE_TEMPLATE`, `SUMMARY_REQUEST_PROMPT`,
  `IMAGE_ANALYSIS_REQUEST`, `PHOTO_TIPS`.
- `app.py` must not contain a long inline system prompt.
- PlantCare AI wording is its own product voice. Do not copy another project's
  branding, prompts, or variable names.

## 6. Streamlit rerun safety

Streamlit re-executes the script on every interaction. Therefore:

- Cache the Gemini client with `@st.cache_resource`; never rebuild it per rerun.
- Keep the Gemini `Chat` object in `st.session_state.chat`. Create a new chat
  only on onboarding or on "Analyze New Plant".
- Render the welcome message only when the conversation has no messages.
- Never send a user image to Gemini more than once for the same submission
  (camera captures must be cleared from widget state after processing).
- The WhatsApp button must not create a new chat; it sends a summary request
  through the existing conversation.

## 7. Code style

- PEP 8, small focused functions with single responsibilities.
- Beginner-friendly, readable, no dead code, no duplicated logic.
- Comments only where they add value (why, not what).
- Suggested helper names: `get_gemini_client`, `get_twilio_client`,
  `create_chat`, `ask_gemini`, `add_message`, `render_message`,
  `initialize_session_state`, `reset_plant_session`, `clean_whatsapp_text`,
  `send_whatsapp`, `normalize_phone_number`.
- Keep the Gemini model name in a single constant `MODEL_NAME`.

## 8. Required repository files

```
app.py
prompts.py
requirements.txt
README.md
.gitignore
AGENTS.md
.streamlit/secrets.toml.example
```

No other folders unless there is a concrete reason.

## 9. Verification before finishing

Run at least: `python -m compileall`, import checks, helper-function tests,
a missing-secrets check, a secret-pattern scan of tracked files, and
`streamlit run app.py` to confirm the app boots without a Python exception.