#!/usr/bin/env python3
"""
YouTube Video Summariser
========================
Full pipeline: Discover → Download Audio → Transcribe → Summarise → Email

Monitors Indonesian YouTube channels (Najwa Shihab, Akbar Faizal Uncensored,
Prof. Rhenald Kasali) and produces weekly summaries with timestamped references.

Requirements:
    pip install yt-dlp openai-whisper anthropic google-api-python-client python-docx schedule
    # Also needs ffmpeg installed system-wide

Usage:
    # Single video (prototype):
    python yt_summariser.py --url "https://www.youtube.com/watch?v=9ibLmF4EQ6E"

    # Weekly scan of all channels:
    python yt_summariser.py --scan

    # Run as scheduled daemon (Monday 7 AM AEST):
    python yt_summariser.py --daemon
"""

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─── Configuration ─────────────────────────────────────────────────
from config import (
    CHANNELS,
    YOUTUBE_API_KEY,
    ANTHROPIC_API_KEY,
    WHISPER_MODEL,
    CLAUDE_MODEL,
    MAX_TOKENS,
    OUTPUT_DIR,
    TRANSCRIPTS_DIR,
    SUMMARIES_DIR,
    AUDIO_DIR,
)


# ═══════════════════════════════════════════════════════════════════
# STEP 1: Discover New Videos from Channels
# ═══════════════════════════════════════════════════════════════════

def get_recent_videos(channel_id: str, days: int = 7) -> list[dict]:
    """
    Fetch videos published in the last `days` from a YouTube channel
    using the YouTube Data API v3.
    """
    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("  ⚠  google-api-python-client not installed. Install with:")
        print("     pip install google-api-python-client")
        return []

    if not YOUTUBE_API_KEY:
        print("  ⚠  YOUTUBE_API_KEY not set. Skipping API discovery.")
        return []

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    request = youtube.search().list(
        part="snippet",
        channelId=channel_id,
        publishedAfter=published_after,
        order="date",
        type="video",
        maxResults=25,
    )
    response = request.execute()

    videos = []
    for item in response.get("items", []):
        videos.append({
            "video_id": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "published_at": item["snippet"]["publishedAt"],
            "url": f"https://www.youtube.com/watch?v={item['id']['videoId']}",
            "thumbnail": item["snippet"]["thumbnails"].get("high", {}).get("url", ""),
            "description": item["snippet"].get("description", ""),
        })

    return videos


