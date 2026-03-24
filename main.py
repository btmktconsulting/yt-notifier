"""
YouTube → Discord new video notifier (multi-channel)
Checks multiple YouTube channels and sends notifications to separate Discord webhooks.
Designed to run as a GitHub Actions scheduled workflow.
"""

import json
import os
import re
import xml.etree.ElementTree as ET

import requests

# ── Config ────────────────────────────────────────────────────────────────────

# Each channel config: channel ID, discord webhook, and whether to include a summary
CHANNELS = [
    {
        "name": "Bad Friends",
        "channel_id": os.environ.get("BADFRIENDS_CHANNEL_ID", "UCRBpynZV0b7ww2XMCfC17qg"),
        "webhook": os.environ.get("BADFRIENDS_DISCORD_WEBHOOK", ""),
        "include_summary": False,
    },
    {
        "name": "Smeedia",
        "channel_id": os.environ.get("SMEEDIA_CHANNEL_ID", "UC6suAIZxy1WbCquJosUWtWQ"),
        "webhook": os.environ.get("SMEEDIA_DISCORD_WEBHOOK", ""),
        "include_summary": True,
    },
]

STATE_FILE = "last_video_ids.json"

NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


# ── Channel ID resolution ────────────────────────────────────────────────────

def resolve_channel_id(handle: str) -> str | None:
    """If given a @handle, scrape the channel page to find the UC... ID."""
    if handle.startswith("UC"):
        return handle

    clean = handle.lstrip("@")
    url = f"https://www.youtube.com/@{clean}"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] Could not fetch channel page for {handle}: {e}")
        return None

    match = re.search(r'"channelId"\s*:\s*"(UC[^"]+)"', resp.text)
    if match:
        return match.group(1)

    print(f"[ERROR] Could not find channel ID for {handle}")
    return None


# ── Feed fetching ─────────────────────────────────────────────────────────────

def fetch_latest_video(channel_id: str):
    """Return (video_id, title, url) for the most recent upload, or None."""
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
    title = entry.findtext("atom:title", namespaces=NS)
    url = f"https://www.youtube.com/watch?v={video_id}"
    return video_id, title, url


# ── Video summary ─────────────────────────────────────────────────────────────

def fetch_video_description(video_id: str) -> str | None:
    """Scrape the video description from the YouTube page."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
        })
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] Could not fetch video page: {e}")
        return None

    # Extract description from the page's JSON data
    match = re.search(r'"shortDescription"\s*:\s*"((?:[^"\\]|\\.)*)"', resp.text)
    if match:
        desc = match.group(1)
        # Unescape JSON string
        desc = desc.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        # Trim to first ~300 chars for a concise summary
        if len(desc) > 300:
            desc = desc[:300].rsplit(" ", 1)[0] + "..."
        print(f"[OK] Fetched summary for video {video_id}")
        return desc

    print(f"[WARN] Could not extract description for video {video_id}")
    return None


# ── State persistence ─────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Discord notification ─────────────────────────────────────────────────────

def send_discord_message(webhook_url: str, channel_name: str, title: str, url: str, summary: str | None = None):
    description = f"New video uploaded!\n\n🔗 {url}"
    if summary:
        description = f"New video uploaded!\n\n**Summary:**\n{summary}\n\n🔗 {url}"

    payload = {
        "embeds": [{
            "title": title,
            "url": url,
            "description": description,
            "color": 16711680,
            "footer": {"text": f"{channel_name} • YouTube Notifier"}
        }]
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        print(f"[OK] Discord notified for {channel_name}: {title}")
    except requests.RequestException as e:
        print(f"[ERROR] Discord webhook failed for {channel_name}: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    state = load_state()

    for channel in CHANNELS:
        name = channel["name"]
        webhook = channel["webhook"]

        if not webhook:
            print(f"[SKIP] No webhook configured for {name}")
            continue

        channel_id = channel["channel_id"]
        print(f"[CHECK] {name} (channel: {channel_id})")

        result = fetch_latest_video(channel_id)
        if result is None:
            continue

        video_id, title, url = result
        last_id = state.get(name)

        if last_id is None or video_id != last_id:
            print(f"[NEW] {name}: {title} — {url}")

            summary = None
            if channel["include_summary"]:
                summary = fetch_video_description(video_id)

            send_discord_message(webhook, name, title, url, summary)
            state[name] = video_id
        else:
            print(f"[OK] {name}: No new video. Latest: {title}")

    save_state(state)


if __name__ == "__main__":
    main()
