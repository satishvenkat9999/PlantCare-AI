# PlantCare AI 🌱

**Snap your plant. Understand it. Care for it.**

PlantCare AI is a Streamlit app where you tell it your name and WhatsApp
number, send or upload a photo of your plant, chat about what you see, and then
have the conversation turned into a plant-care report that is delivered to your
own WhatsApp number.

Everything happens in one conversation: the same Gemini chat session analyses
the photo, answers your follow-up questions, and writes the summary that is sent
to WhatsApp. Nothing is stored on a server.

---

## What it does

1. **Onboarding** - your name and WhatsApp number (validated as an
   international number, e.g. `+919876543210`).
2. **Gemini chat** - one persistent conversation, created once and reused for
   every message.
3. **Photo or camera input** - upload an image through the chat box, or take a
   photo with your phone camera from the sidebar.
4. **Photo analysis** - the photo is validated, downscaled if it is large, and
   analysed with Gemini's image understanding.
5. **Follow-up questions** - suggested questions help you get started; after the
   first turn the app keeps its answers contextual to the same plant.
6. **WhatsApp report** - one button turns the conversation into a report and
   sends it to your number with Twilio's approved WhatsApp template.
7. **Analyze New Plant / Start over** - a fresh Gemini conversation, or a full
   reset.

PlantCare AI gives **image-based guidance, never a professional diagnosis.**
Its answers are careful to separate what is visible in the photo from what is
inferred and what simply cannot be determined from one picture.

---

## Requirements

| Requirement | Version |
| --- | --- |
| Python | 3.9 or newer |
| Streamlit | 1.48 or newer (2.x not yet supported) |
| `google-genai` | 2.x |
| `twilio` | 9.x |
| `pillow` | 10 or newer |

A Google AI Studio API key and a Twilio account with an approved WhatsApp
sender and an approved WhatsApp content template are both required.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create your secrets file

Copy the example file and fill in your own values:

```bash
# macOS / Linux
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

```powershell
# Windows PowerShell
Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml
```

`.streamlit/secrets.toml` is ignored by Git on purpose. Never commit it, never
paste real keys into `README.md`, and never share it.

| Secret | Where it comes from | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) | Gemini chat and image analysis |
| `TWILIO_ACCOUNT_SID` | Twilio Console | Twilio account |
| `TWILIO_AUTH_TOKEN` | Twilio Console | Twilio authentication |
| `TWILIO_WHATSAPP_FROM` | Twilio WhatsApp sender | The `whatsapp:+...` sender number |
| `TWILIO_CONTENT_SID` | Twilio approved content template | The WhatsApp template used for the report |

### 3. Run the app

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`.

Camera capture needs a secure context, so it only works on `localhost` or on an
`https://` deployment. On a desktop browser it may ask for camera permission the
first time. If you would rather not use the camera, the chat box accepts image
uploads instead.

---

## Deploying to Streamlit Community Cloud

1. Push the repository to GitHub.
2. In **App -> Settings -> Secrets**, add the five secrets listed above.
3. Deploy. Streamlit runs `streamlit run app.py` for you.

---

## How it is built

```
app.py                       Streamlit UI, session state, Gemini and Twilio calls
prompts.py                   every prompt and user-facing message
requirements.txt             pinned dependencies
.streamlit/secrets.toml.example
.gitignore
AGENTS.md
```

Two details worth knowing if you read the code:

- The Gemini client and the Twilio client are cached with `@st.cache_resource`,
  because Streamlit re-runs the whole script on every interaction. The Gemini
  `Chat` object lives in `st.session_state.chat` instead, so each browser
  session gets its own conversation and it is not shared between users.
- The report button asks Gemini for the summary through the **existing**
  conversation, so clicking it never starts a second chat.

---

## Privacy and safety

- Photos live only in your browser session and are sent straight to Gemini.
- PlantCare AI stores nothing: no database, no image folder, no user accounts.
- Answers are guidance, not a diagnosis. For anything serious, talk to a local
  nursery, garden centre, or plant specialist.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "PlantCare AI is not configured yet" | A required secret is missing. The message names the missing keys. |
| Report button stays disabled | Chat about your plant first, and check that the four Twilio secrets are set. |
| Report says it could not be sent | The template `TWILIO_CONTENT_SID` must be an **approved** WhatsApp template, and the destination must be verified for the sandbox. |
| Camera does not open | Use `localhost` or `https`, and allow camera permission. Otherwise upload a photo instead. |
| Gemini says it cannot read the photo | Re-take the photo in good light, closer, with the whole plant in frame. |

---

## License

Provided as-is for learning and personal use.