def get_recent_videos_ytdlp(channel_handle: str, days: int = 7) -> list[dict]:
    """
    Fallback: Use yt-dlp to discover recent videos (no API key needed).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y%m%d")

    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--dateafter", cutoff_str,
        "--playlist-end", "25",
        f"https://www.youtube.com/{channel_handle}/videos",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            data = json.loads(line)
            videos.append({
                "video_id": data.get("id", ""),
                "title": data.get("title", ""),
                "published_at": data.get("upload_date", ""),
                "url": f"https://www.youtube.com/watch?v={data.get('id', '')}",
                "duration": data.get("duration", 0),
                "description": data.get("description", ""),
            })
        return videos
    except Exception as e:
        print(f"  ⚠  yt-dlp discovery failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════
# STEP 2: Download Audio from YouTube Video
# ═══════════════════════════════════════════════════════════════════

def download_audio(video_url: str, output_dir: str = AUDIO_DIR) -> str:
    """
    Download audio from a YouTube video using yt-dlp.
    Returns the path to the downloaded audio file.
    """
    video_id = extract_video_id(video_url)
    output_path = os.path.join(output_dir, f"{video_id}.mp3")

    if os.path.exists(output_path):
        print(f"  ✓  Audio already downloaded: {output_path}")
        return output_path

    cmd = [
        "yt-dlp",
        "-x",                          # Extract audio
        "--audio-format", "mp3",       # Convert to mp3
        "--audio-quality", "0",        # Best quality
        "-o", output_path,
        "--no-playlist",
        "--remote-components", "ejs:github",  # Enable JS challenge solver
    ]

    # Add cookies if available (needed for GitHub Actions / cloud runners)
    cookies_path = os.getenv("YT_DLP_COOKIES", "")
    if cookies_path and os.path.exists(cookies_path):
        cmd.extend(["--cookies", cookies_path])

    cmd.append(video_url)

    print(f"  ↓  Downloading audio from {video_url}...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr}")

    # yt-dlp may append extension - find the actual file
    for ext in [".mp3", ".mp3.mp3", ".m4a", ".webm", ".opus"]:
        candidate = output_path.replace(".mp3", "") + ext
        if os.path.exists(candidate):
            return candidate

    if os.path.exists(output_path):
        return output_path

    raise FileNotFoundError(f"Audio file not found after download: {output_path}")


def get_video_metadata(video_url: str) -> dict:
    """Get video title, channel, duration etc. via yt-dlp."""
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-download",
        "--no-playlist",
        "--remote-components", "ejs:github",
    ]

    cookies_path = os.getenv("YT_DLP_COOKIES", "")
    if cookies_path and os.path.exists(cookies_path):
        cmd.extend(["--cookies", cookies_path])

    cmd.append(video_url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode == 0:
        return json.loads(result.stdout)
    return {}


# ═══════════════════════════════════════════════════════════════════
# STEP 3: Transcribe Audio with Whisper
# ═══════════════════════════════════════════════════════════════════

def transcribe_audio(audio_path: str, language: str = "id") -> dict:
    """
    Transcribe audio using OpenAI Whisper (local model).
    Returns dict with 'text' (full text) and 'segments' (timestamped).

    For Indonesian content, use language="id" and model="large-v3".
    """
    try:
        import whisper
    except ImportError:
        print("  ⚠  openai-whisper not installed. Install with:")
        print("     pip install openai-whisper")
        return {"text": "", "segments": []}

    print(f"  🎙  Transcribing with Whisper ({WHISPER_MODEL})...")
    print(f"      Language: {language} | File: {audio_path}")

    model = whisper.load_model(WHISPER_MODEL)

    result = model.transcribe(
        audio_path,
        language=language,
        task="transcribe",     # Keep in original language
        verbose=False,
        word_timestamps=True,  # Fine-grained timestamps
    )

    return result


def transcribe_audio_api(audio_path: str, language: str = "id") -> dict:
    """
    Use OpenAI Whisper API for transcription.
    Automatically splits files larger than 24 MB into chunks.
    Requires OPENAI_API_KEY environment variable.
    """
    import openai

    client = openai.OpenAI()
    max_size = 24 * 1024 * 1024  # 24 MB (leave margin under 25 MB limit)
    file_size = os.path.getsize(audio_path)

    if file_size <= max_size:
        # Small enough — transcribe directly
        print(f"  🎙  Transcribing via Whisper API ({file_size / 1024 / 1024:.1f} MB)...")
        return _transcribe_single_file(client, audio_path, language)

    # File too large — split into chunks using ffmpeg
    print(f"  🎙  File is {file_size / 1024 / 1024:.1f} MB (over 24 MB limit). Splitting into chunks...")
    chunk_dir = os.path.join(os.path.dirname(audio_path), "chunks")
    os.makedirs(chunk_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    chunk_pattern = os.path.join(chunk_dir, f"{base_name}_chunk_%03d.mp3")

    # Split into 10-minute chunks
    split_cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-f", "segment",
        "-segment_time", "600",  # 10 minutes per chunk
        "-c", "copy",
        chunk_pattern,
    ]
    subprocess.run(split_cmd, capture_output=True, text=True, timeout=300)

    # Find all chunks and sort them
    import glob
    chunks = sorted(glob.glob(os.path.join(chunk_dir, f"{base_name}_chunk_*.mp3")))
    print(f"  📦  Split into {len(chunks)} chunks")

    # Transcribe each chunk and merge results
    all_segments = []
    all_text = []
    time_offset = 0.0

    for i, chunk_path in enumerate(chunks):
        print(f"  🎙  Transcribing chunk {i + 1}/{len(chunks)}...")
        chunk_result = _transcribe_single_file(client, chunk_path, language)

        all_text.append(chunk_result.get("text", ""))
        for seg in chunk_result.get("segments", []):
            all_segments.append({
                "start": seg["start"] + time_offset,
                "end": seg["end"] + time_offset,
                "text": seg["text"],
            })

        # Get duration of this chunk for offset calculation
        probe_cmd = [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            chunk_path,
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        try:
            time_offset += float(probe_result.stdout.strip())
        except ValueError:
            time_offset += 600.0  # fallback: assume 10 minutes

    # Clean up chunks
    for chunk_path in chunks:
        os.remove(chunk_path)

    return {
        "text": " ".join(all_text),
        "segments": all_segments,
    }


def _transcribe_single_file(client, audio_path: str, language: str) -> dict:
    """Transcribe a single audio file via OpenAI Whisper API."""
    with open(audio_path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language=language,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    return {
        "text": result.text,
        "segments": [
            {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
            }
            for seg in result.segments
        ],
    }


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS or MM:SS format."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_transcript_with_timestamps(transcription: dict) -> str:
    """
    Format the Whisper transcription into a readable timestamped transcript.
    Groups segments into ~30-second blocks for readability.
    """
    segments = transcription.get("segments", [])
    if not segments:
        return transcription.get("text", "")

    lines = []
    current_block_start = None
    current_block_texts = []
    block_duration = 30  # seconds per block

    for seg in segments:
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()

        if not text:
            continue

        if current_block_start is None:
            current_block_start = start

        if start - current_block_start >= block_duration and current_block_texts:
            ts = format_timestamp(current_block_start)
            block_text = " ".join(current_block_texts)
            lines.append(f"[{ts}] {block_text}")
            current_block_start = start
            current_block_texts = [text]
        else:
            current_block_texts.append(text)

    # Flush remaining
    if current_block_texts and current_block_start is not None:
        ts = format_timestamp(current_block_start)
        block_text = " ".join(current_block_texts)
        lines.append(f"[{ts}] {block_text}")

    return "\n\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# STEP 4: Generate AI Summary with Claude
# ═══════════════════════════════════════════════════════════════════

def generate_summary(transcript: str, video_title: str, channel_name: str) -> str:
    """
    Use Claude to generate a structured summary with timestamp references.
    """
    try:
        import anthropic
    except ImportError:
        print("  ⚠  anthropic package not installed. Install with:")
        print("     pip install anthropic")
        return ""

    if not ANTHROPIC_API_KEY:
        print("  ⚠  ANTHROPIC_API_KEY not set.")
        return ""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = textwrap.dedent("""
        You are an expert analyst summarising Indonesian YouTube video content
        for a professional audience monitoring Indonesian political and business discourse.

        Your task: Produce a clear, structured summary in ENGLISH of the video transcript.
        The transcript is in Indonesian (Bahasa Indonesia). Translate and summarise into English.

        FORMAT YOUR SUMMARY AS FOLLOWS:

        ## Overview
        A 2-3 sentence overview of the video's main topic and significance.

        ## Key Participants
        List the host and any guests, with brief context on who they are.

        ## Key Points & Timestamps
        - [MM:SS] Point 1: Description of the key argument or revelation
        - [MM:SS] Point 2: Next significant point
        - ... (cover all major points, typically 5-12 for a long-form video)

        ## Notable Quotes (translated)
        1-3 notable translated quotes with timestamps.

        ## Analysis & Implications
        2-3 sentences on why this matters for Indonesian politics/business/society.

        RULES:
        - Always include timestamps in [MM:SS] or [HH:MM:SS] format
        - Reference the closest timestamp from the transcript
        - Translate all Indonesian content into English
        - Keep the summary concise but comprehensive (300-600 words)
        - Maintain political neutrality — report what was said, not your opinion
        - Flag any significant claims, allegations, or policy positions
    """).strip()

    user_prompt = f"""Summarise this video:

