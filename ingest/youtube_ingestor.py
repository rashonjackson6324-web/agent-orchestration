import os
import json
import datetime
import subprocess
import requests
from pathlib import Path

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
CHANNELS_FILE = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/intel/channels.txt")
OUTPUT_DIR = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/intel")
TODAY = datetime.datetime.now().strftime("%Y-%m-%d")

def tg(m):
    try:
        requests.post(
            "https://api.telegram.org/bot" + TOKEN + "/sendMessage",
            json={"chat_id": CHAT, "text": m},
            timeout=10
        )
    except:
        pass

def read_channels():
    if not Path(CHANNELS_FILE).exists():
        return []
    lines = Path(CHANNELS_FILE).read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]

def install_deps():
    subprocess.run(
        ["pip", "install", "yt-dlp", "youtube-transcript-api", "--break-system-packages", "-q"],
        capture_output=True
    )

def search_channel_videos(channel_name, max_results=2):
    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "extract_flat": True,
            "playlist_items": f"1:{max_results}",
        }
        query = f"ytsearch{max_results}:{channel_name} trading strategies"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(query, download=False)
            if result and "entries" in result:
                return result["entries"]
    except Exception as e:
        print(f"  Search failed for {channel_name}: {e}")
    return []

def get_transcript(video_id):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        text = " ".join([t["text"] for t in transcript])
        return text[:3000]
    except Exception as e:
        return f"[transcript unavailable: {e}]"

def summarize_with_claude(channel, title, transcript):
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            f"Channel: {channel}\nVideo: {title}\n\nTranscript excerpt:\n{transcript}\n\n"
            f"In 3-4 sentences: What is the key trading insight or strategy explained? "
            f"What angle did they NOT cover that would make good content?"
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"[summary unavailable: {e}]"

def run_harvest():
    install_deps()
    channels = read_channels()
    if not channels:
        tg("YouTube Harvest: no channels in channels.txt")
        return

    print(f"Harvesting {len(channels)} channels...")
    tg(f"YouTube Harvest starting - {len(channels)} channels")

    report_lines = [
        f"# YouTube Intel Harvest - {TODAY}",
        f"Channels: {len(channels)}",
        ""
    ]

    total_videos = 0
    for channel in channels:
        print(f"\n  Channel: {channel}")
        report_lines.append(f"## {channel}")
        videos = search_channel_videos(channel)
        if not videos:
            report_lines.append("No videos found.\n")
            continue
        for video in videos:
            vid_id = video.get("id", "")
            title = video.get("title", "Unknown")
            url = f"https://youtube.com/watch?v={vid_id}"
            print(f"    Video: {title}")
            transcript = get_transcript(vid_id)
            summary = summarize_with_claude(channel, title, transcript)
            report_lines.append(f"### {title}")
            report_lines.append(f"URL: {url}")
            report_lines.append(f"**Summary:** {summary}")
            report_lines.append("")
            total_videos += 1

    output_path = Path(OUTPUT_DIR) / f"youtube-{TODAY}.md"
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(report_lines))
    print(f"\nReport saved: {output_path}")
    tg(
        f"YouTube Harvest complete\n"
        f"Channels: {len(channels)} | Videos: {total_videos}\n"
        f"Report: ~/.agent-network/intel/youtube-{TODAY}.md"
    )

if __name__ == "__main__":
    run_harvest()
