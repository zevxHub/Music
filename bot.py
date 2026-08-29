import os
import asyncio
import tempfile
import uuid
import threading
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
from shazamio import Shazam
from flask import Flask

# --- STEP 1: Flask Keep-Alive Server for Render Free Web Service ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is online!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Run Flask in a background thread so Render marks the service as Healthy
threading.Thread(target=run_flask, daemon=True).start()

# --- STEP 2: Bot Setup ---
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

# --- STEP 3: Core Audio Processing & Recognition Functions ---
async def download_audio_from_url(url: str) -> str:
    """Download audio from YouTube, TikTok, Spotify fallback, etc. using yt-dlp."""
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
        "default_search": "auto",
        # Pass realistic headers & extractor args for TikTok
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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

async def recognize_audio(file_path: str):
    """Pass audio track to Shazam engine."""
    try:
        result = await shazam.recognize(file_path)
        return result
    except Exception:
        return None

def build_embed(result: dict) -> discord.Embed:
    """Construct a formatted Discord embed for song results."""
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

# --- STEP 4: Music Recognition Command ---
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
        result = await recognize_audio(file_path)

        if result and "track" in result:
            embed = build_embed(result)
            await ctx.reply(embed=embed)
        else:
            await ctx.reply("❌ Sorry, I couldn't recognize that song.")

    except Exception as e:
        await ctx.reply(f"❌ An error occurred: {str(e)}")

    finally:
        # Always purge temporary local audio file
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"Deleted temp file: {file_path}")
            except Exception as cleanup_error:
                print(f"Failed to delete {file_path}: {cleanup_error}")

# --- STEP 5: Start Execution ---
if __name__ == "__main__":
    bot.run(TOKEN)