**Video Title:** {video_title}
**Channel:** {channel_name}

**Timestamped Transcript:**
{transcript[:80000]}"""  # Truncate if extremely long

    print(f"  🤖  Generating AI summary with Claude...")

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text


# ═══════════════════════════════════════════════════════════════════
# STEP 5: Generate DOCX Transcript
# ═══════════════════════════════════════════════════════════════════

def create_transcript_docx(
    transcript_text: str,
    video_title: str,
    channel_name: str,
    video_url: str,
    output_path: str,
):
    """Create a formatted Word document of the full transcript."""
    # Use Node.js docx library via subprocess
    js_code = f"""
const {{ Document, Packer, Paragraph, TextRun, HeadingLevel,
         AlignmentType, BorderStyle }} = require('docx');
const fs = require('fs');

const transcript = fs.readFileSync('/tmp/transcript_content.txt', 'utf8');
const lines = transcript.split('\\n\\n');

const children = [
    new Paragraph({{
        heading: HeadingLevel.HEADING_1,
        children: [new TextRun({{ text: {json.dumps(video_title)}, bold: true, font: "Arial", size: 32 }})]
    }}),
    new Paragraph({{
        spacing: {{ after: 120 }},
        children: [
            new TextRun({{ text: "Channel: ", bold: true, font: "Arial", size: 22 }}),
            new TextRun({{ text: {json.dumps(channel_name)}, font: "Arial", size: 22 }}),
        ]
    }}),
    new Paragraph({{
        spacing: {{ after: 120 }},
        children: [
            new TextRun({{ text: "URL: ", bold: true, font: "Arial", size: 22 }}),
            new TextRun({{ text: {json.dumps(video_url)}, font: "Arial", size: 22, color: "2E75B6" }}),
        ]
    }}),
    new Paragraph({{
        spacing: {{ after: 120 }},
        children: [
            new TextRun({{ text: "Transcribed: ", bold: true, font: "Arial", size: 22 }}),
            new TextRun({{ text: new Date().toISOString().split('T')[0], font: "Arial", size: 22 }}),
        ]
    }}),
    new Paragraph({{
        spacing: {{ after: 200 }},
        border: {{ bottom: {{ style: BorderStyle.SINGLE, size: 6, color: "CCCCCC" }} }},
        children: []
    }}),
    new Paragraph({{
        heading: HeadingLevel.HEADING_2,
        spacing: {{ before: 200, after: 120 }},
        children: [new TextRun({{ text: "Full Transcript", bold: true, font: "Arial", size: 28 }})]
    }}),
];

