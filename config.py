"""
Configuration for Indonesian YouTube Channel Monitor
=====================================================
YouTube channels to monitor, API keys, and scheduling config.
"""

# ─── YouTube Channels ──────────────────────────────────────────────
CHANNELS = [
    {
        "name": "Najwa Shihab",
        "handle": "@najwashihab",
        "channel_id": "UCo8h2TY_uBkAVUIc14m_KCA",
        "language": "id",  # Indonesian
        "description": "Indonesian journalist, host of Mata Najwa. News & Politics.",
    },
    {
        "name": "Akbar Faizal Uncensored",
        "handle": "@akbarfaizaluncensored",
        "channel_id": "UCpHsdx-LjZ2FC1xcdFzU6vg",
        "language": "id",
        "description": "Political podcast by Akbar Faizal. Uncensored interviews.",
    },
    {
        "name": "Bocor Alus Politik (Tempo)",
        "handle": "@Tempodotco",
        "channel_id": "UC3QRoNY-nYDTNSv-1dR0P-g",
        "language": "id",
        "description": "Political podcast by Tempo journalists. Hosted on the Tempodotco channel.",
        "title_filter": "Bocor Alus",  # Only process videos with this in the title
    },
]

# ─── API Keys (set via environment variables) ──────────────────────
# Export these in your shell or .env file:
#   export YOUTUBE_API_KEY="your-youtube-data-api-v3-key"
#   export ANTHROPIC_API_KEY="your-anthropic-api-key"
#   export GMAIL_APP_PASSWORD="your-gmail-app-password"

import os

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─── Email Config ──────────────────────────────────────────────────
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "your-email@gmail.com")
EMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENTS = os.getenv("EMAIL_RECIPIENTS", "recipient@example.com").split(",")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# ─── Schedule ──────────────────────────────────────────────────────
# Cron: Every Monday at 7:00 AM AEST (= Sunday 20:00 UTC)
SCHEDULE_CRON = "0 20 * * 0"  # UTC equivalent of Monday 7 AM AEST
SCHEDULE_TIMEZONE = "Australia/Sydney"
SCHEDULE_DAY = "monday"
SCHEDULE_TIME = "07:00"  # AEST

# ─── Paths ─────────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TRANSCRIPTS_DIR = os.path.join(OUTPUT_DIR, "transcripts")
SUMMARIES_DIR = os.path.join(OUTPUT_DIR, "summaries")
AUDIO_DIR = os.path.join(OUTPUT_DIR, "audio")

for d in [OUTPUT_DIR, TRANSCRIPTS_DIR, SUMMARIES_DIR, AUDIO_DIR]:
    os.makedirs(d, exist_ok=True)

# ─── Whisper Config ────────────────────────────────────────────────
WHISPER_MODEL = "large-v3"  # Best for Indonesian language
# Alternative: "medium" for faster processing, or use OpenAI Whisper API

# ─── Claude Config ─────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 8192
