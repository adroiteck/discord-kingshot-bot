"""
Kingshot Guild Discord Bot v3.0
================================
A full-featured Discord bot for managing a Kingshot guild server.
Designed for leaders AND members to use.

Architecture:
  - bot.py       — Slim loader (this file)
  - utils.py     — Shared data I/O, helpers, views
  - cogs/        — Modular command groups
  - data/        — JSON data files + hero/formation configs

Cogs:
  events      — Event guides, cycle tracking, tips
  general     — Profiles, codes, timezones, leaderboard, power history
  war         — Rally, war schedule, signups, kill tracking, scouting
  stats       — Member stats, optimization, achievements, MVP
  moderation  — Kick, mute, clear, promote, demote, setup, announcements
  suggestions — Community crowdsourcing, troop reports
  tasks       — Background scheduled tasks, cleanup, bot health
  gameapi     — Kingshot game API: gift code redemption, player lookup

Usage:
  1. Set BOT_TOKEN env var (or bot_token in config.json)
  2. Run: python bot.py
  3. Use /setup in your server to create all channels and roles
"""

import discord
from discord.ext import commands
import logging
import os
from datetime import datetime, timezone

from utils import load_config, RolePanelView

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kingshot-bot")

# ---------------------------------------------------------------------------
# Config & Bot setup
# ---------------------------------------------------------------------------
config = load_config()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)

# ---------------------------------------------------------------------------
# Cog extensions to load
# ---------------------------------------------------------------------------
COG_EXTENSIONS = [
    "cogs.events",
    "cogs.general",
    "cogs.war",
    "cogs.stats",
    "cogs.moderation",
    "cogs.suggestions",
    "cogs.tasks",
    "cogs.gameapi",
    "cogs.translate",
]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    log.info(f"Connected to {len(bot.guilds)} guild(s)")

    # Register persistent views so buttons survive bot restarts
    bot.add_view(RolePanelView())

    # Load all cog extensions
    for ext in COG_EXTENSIONS:
        try:
            await bot.load_extension(ext)
            log.info(f"Loaded extension: {ext}")
        except Exception as e:
            log.error(f"Failed to load extension {ext}: {e}")

    # Start background tasks from cogs that have them
    for cog_name in ("Tasks", "GameAPI"):
        cog = bot.get_cog(cog_name)
        if cog and hasattr(cog, "start_tasks"):
            cog.start_tasks()
            log.info(f"{cog_name} background tasks started")

    # Sync slash commands to guild (no copy_global_to — prevents duplicates)
    try:
        guild_obj = discord.Object(id=int(config.get("guild_id", "0")))
        synced = await bot.tree.sync(guild=guild_obj)
        log.info(f"Synced {len(synced)} slash command(s) to guild {config.get('guild_id')}")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")


@bot.event
async def on_member_join(member: discord.Member):
    """Auto-assign recruit role and send welcome message."""
    guild = member.guild

    # Auto-assign Recruit role
    recruit_role = discord.utils.get(guild.roles, name="R1 | Bear Bait")
    if recruit_role:
        try:
            await member.add_roles(recruit_role)
        except discord.Forbidden:
            log.warning(f"Cannot assign role to {member} — missing permissions")

    # Send welcome message
    welcome_ch = discord.utils.get(guild.text_channels, name="welcome")
    if welcome_ch:
        embed = discord.Embed(
            title=f"Welcome to the guild, {member.display_name}! ⚔️",
            description=(
                f"Hey {member.mention}! Welcome to our Kingshot guild server.\n\n"
                "**Quick Start:**\n"
                "1️⃣ Read the rules in #rules\n"
                "2️⃣ Grab your roles in #roles\n"
                "3️⃣ Set your nickname to your **in-game name**\n"
                "4️⃣ Introduce yourself in #introductions\n"
                "5️⃣ Check out #bot-guide to see what I can do!\n\n"
                "Glad to have you — let's dominate! 🏰"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await welcome_ch.send(embed=embed)


# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """Handle slash command errors globally."""
    if isinstance(error, discord.app_commands.MissingPermissions):
        msg = "❌ You don't have permission to use this command."
    elif isinstance(error, discord.app_commands.MissingAnyRole):
        msg = "❌ You need a leadership role to use this command."
    elif isinstance(error, discord.app_commands.CommandOnCooldown):
        msg = f"⏳ Command on cooldown — try again in **{int(error.retry_after)}s**."
    elif isinstance(error, discord.app_commands.CommandNotFound):
        msg = "❌ Command not found."
    else:
        log.error(f"Slash command error in /{interaction.command.name if interaction.command else '?'}: {error}", exc_info=error)
        msg = "⚠️ Something went wrong. Please try again later."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception as e:
        log.warning(f"Could not send error message to user: {e}")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Handle text command errors globally."""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission.", ephemeral=True)
    elif isinstance(error, commands.MissingAnyRole):
        await ctx.send("❌ You need a leadership role.", ephemeral=True)
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Cooldown — try in **{int(error.retry_after)}s**.", ephemeral=True)
    elif isinstance(error, commands.CommandNotFound):
        pass  # Ignore unknown text commands silently
    else:
        log.error(f"Command error in {ctx.command}: {error}", exc_info=error)
        try:
            await ctx.send("⚠️ Something went wrong. Please try again later.", ephemeral=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or config.get("bot_token", "")
    if not token:
        log.critical("No bot token found. Set BOT_TOKEN env var or bot_token in config.json.")
        raise SystemExit(1)
    bot.run(token, log_handler=None)