for (const line of lines) {{
    if (!line.trim()) continue;
    const tsMatch = line.match(/^\\[(\\d{{2}}:\\d{{2}}(?::\\d{{2}})?)\\]\\s*(.*)/s);
    if (tsMatch) {{
        children.push(new Paragraph({{
            spacing: {{ before: 160, after: 80 }},
            children: [
                new TextRun({{ text: "[" + tsMatch[1] + "] ", bold: true, font: "Arial", size: 22, color: "2E75B6" }}),
                new TextRun({{ text: tsMatch[2], font: "Arial", size: 22 }}),
            ]
        }}));
    }} else {{
        children.push(new Paragraph({{
            spacing: {{ after: 80 }},
            children: [new TextRun({{ text: line, font: "Arial", size: 22 }})]
        }}));
    }}
}}

const doc = new Document({{
    styles: {{
        default: {{ document: {{ run: {{ font: "Arial", size: 22 }} }} }},
    }},
    sections: [{{
        properties: {{
            page: {{
                size: {{ width: 11906, height: 16838 }},
                margin: {{ top: 1440, right: 1440, bottom: 1440, left: 1440 }}
            }}
        }},
        children
    }}]
}});

Packer.toBuffer(doc).then(buffer => {{
    fs.writeFileSync({json.dumps(output_path)}, buffer);
    console.log("DOCX created: " + {json.dumps(output_path)});
}});
"""

    # Write transcript content to temp file (avoids shell escaping issues)
    with open("/tmp/transcript_content.txt", "w") as f:
        f.write(transcript_text)

    with open("/tmp/create_transcript.js", "w") as f:
        f.write(js_code)

    # Look for node_modules in the working directory (where npm install runs)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_node_modules = os.path.join(script_dir, "node_modules")

    node_env = {**os.environ}
    # Try local node_modules first, then fall back to global
    node_paths = []
    if os.path.isdir(local_node_modules):
        node_paths.append(local_node_modules)
    # Also check global
    global_result = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True)
    if global_result.returncode == 0 and global_result.stdout.strip():
        node_paths.append(global_result.stdout.strip())
    if node_paths:
        node_env["NODE_PATH"] = os.pathsep.join(node_paths)

    result = subprocess.run(
        ["node", "/tmp/create_transcript.js"],
        capture_output=True,
        text=True,
        env=node_env,
    )
    if result.returncode != 0:
        print(f"  ⚠  DOCX creation failed: {result.stderr}")
    return output_path


# ═══════════════════════════════════════════════════════════════════
# STEP 6: Send Email with Summary + Transcript Attachments
# ═══════════════════════════════════════════════════════════════════

def send_weekly_email(summaries: list[dict], transcript_paths: list[str]):
    """
    Send a formatted email with all video summaries in the body
    and full transcripts attached as .docx files.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    from config import EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENTS, SMTP_SERVER, SMTP_PORT

    if not EMAIL_PASSWORD:
        print("  ⚠  GMAIL_APP_PASSWORD not set. Skipping email.")
        return

    # Build email
    now = datetime.now()
    week_start = (now - timedelta(days=now.weekday() + 7)).strftime("%d %b")
    week_end = (now - timedelta(days=now.weekday() + 1)).strftime("%d %b %Y")
    subject = f"🇮🇩 Indonesian YouTube Weekly Digest — {week_start} – {week_end}"

    msg = MIMEMultipart("mixed")
    msg["From"] = EMAIL_SENDER
    msg["To"] = ", ".join(EMAIL_RECIPIENTS)
    msg["Subject"] = subject

    # ─── HTML Body ─────────────────────────────────────────────────
    html_parts = [
        """
        <html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                          color: #1a1a1a; max-width: 800px; margin: 0 auto; padding: 20px;">
        <div style="border-bottom: 3px solid #DC2626; padding-bottom: 16px; margin-bottom: 24px;">
            <h1 style="margin: 0; font-size: 24px; color: #111;">
                🇮🇩 Indonesian YouTube Weekly Digest
            </h1>
            <p style="margin: 4px 0 0; color: #666; font-size: 14px;">
        """,
        f"        Week of {week_start} – {week_end} | Generated {now.strftime('%A %d %B %Y, %I:%M %p AEST')}",
        """
            </p>
        </div>
        """,
    ]

    if not summaries:
        html_parts.append("<p><em>No new videos found from monitored channels this week.</em></p>")
    else:
        # Group by channel
        by_channel = {}
        for s in summaries:
            ch = s.get("channel", "Unknown")
            by_channel.setdefault(ch, []).append(s)

        for channel, vids in by_channel.items():
            html_parts.append(f"""
                <div style="margin-bottom: 32px;">
                    <h2 style="color: #DC2626; font-size: 18px; border-bottom: 1px solid #eee;
                               padding-bottom: 8px; margin-bottom: 16px;">
                        📺 {channel} ({len(vids)} video{'s' if len(vids) != 1 else ''})
                    </h2>
            """)

            for vid in vids:
                summary_html = vid.get("summary", "").replace("\n", "<br>")
                html_parts.append(f"""
                    <div style="background: #f9fafb; border-left: 4px solid #DC2626;
                                padding: 16px; margin-bottom: 20px; border-radius: 0 8px 8px 0;">
                        <h3 style="margin: 0 0 4px; font-size: 16px;">
                            <a href="{vid.get('url', '#')}" style="color: #111; text-decoration: none;">
                                {vid.get('title', 'Untitled')}
                            </a>
                        </h3>
                        <p style="margin: 0 0 12px; color: #888; font-size: 12px;">
                            Published: {vid.get('published_at', 'Unknown')}
                        </p>
                        <div style="font-size: 14px; line-height: 1.6;">
                            {summary_html}
                        </div>
                    </div>
                """)

            html_parts.append("</div>")

    html_parts.append("""
        <div style="border-top: 1px solid #eee; padding-top: 12px; margin-top: 24px;
                    color: #999; font-size: 12px;">
            <p>Full transcripts are attached as .docx files.<br>
            Generated by YouTube Video Summariser • Powered by Whisper + Claude</p>
        </div>
        </body></html>
    """)

    html_body = "\n".join(html_parts)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # ─── Attach Transcripts ───────────────────────────────────────
    for path in transcript_paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                attachment = MIMEApplication(f.read(), _subtype="docx")
                attachment.add_header(
                    "Content-Disposition", "attachment",
                    filename=os.path.basename(path),
                )
                msg.attach(attachment)

    # ─── Send ─────────────────────────────────────────────────────
    print(f"  ✉  Sending email to {', '.join(EMAIL_RECIPIENTS)}...")
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
    print(f"  ✓  Email sent successfully!")


