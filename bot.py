import os
import asyncio
import tempfile
import uuid
import threading
import subprocess
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
from shazamio import Shazam
from flask import Flask

# --- Flask Keep-Alive for Render ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is online!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# --- Bot Setup ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in environment variables")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

shazam = Shazam()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

# --- Core Audio Processing ---

async def download_audio_from_url(url: str) -> str:
    """
    Download the full audio from any media URL using yt-dlp.
    Returns the path to the downloaded MP3 file.
    """
    temp_dir = tempfile.gettempdir()
    file_id = f"temp_{uuid.uuid4()}"
    outtmpl_path = os.path.join(temp_dir, file_id)
    final_output_path = f"{outtmpl_path}.mp3"

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{outtmpl_path}.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "default_search": "ytsearch",          # Fallback for Spotify/Apple Music
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.tiktok.com/",
        },
        "extractor_args": {
            "tiktok": {
                "app_version": "20.2.1",
                "manifest_app_version": "2021",
            }
        },
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        # No trimming here – we'll do it later for multiple attempts
    }

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    try:
        await asyncio.to_thread(_download)
    except Exception as e:
        if os.path.exists(final_output_path):
            os.remove(final_output_path)
        raise RuntimeError(f"yt-dlp failed: {e}") from e

    if not os.path.exists(final_output_path):
        raise RuntimeError("yt-dlp did not produce an audio file")
    return final_output_path

async def trim_audio(input_path: str, start_sec: int, duration_sec: int, output_path: str) -> bool:
    """
    Use ffmpeg to extract a segment from input_path and save to output_path.
    Returns True on success.
    """
    try:
        cmd = [
            "ffmpeg",
            "-y",                     # overwrite output
            "-i", input_path,
            "-ss", str(start_sec),
            "-t", str(duration_sec),
            "-acodec", "copy",        # keep quality
            output_path
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        await process.communicate()
        return process.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False

async def recognize_segment(file_path: str, start_sec: int, duration_sec: int) -> dict:
    """
    Trim the given audio file to a segment, recognize it with Shazam.
    Returns the result dict if recognised, else None.
    The trimmed file is deleted after recognition.
    """
    temp_dir = tempfile.gettempdir()
    segment_path = os.path.join(temp_dir, f"segment_{uuid.uuid4()}.mp3")
    try:
        success = await trim_audio(file_path, start_sec, duration_sec, segment_path)
        if not success:
            return None
        # Recognize
        result = await shazam.recognize(segment_path)
        return result
    except Exception:
        return None
    finally:
        if os.path.exists(segment_path):
            os.remove(segment_path)

async def recognize_audio_with_retry(file_path: str) -> dict:
    """
    Attempt to recognise the audio by testing several segments.
    Returns the first successful result, or None if all fail.
    """
    # Try a few different segments (start, duration) in seconds
    attempts = [
        (0, 15),     # beginning
        (15, 15),    # a bit later
        (30, 15),    # further in
    ]
    for start, duration in attempts:
        result = await recognize_segment(file_path, start, duration)
        if result and "track" in result:
            return result
    return None

def build_embed(result: dict) -> discord.Embed:
    track = result.get("track", {})
    title = track.get("title", "Unknown Title")
    artist = track.get("subtitle", "Unknown Artist")
    cover_url = track.get("images", {}).get("coverart")
    shazam_url = track.get("url")

    embed = discord.Embed(
        title=f"🎵 {title}",
        description=f"**Artist:** {artist}",
        color=discord.Color.green(),
        url=shazam_url,
    )
    if cover_url:
        embed.set_thumbnail(url=cover_url)
    return embed

# --- Command ---

@bot.command(name="whatsong", aliases=["recognize"])
async def whatsong(ctx: commands.Context, *, url: str = None):
    source_url = url
    attachment = None

    if not source_url and ctx.message.attachments:
        attachment = ctx.message.attachments[0]

    if not source_url and not attachment:
        await ctx.reply("Please provide a media URL or attach an audio/video file.")
        return

    file_path = None

    try:
        if source_url:
            await ctx.reply("🎵 Extracting audio from URL...")
            file_path = await download_audio_from_url(source_url)
        elif attachment:
            await ctx.reply("🎵 Downloading attached file...")
            temp_dir = tempfile.gettempdir()
            original_name = attachment.filename
            suffix = Path(original_name).suffix or ".mp3"
            filename = f"temp_{uuid.uuid4()}{suffix}"
            file_path = os.path.join(temp_dir, filename)
            await attachment.save(file_path)

        await ctx.reply("🔍 Analyzing song signature with Shazam...")
        result = await recognize_audio_with_retry(file_path)

        if result and "track" in result:
            embed = build_embed(result)
            await ctx.reply(embed=embed)
        else:
            await ctx.reply("❌ Sorry, I couldn't recognize that song.")

    except Exception as e:
        await ctx.reply(f"❌ An error occurred: {str(e)}")

    finally:
        # Clean up the main downloaded file
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"Deleted temp file: {file_path}")
            except Exception as cleanup_error:
                print(f"Failed to delete {file_path}: {cleanup_error}")

# --- Start ---

if __name__ == "__main__":
    bot.run(TOKEN)
