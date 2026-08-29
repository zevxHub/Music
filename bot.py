import os
import asyncio
import tempfile
import uuid
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
from shazamio import Shazam

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

async def download_audio_from_url(url: str) -> str:
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
    try:
        result = await shazam.recognize(file_path)
        return result
    except Exception:
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
            await ctx.reply("🎵 Downloading audio from URL...")
            file_path = await download_audio_from_url(source_url)
        elif attachment:
            await ctx.reply("🎵 Downloading attached file...")
            temp_dir = tempfile.gettempdir()
            original_name = attachment.filename
            suffix = Path(original_name).suffix or ".mp3"
            filename = f"temp_{uuid.uuid4()}{suffix}"
            file_path = os.path.join(temp_dir, filename)
            await attachment.save(file_path)

        await ctx.reply("🔍 Recognizing song...")
        result = await recognize_audio(file_path)

        if result and "track" in result:
            embed = build_embed(result)
            await ctx.reply(embed=embed)
        else:
            await ctx.reply("❌ Sorry, I couldn't recognize that song.")

    except Exception as e:
        await ctx.reply(f"❌ An error occurred: {str(e)}")

    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as cleanup_error:
                print(f"Failed to delete {file_path}: {cleanup_error}")

if __name__ == "__main__":
    bot.run(TOKEN)