# ═══════════════════════════════════════════════════════════════════
# Pipeline: Process a Single Video
# ═══════════════════════════════════════════════════════════════════

def process_video(video_url: str, channel_name: str = "", language: str = "id") -> dict:
    """
    Full pipeline for one video:
      1. Get metadata
      2. Download audio
      3. Transcribe
      4. Format transcript
      5. Generate summary
      6. Save transcript as DOCX
    Returns dict with summary, transcript path, etc.
    """
    video_id = extract_video_id(video_url)
    print(f"\n{'═' * 60}")
    print(f"  Processing: {video_url}")
    print(f"{'═' * 60}")

    # 1. Metadata
    print("\n  STEP 1: Fetching metadata...")
    meta = get_video_metadata(video_url)
    title = meta.get("title", f"Video {video_id}")
    channel = channel_name or meta.get("channel", "Unknown")
    published = meta.get("upload_date", "Unknown")

    print(f"  Title:   {title}")
    print(f"  Channel: {channel}")

    # 2. Download audio
    print("\n  STEP 2: Downloading audio...")
    audio_path = download_audio(video_url)
    print(f"  ✓  Audio saved: {audio_path}")

    # 3. Transcribe (use API on GitHub Actions / cloud, local model otherwise)
    print("\n  STEP 3: Transcribing audio...")
    if os.getenv("USE_WHISPER_API", "").lower() == "true":
        transcription = transcribe_audio_api(audio_path, language=language)
    else:
        transcription = transcribe_audio(audio_path, language=language)
    print(f"  ✓  Transcription complete ({len(transcription.get('segments', []))} segments)")

    # 4. Format transcript
    print("\n  STEP 4: Formatting transcript...")
    formatted_transcript = format_transcript_with_timestamps(transcription)

    # 5. Generate summary
    print("\n  STEP 5: Generating AI summary...")
    summary = generate_summary(formatted_transcript, title, channel)
    print(f"  ✓  Summary generated ({len(summary)} chars)")

    # 6. Save transcript DOCX
    print("\n  STEP 6: Creating transcript document...")
    safe_title = re.sub(r'[^\w\s-]', '', title)[:60].strip()
    docx_filename = f"Transcript_{safe_title}_{video_id}.docx"
    docx_path = os.path.join(TRANSCRIPTS_DIR, docx_filename)
    create_transcript_docx(formatted_transcript, title, channel, video_url, docx_path)
    print(f"  ✓  Transcript saved: {docx_path}")

    # Save summary as text too
    summary_path = os.path.join(SUMMARIES_DIR, f"Summary_{video_id}.md")
    with open(summary_path, "w") as f:
        f.write(f"# {title}\n**Channel:** {channel}\n**URL:** {video_url}\n\n{summary}")
    print(f"  ✓  Summary saved: {summary_path}")

    return {
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "url": video_url,
        "published_at": published,
        "summary": summary,
        "transcript_path": docx_path,
        "summary_path": summary_path,
    }


