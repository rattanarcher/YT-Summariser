# 🇮🇩 Indonesian YouTube Channel Monitor & Summariser

Automated pipeline that monitors Indonesian YouTube channels, transcribes video audio into timestamped text, generates AI-powered English summaries, and delivers a weekly email digest every Monday at 7:00 AM AEST.

## Monitored Channels

| Channel | Handle | Focus |
|---------|--------|-------|
| **Najwa Shihab** | @najwashihab | News & politics (Mata Najwa) |
| **Akbar Faizal Uncensored** | @akbarfaizaluncensored | Political podcast interviews |
| **Bocor Alus Politik** | @Tempodotco | Political podcast by Tempo journalists (filtered from Tempodotco channel) |

## Architecture

```
YouTube Channels
       │
       ▼
┌──────────────────┐
│  1. DISCOVER     │  YouTube Data API v3 or yt-dlp
│     New videos   │  Scans last 7 days per channel
│     from week    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  2. DOWNLOAD     │  yt-dlp
│     Audio track  │  Extracts audio as MP3
│     (MP3)        │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  3. TRANSCRIBE   │  OpenAI Whisper (large-v3)
│     Full text    │  Indonesian language model
│     + timestamps │  30-second timestamp blocks
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  4. SUMMARISE    │  Claude (Anthropic API)
│     English      │  Translates & summarises
│     + timestamps │  Structured format with timestamps
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  5. EMAIL        │  Gmail SMTP
│     Weekly       │  HTML digest + .docx attachments
│     digest       │  Monday 7:00 AM AEST
└──────────────────┘
```

## Quick Start

### 1. Install
```bash
chmod +x setup.sh
./setup.sh
```

### 2. Configure API Keys
Edit `.env` with your credentials:
```bash
export YOUTUBE_API_KEY="..."       # YouTube Data API v3
export ANTHROPIC_API_KEY="..."     # Anthropic (Claude)
export EMAIL_SENDER="..."          # Gmail address
export GMAIL_APP_PASSWORD="..."    # Gmail App Password
export EMAIL_RECIPIENTS="a@b.com,c@d.com"
```
Then: `source .env`

### 3. Test with a Single Video
```bash
python yt_summariser.py --url "https://www.youtube.com/watch?v=9ibLmF4EQ6E"
```

### 4. Run Weekly Scan
```bash
python yt_summariser.py --scan
```

### 5. Schedule with GitHub Actions (recommended)

The repo includes a GitHub Actions workflow that runs automatically every Monday at 7:00 AM AEST. No server needed.

**Step 1 — Add secrets to your GitHub repo:**

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Value |
|-------------|-------|
| `YOUTUBE_API_KEY` | Your YouTube Data API v3 key |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `OPENAI_API_KEY` | Your OpenAI API key (for Whisper transcription) |
| `EMAIL_SENDER` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | Your Gmail App Password |
| `EMAIL_RECIPIENTS` | Comma-separated list, e.g. `a@b.com,c@d.com` |

**Step 2 — Push to GitHub:**
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/yt-summariser.git
git push -u origin main
```

That's it. The workflow runs on schedule and also commits summaries back to the repo.

**Manual trigger:** Go to **Actions → Weekly YouTube Digest → Run workflow**. You can optionally paste a single video URL to process just that video.

**What happens each run:**
1. Checks out the repo on an Ubuntu runner
2. Installs Python 3.12, Node.js 20, ffmpeg
3. Scans all 3 channels for videos from the past 7 days
4. Downloads audio, transcribes via OpenAI Whisper API, summarises via Claude
5. Sends the HTML email digest with .docx transcript attachments
6. Uploads transcripts/summaries as downloadable GitHub Artifacts (retained 90 days)
7. Commits summary .md files back to the repo

**Why Whisper API instead of local Whisper?** GitHub Actions runners have 7 GB RAM and no GPU. The local `large-v3` model needs ~10 GB + GPU. The OpenAI Whisper API handles this at ~$0.006/minute of audio with no resource constraints.

### Alternative: Cron or daemon (self-hosted)

```bash
# Option A — Python daemon (stays running):
python yt_summariser.py --daemon

# Option B — Cron (Sunday 21:00 UTC = Monday 07:00 AEST):
crontab -e
0 21 * * 0 cd /path/to/yt_summariser && source .env && python yt_summariser.py --scan >> output/cron.log 2>&1
```

## Output Format

### Email Digest (HTML)
- Grouped by channel
- Each video: title, publish date, structured summary with timestamp references
- Timestamps link to approximate positions in the video
- Attached: full .docx transcripts for each video

### Summary Structure
Each video summary contains:
1. **Overview** — 2-3 sentence synopsis
2. **Key Participants** — Host and guests with context
3. **Key Points & Timestamps** — 5-12 major points with [MM:SS] references
4. **Notable Quotes** — 1-3 translated quotes
5. **Analysis & Implications** — Why it matters

### Transcript DOCX
- Full timestamped transcript in original Indonesian
- 30-second timestamp blocks
- Formatted Word document with metadata header

## File Structure
```
yt_summariser/
├── .github/
│   └── workflows/
│       └── weekly-digest.yml   # GitHub Actions schedule
├── yt_summariser.py            # Main pipeline (all 6 steps)
├── config.py                   # Channel list, API keys, paths
├── requirements.txt            # Python dependencies (for pip/CI)
├── setup.sh                    # Local installation (optional)
├── README.md                   # This file
├── .env                        # API keys — local only (git-ignored)
└── output/
    ├── transcripts/            # .docx full transcripts
    ├── summaries/              # .md summary files (committed by CI)
    ├── audio/                  # Downloaded .mp3 files (ephemeral)
    └── sample_weekly_email.html
```

## Requirements

- **Python 3.10+**
- **Node.js 18+** (for DOCX generation)
- **ffmpeg** (for audio processing)
- **~10 GB disk** (Whisper large-v3 model download)

### API Keys Needed
| Service | Purpose | Get it at |
|---------|---------|-----------|
| YouTube Data API v3 | Channel scanning | [Google Cloud Console](https://console.cloud.google.com/apis/credentials) |
| Anthropic API | Claude summaries | [Anthropic Console](https://console.anthropic.com/settings/keys) |
| Gmail App Password | Email delivery | [Google Account](https://myaccount.google.com/apppasswords) |

## Notes

- **Language**: Whisper `large-v3` provides the best accuracy for Indonesian (Bahasa Indonesia). The `medium` model is faster but less accurate for Indonesian.
- **Costs**: Anthropic API charges per token. Typical video summary costs ~$0.01-0.03. YouTube Data API has a free daily quota of 10,000 units.
- **No API fallback**: If `YOUTUBE_API_KEY` is not set, the system falls back to `yt-dlp` for video discovery (slower but no API key needed).
- **Whisper API alternative**: Set `OPENAI_API_KEY` to use OpenAI's hosted Whisper API instead of the local model (faster, no GPU needed, but costs ~$0.006/minute).
