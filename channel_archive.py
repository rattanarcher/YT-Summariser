#!/usr/bin/env python3
"""
Channel Archiver
================
Bulk-process an ENTIRE YouTube channel's long-form videos into documents.
One DOCX per video (summary + Indonesian transcript), committed to the repo.

Designed to stay within Gemini's free tier by processing a capped number of
videos per run and resuming where it left off. Run daily via GitHub Actions
(or manually) until the whole channel is archived.

Key behaviour:
  - Lists ALL videos on the channel (not just the last 7 days)
  - Filters to long-form videos (>= MIN_DURATION)
  - Keeps a progress ledger (archive_progress.json) of completed video IDs
  - Each run processes up to DAILY_VIDEO_CAP new videos, then stops
  - Writes one DOCX per video into archive/<ChannelName>/
  - Skips videos already done, so re-running resumes automatically

Usage:
    python channel_archive.py --channel "@najwashihab"
    python channel_archive.py --channel "https://www.youtube.com/@najwashihab"
    python channel_archive.py --channel "@najwashihab" --cap 50
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

# Reuse the existing pipeline functions
from yt_summariser import (
    download_audio,
    get_video_metadata,
    transcribe_audio,
    format_transcript_with_timestamps,
    generate_summary,
    create_transcript_docx,
    extract_video_id,
)

# ─── Configuration ─────────────────────────────────────────────────
MIN_DURATION = 600          # Only archive videos >= 10 minutes
DAILY_VIDEO_CAP = 100       # Max videos to process per run (free-tier safe)
                            # Each video = 2 Gemini requests (transcribe + summarise)
                            # 100 videos = 200 requests. Flash-Lite free tier ~1,000 RPD.
PACING_SECONDS = 5          # Wait between videos to respect RPM limits

ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive")
PROGRESS_FILE = os.path.join(ARCHIVE_DIR, "archive_progress.json")


# ═══════════════════════════════════════════════════════════════════
# Channel video listing (ALL videos, not date-limited)
# ═══════════════════════════════════════════════════════════════════

def list_all_channel_videos(channel: str) -> list[dict]:
    """
    List every video on a channel using yt-dlp (flat playlist = fast, IDs only).
    Returns a list of {video_id, title, url}. Duration is fetched later per video.
    """
    # Normalise channel input into a /videos URL
    if channel.startswith("http"):
        base = channel.rstrip("/")
        if not base.endswith("/videos"):
            base = base + "/videos"
        channel_url = base
    else:
        handle = channel if channel.startswith("@") else "@" + channel
        channel_url = f"https://www.youtube.com/{handle}/videos"

    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--remote-components", "ejs:github",
    ]
    cookies_path = os.getenv("YT_DLP_COOKIES", "")
    if cookies_path and os.path.exists(cookies_path):
        cmd.extend(["--cookies", cookies_path])
    cmd.append(channel_url)

    print(f"  📋  Listing all videos from {channel_url} ...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = data.get("id", "")
        if not vid:
            continue
        videos.append({
            "video_id": vid,
            "title": data.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={vid}",
            # Flat playlist sometimes includes duration; may be None
            "duration": data.get("duration", None),
        })
    print(f"  ✓  Found {len(videos)} videos on the channel (before duration filter)")
    return videos


# ═══════════════════════════════════════════════════════════════════
# Progress ledger
# ═══════════════════════════════════════════════════════════════════

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"done": {}, "failed": {}, "channel": "", "started": ""}


def save_progress(progress: dict):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
# Process one video into the archive
# ═══════════════════════════════════════════════════════════════════

def archive_video(video: dict, channel_name: str, out_dir: str) -> dict:
    """
    Download, transcribe, summarise, and write one DOCX for a single video.
    Returns a status dict.
    """
    video_url = video["url"]
    video_id = video["video_id"]

    print(f"\n{'═' * 60}")
    print(f"  Archiving: {video.get('title', video_id)[:60]}")
    print(f"  {video_url}")
    print(f"{'═' * 60}")

    # Metadata + duration check
    meta = get_video_metadata(video_url)
    title = meta.get("title", video.get("title", f"Video {video_id}"))
    duration = meta.get("duration", 0)
    published = meta.get("upload_date", "")

    print(f"  Duration: {duration // 60}m {duration % 60}s" if duration else "  Duration: unknown")
    if duration and duration < MIN_DURATION:
        print(f"  ⏭  Skipping: under {MIN_DURATION // 60} minutes")
        return {"status": "skipped_short", "video_id": video_id}

    # Download audio
    print("  ↓  Downloading audio...")
    audio_path = download_audio(video_url)

    # Transcribe (Gemini)
    print("  🎙  Transcribing...")
    transcription = transcribe_audio(audio_path, language="id")
    formatted = format_transcript_with_timestamps(transcription)
    if not formatted.strip():
        print("  ⚠  Empty transcript, marking failed")
        return {"status": "failed", "video_id": video_id, "reason": "empty transcript"}

    # Summarise (Gemini)
    print("  🤖  Summarising...")
    summary_result = generate_summary(formatted, title, channel_name)
    email_summary = summary_result.get("email_summary", "")
    detailed_analysis = summary_result.get("detailed_analysis", "")

    # Write DOCX (date-prefixed so the folder sorts chronologically)
    date_prefix = published if published else "00000000"
    safe_title = re.sub(r'[^\w\s-]', '', title)[:60].strip()
    docx_name = f"{date_prefix}_{safe_title}_{video_id}.docx"
    docx_path = os.path.join(out_dir, docx_name)

    # Prepend the email summary into the DOCX so each doc is self-contained
    combined_analysis = ""
    if email_summary:
        combined_analysis += "# Summary\n\n" + email_summary + "\n\n"
    if detailed_analysis:
        combined_analysis += detailed_analysis

    create_transcript_docx(formatted, title, channel_name, video_url, docx_path, combined_analysis)
    print(f"  ✓  Saved: {docx_name}")

    # Clean up audio to save space
    try:
        os.remove(audio_path)
    except OSError:
        pass

    return {
        "status": "done",
        "video_id": video_id,
        "title": title,
        "docx": os.path.relpath(docx_path, ARCHIVE_DIR),
        "published": published,
    }


# ═══════════════════════════════════════════════════════════════════
# Main archive run
# ═══════════════════════════════════════════════════════════════════

def run_archive(channel: str, cap: int):
    import time as _time

    print("\n" + "█" * 60)
    print("  CHANNEL ARCHIVER")
    print(f"  Channel: {channel}")
    print(f"  Daily cap: {cap} videos ({cap * 2} Gemini requests)")
    print(f"  {datetime.now().strftime('%A %d %B %Y, %I:%M %p')}")
    print("█" * 60)

    # Derive a clean channel name for the output folder
    channel_name = channel.replace("https://www.youtube.com/", "").replace("@", "").strip("/")
    channel_name = channel_name.replace("/videos", "")
    out_dir = os.path.join(ARCHIVE_DIR, channel_name)
    os.makedirs(out_dir, exist_ok=True)

    progress = load_progress()
    if not progress.get("channel"):
        progress["channel"] = channel
        progress["started"] = datetime.now().isoformat()

    done_ids = set(progress.get("done", {}).keys())
    failed_ids = set(progress.get("failed", {}).keys())

    # List all videos
    all_videos = list_all_channel_videos(channel)

    # Determine which are still to do (not done; failed ones get one retry)
    todo = [v for v in all_videos if v["video_id"] not in done_ids]
    print(f"\n  Total videos: {len(all_videos)}")
    print(f"  Already done: {len(done_ids)}")
    print(f"  Remaining:    {len(todo)}")

    if not todo:
        print("\n  🎉  Channel fully archived. Nothing left to do.")
        save_progress(progress)
        return

    processed = 0
    for video in todo:
        if processed >= cap:
            print(f"\n  ⏸  Hit daily cap of {cap}. Will resume on next run.")
            break

        try:
            result = archive_video(video, channel_name, out_dir)
            status = result.get("status")

            if status == "done":
                progress["done"][video["video_id"]] = {
                    "title": result.get("title", ""),
                    "docx": result.get("docx", ""),
                    "published": result.get("published", ""),
                    "archived_at": datetime.now().isoformat(),
                }
                progress.get("failed", {}).pop(video["video_id"], None)
                processed += 1
            elif status == "skipped_short":
                # Record short videos as done so we don't re-check them
                progress["done"][video["video_id"]] = {
                    "title": video.get("title", ""),
                    "skipped": "too short",
                    "archived_at": datetime.now().isoformat(),
                }
            else:
                progress.setdefault("failed", {})[video["video_id"]] = {
                    "title": video.get("title", ""),
                    "reason": result.get("reason", "unknown"),
                    "failed_at": datetime.now().isoformat(),
                }

            # Save progress after EVERY video so a crash never loses work
            save_progress(progress)

        except Exception as e:
            print(f"  ⚠  Error: {e}")
            progress.setdefault("failed", {})[video["video_id"]] = {
                "title": video.get("title", ""),
                "reason": str(e)[:200],
                "failed_at": datetime.now().isoformat(),
            }
            save_progress(progress)
            # If we hit a quota error, stop the run gracefully
            if any(code in str(e) for code in ["RESOURCE_EXHAUSTED", "429", "quota"]):
                print("  🛑  Quota limit reached. Stopping. Resume on next run.")
                break

        # Pace between videos to respect RPM limits
        if processed < cap:
            _time.sleep(PACING_SECONDS)

    # Final summary
    remaining = len([v for v in all_videos if v["video_id"] not in set(progress["done"].keys())])
    print(f"\n{'█' * 60}")
    print(f"  Run complete. Processed {processed} videos this run.")
    print(f"  Total archived: {len(progress['done'])} | Remaining: {remaining}")
    print(f"{'█' * 60}\n")
    save_progress(progress)


def main():
    parser = argparse.ArgumentParser(description="Archive an entire YouTube channel into documents")
    parser.add_argument("--channel", required=True, help="Channel handle or URL (e.g. @najwashihab)")
    parser.add_argument("--cap", type=int, default=DAILY_VIDEO_CAP,
                        help=f"Max videos to process this run (default {DAILY_VIDEO_CAP})")
    args = parser.parse_args()
    run_archive(args.channel, args.cap)


if __name__ == "__main__":
    main()