# ═══════════════════════════════════════════════════════════════════
# Weekly Scan: All Channels
# ═══════════════════════════════════════════════════════════════════

def weekly_scan():
    """
    Scan all configured channels for new videos from the past week,
    process each video, and send the weekly digest email.
    """
    print("\n" + "█" * 60)
    print("  WEEKLY SCAN — Indonesian YouTube Channel Monitor")
    print(f"  {datetime.now().strftime('%A %d %B %Y, %I:%M %p')}")
    print("█" * 60)

    all_summaries = []
    all_transcripts = []

    for channel in CHANNELS:
        print(f"\n{'─' * 60}")
        print(f"  📺 Scanning: {channel['name']}")
        print(f"     Handle:  {channel['handle']}")
        print(f"{'─' * 60}")

        # Try API first, fall back to yt-dlp
        videos = get_recent_videos(channel["channel_id"], days=7)
        if not videos:
            videos = get_recent_videos_ytdlp(channel["handle"], days=7)

        print(f"  Found {len(videos)} new video(s)")

        for video in videos:
            try:
                result = process_video(
                    video["url"],
                    channel_name=channel["name"],
                    language=channel.get("language", "id"),
                )
                all_summaries.append(result)
                if result.get("transcript_path"):
                    all_transcripts.append(result["transcript_path"])
            except Exception as e:
                print(f"  ⚠  Error processing {video.get('url')}: {e}")
                continue

    # Send email
    print(f"\n{'─' * 60}")
    print(f"  ✉  Preparing weekly digest email...")
    print(f"     {len(all_summaries)} summaries, {len(all_transcripts)} transcripts")
    print(f"{'─' * 60}")

    send_weekly_email(all_summaries, all_transcripts)

    print(f"\n{'█' * 60}")
    print(f"  ✓  Weekly scan complete!")
    print(f"{'█' * 60}\n")

    return all_summaries


