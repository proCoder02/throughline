"# Throughline

Throughline is a Flask-based voice-to-action workspace that captures live speech, transcribes it, extracts actionable tasks and personality notes, and helps the user follow up with reminders. The application combines a browser-based front end, real-time speech streaming, AI summarization, local persistence, and background reminder processing.

## What the app does

- Records or accepts speech input from the browser
- Sends audio to Deepgram for transcription
- Sends transcript/context to an Ollama-backed LLM for analysis
- Stores conversations, tasks, and personality notes in SQLite
- Exposes task management endpoints for completing, reopening, and marking reminders
- Sends reminder emails with an attached .ics calendar file

## Architecture overview

The project is organized around a single Flask application:

- Front end: served from the templates directory, with browser interactions handled by Flask routes and WebSocket streaming
- Backend: app.py contains the Flask app, database helpers, authentication routes, speech processing endpoints, LLM integration, and reminder worker logic
- Data layer: SQLite database file (conversations.db) with tables for users, conversations, tasks, and personality notes
- External services:
  - Deepgram for speech-to-text transcription
  - Ollama for LLM-based conversation/task extraction
  - SMTP/Gmail for reminder emails
- Background jobs: a daemon thread monitors tasks and sends reminder emails when due

## Project structure

- app.py: main Flask application and business logic
- templates/index.html: web UI
- requirements.txt: Python dependencies
- migrate_add_users.py: database migration helper
- .env: local environment variables (API keys, SMTP settings)
- conversations.db: local SQLite database created at runtime

## Prerequisites

- Python 3.10+ recommended
- A Deepgram API key
- An Ollama API key or access to an Ollama-compatible endpoint
- Optional: SMTP credentials for reminder emails

## Installation

1. Open a terminal in the project folder.
2. Create and activate a virtual environment:

   Windows:
   ```bash
   py -3 -m venv .venv
   .venv\Scripts\activate
   ```

   macOS/Linux:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Environment configuration

Create or update a .env file in the project root with values similar to the following:

```env
FLASK_SECRET_KEY=change-me
DEEPGRAM_API_KEY=your_deepgram_key
olama_api_key=your_ollama_key
OLLAMA_MODEL=gpt-oss:120b
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password
USE_HTTPS=false
```

Notes:
- For Gmail, use an app password rather than a normal account password if SMTP authentication is required.
- The app reads these values at startup through python-dotenv.

## Running the app

Start the Flask application:

```bash
python app.py
```

Then open:

```text
http://localhost:5000
```

The app will create the SQLite database automatically on first run.

## How the runtime works

1. The browser loads the main page from the templates folder.
2. Audio is streamed to Deepgram for transcription.
3. The transcript and user prompt are sent to the LLM for analysis.
4. Extracted tasks, notes, and summaries are saved into SQLite.
5. A background reminder worker watches due tasks and sends email reminders when appropriate.

## Troubleshooting

- If the app cannot start, verify that the virtual environment is active and requirements were installed successfully.
- If transcription fails, confirm that DEEPGRAM_API_KEY is valid.
- If analysis fails, confirm that the Ollama key/model configuration is correct.
- If reminder emails fail, verify SMTP credentials and app-password settings.

## License

This project is currently intended for local development and experimentation.
