#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# YouTube Video Summariser — Setup Script
# ═══════════════════════════════════════════════════════════════
#
# This script installs all dependencies and configures the system.
# Run: chmod +x setup.sh && ./setup.sh
#
# Tested on: Ubuntu 22.04+, macOS 13+
# ═══════════════════════════════════════════════════════════════

set -e
echo "═══════════════════════════════════════════════════════════"
echo "  YouTube Video Summariser — Setup"
echo "═══════════════════════════════════════════════════════════"

# ─── System Dependencies ──────────────────────────────────────
echo ""
echo "▸ Checking system dependencies..."

# ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "  Installing ffmpeg..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt-get update && sudo apt-get install -y ffmpeg
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install ffmpeg
    fi
else
    echo "  ✓ ffmpeg found"
fi

# Node.js
if ! command -v node &> /dev/null; then
    echo "  ⚠  Node.js not found. Please install Node.js 18+."
    echo "     https://nodejs.org/"
else
    echo "  ✓ Node.js $(node --version) found"
fi

# ─── Python Dependencies ─────────────────────────────────────
echo ""
echo "▸ Installing Python packages..."
pip install --upgrade pip

pip install \
    yt-dlp \
    openai-whisper \
    anthropic \
    google-api-python-client \
    python-docx \
    schedule \
    pytz

# ─── Node.js Dependencies ────────────────────────────────────
echo ""
echo "▸ Installing Node.js packages..."
npm install -g docx

# ─── Environment Variables ────────────────────────────────────
echo ""
echo "▸ Checking environment variables..."

ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
# ─── YouTube Video Summariser — Environment Config ────────────
# Fill in your API keys below, then run:
#   source .env

# YouTube Data API v3 key (for channel scanning)
# Get one at: https://console.cloud.google.com/apis/credentials
export YOUTUBE_API_KEY=""

# Anthropic API key (for Claude summaries)
# Get one at: https://console.anthropic.com/settings/keys
export ANTHROPIC_API_KEY=""

# Gmail sender configuration
# Use an App Password: https://myaccount.google.com/apppasswords
export EMAIL_SENDER="your-email@gmail.com"
export GMAIL_APP_PASSWORD=""
export EMAIL_RECIPIENTS="recipient1@example.com,recipient2@example.com"

# Optional: OpenAI API key (if using Whisper API instead of local model)
# export OPENAI_API_KEY=""
ENVEOF
    echo "  Created .env file — please edit it with your API keys."
else
    echo "  ✓ .env file exists"
fi

# ─── Create output directories ───────────────────────────────
echo ""
echo "▸ Creating output directories..."
mkdir -p output/{transcripts,summaries,audio}
echo "  ✓ output/transcripts/"
echo "  ✓ output/summaries/"
echo "  ✓ output/audio/"

# ─── Done ─────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Setup complete!"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo "  1. Edit .env with your API keys"
echo "  2. Run: source .env"
echo "  3. Test with:  python yt_summariser.py --url 'https://www.youtube.com/watch?v=9ibLmF4EQ6E'"
echo "  4. Full scan:  python yt_summariser.py --scan"
echo "  5. Daemon:     python yt_summariser.py --daemon"
echo ""
echo "  Or set up a cron job for Monday 7 AM AEST (Sunday 21:00 UTC):"
echo "  crontab -e"
echo "  0 21 * * 0 cd $(pwd) && source .env && python yt_summariser.py --scan >> output/cron.log 2>&1"
echo ""