def run_daemon():
    """Run as a scheduled daemon — executes weekly_scan every Monday 7 AM AEST."""
    try:
        import schedule
        import time
        import pytz
    except ImportError:
        print("Install schedule and pytz: pip install schedule pytz")
        sys.exit(1)

    aest = pytz.timezone("Australia/Sydney")

    def job():
        now_aest = datetime.now(aest)
        print(f"  ⏰  Scheduled run triggered at {now_aest.strftime('%Y-%m-%d %H:%M %Z')}")
        weekly_scan()

    # Schedule for every Monday at 07:00 AEST
    schedule.every().monday.at("07:00").do(job)

    print(f"  🕐  Daemon started. Scheduled: Every Monday at 07:00 AEST")
    print(f"      Next run: {schedule.next_run()}")
    print(f"      Press Ctrl+C to stop.\n")

    while True:
        schedule.run_pending()
        time.sleep(60)


# ═══════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════

def extract_video_id(url: str) -> str:
    """Extract the video ID from a YouTube URL."""
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return url  # Assume it's already an ID


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="YouTube Video Summariser for Indonesian Channels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python yt_summariser.py --url "https://www.youtube.com/watch?v=9ibLmF4EQ6E"
              python yt_summariser.py --scan
              python yt_summariser.py --daemon
        """),
    )
    parser.add_argument("--url", help="Process a single YouTube video URL")
    parser.add_argument("--scan", action="store_true", help="Scan all channels for new videos")
    parser.add_argument("--daemon", action="store_true", help="Run as scheduled daemon (Mon 7AM AEST)")
    parser.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")

    args = parser.parse_args()

    if args.url:
        result = process_video(args.url)
        print(f"\n  Summary:\n{'─' * 40}\n{result['summary']}")
        # Send email if email credentials are configured
        from config import EMAIL_PASSWORD
        if EMAIL_PASSWORD:
            try:
                print(f"\n  Sending email with results...")
                transcript_paths = []
                tp = result.get("transcript_path", "")
                if tp and os.path.exists(tp):
                    transcript_paths.append(tp)
                send_weekly_email([result], transcript_paths)
            except Exception as e:
                print(f"\n  ⚠  Email failed: {e}")
        else:
            print(f"\n  ⚠  GMAIL_APP_PASSWORD not set — skipping email.")
    elif args.scan:
        weekly_scan()
    elif args.daemon:
        run_daemon()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
