"""
YouTube → Discord new video notifier
Resolves @handle to channel ID automatically.
Designed to run as a persistent worker on Railway.
"""

import os
import re
import time
import xml.etree.ElementTree as ET

import requests
import schedule

# ── Config (set these as Railway environment variables) ───────────────────────

# Either a @handle (e.g. "@BadFriends") or a UC... channel ID
YOUTUBE_CHANNEL = os.environ.get("YOUTUBE_CHANNEL", "@BadFriends")

# Discord webhook URL
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# How often to check (in minutes)
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "10"))

# File to persist last seen video ID
STATE_FILE = "/tmp/last_video_id.txt"

# ── Channel ID resolution ─────────────────────────────────────────────────────

NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def resolve_channel_id(channel: str) -> str | None:
    """If given a @handle, scrape the channel page to find the UC... ID."""
    if channel.startswith("UC"):
        return channel  # Already a channel ID

    handle = channel.lstrip("@")
    url = f"https://www.youtube.com/@{handle}"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] Could not fetch channel page: {e}")
        return None

    match = re.search(r'"channelId"\s*:\s*"(UC[^"]+)"', resp.text)
    if match:
        return match.group(1)

    print("[ERROR] Could not find channel ID on page.")
    return None


# ── Feed fetching ─────────────────────────────────────────────────────────────

def fetch_latest_video(channel_id: str):
    """Return (video_id, title, url) for the most recent upload, or None on error."""
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        resp = requests.get(rss_url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] Could not fetch RSS feed: {e}")
        return None

    root = ET.fromstring(resp.content)
    entry = root.find("atom:entry", NS)
    if entry is None:
        print("[WARN] No videos found in feed.")
        return None

    video_id = entry.findtext("yt:videoId", namespaces=NS)
    title    = entry.findtext("atom:title", namespaces=NS)
    url      = f"https://www.youtube.com/watch?v={video_id}"
    return video_id, title, url


# ── State persistence ─────────────────────────────────────────────────────────

def load_last_id():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return f.read().strip()
    return None


def save_last_id(video_id):
    with open(STATE_FILE, "w") as f:
        f.write(video_id)


# ── Discord notification ──────────────────────────────────────────────────────

def send_discord_message(title, url):
    payload = {
        "embeds": [{
            "title": title,
            "url": url,
            "description": f"A new video was just uploaded!\n\n🔗 {url}",
            "color": 16711680,  # YouTube red
            "footer": {"text": "YouTube Notifier"}
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        print(f"[OK] Discord notified: {title}")
    except requests.RequestException as e:
        print(f"[ERROR] Discord webhook failed: {e}")


# ── Main check ────────────────────────────────────────────────────────────────

CHANNEL_ID = None

def check_for_new_video():
    global CHANNEL_ID
    if not CHANNEL_ID:
        print("[ERROR] No channel ID resolved yet, skipping check.")
        return

    result = fetch_latest_video(CHANNEL_ID)
    if result is None:
        return

    video_id, title, url = result
    last_id = load_last_id()

    if last_id is None:
        print(f"[INIT] First run. Saving latest video without notifying: {title}")
        save_last_id(video_id)
        return

    if video_id != last_id:
        print(f"[NEW] New video: {title} — {url}")
        send_discord_message(title, url)
        save_last_id(video_id)
    else:
        print(f"[OK] No new video. Latest: {title}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_WEBHOOK_URL:
        raise SystemExit("[ERROR] DISCORD_WEBHOOK_URL environment variable is not set.")

    print(f"[INIT] Resolving channel: {YOUTUBE_CHANNEL}")
    CHANNEL_ID = resolve_channel_id(YOUTUBE_CHANNEL)
    if not CHANNEL_ID:
        raise SystemExit("[ERROR] Could not resolve YouTube channel ID. Exiting.")
    print(f"[INIT] Channel ID: {CHANNEL_ID}")

    print(f"[INIT] Checking every {CHECK_INTERVAL_MINUTES} minute(s).")
    check_for_new_video()

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(check_for_new_video)
    while True:
        schedule.run_pending()
        time.sleep(30)
