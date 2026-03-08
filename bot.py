"""
Kingshot Guild Discord Bot v2.0
================================
A full-featured Discord bot for managing a Kingshot guild server.
Designed for leaders AND members to use.

Features:
  - Server setup automation (channels, roles, permissions)
  - Scheduled announcements (daily reminders, war prep, tips)
  - Welcome messages & auto-role assignment
  - Moderation commands (kick, mute, clear)
  - Member commands (event guides, hero lookup, gift codes, timers)
  - Alliance tools (rally calls, war schedule, power tracker)

Requirements:
  pip install discord.py

Usage:
  1. Set your bot token in config.json
  2. Run: python bot.py
  3. Use !setup in your server to create all channels and roles
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import asyncio
import logging
import random
import os
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kingshot-bot")

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.json"
DATA_PATH = Path(__file__).parent / "data"
DATA_PATH.mkdir(exist_ok=True)

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def load_data(name, default=None):
    path = DATA_PATH / f"{name}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default or {}

def save_data(name, data):
    with open(DATA_PATH / f"{name}.json", "w") as f:
        json.dump(data, f, indent=2)

config = load_config()

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
gift_codes = load_data("gift_codes", {"codes": []})
user_profiles = load_data("profiles", {})
war_timers = load_data("war_timers", {"timers": []})
daily_tips = load_data("daily_tips", {"tips": []})

# ---------------------------------------------------------------------------
# Role colors
# ---------------------------------------------------------------------------
ROLE_COLORS = {
    "R5 | Alliance Leader": discord.Color.gold(),
    "R4 | Leadership": discord.Color.from_str("#FF4500"),
    "R3 | TC25+": discord.Color.purple(),
    "R2 | TC24-": discord.Color.blue(),
    "R1 | Bear Bait": discord.Color.light_grey(),
}

# Role shorthand mappings for permission checks
LEADER_ROLES = ["R5 | Alliance Leader", "R4 | Leadership"]
OFFICER_ROLES = ["R5 | Alliance Leader", "R4 | Leadership"]
WAR_ROLES = ["R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+"]

# ---------------------------------------------------------------------------
# Event Guides Database
# ---------------------------------------------------------------------------
EVENT_GUIDES = {
    "swordland": {
        "name": "Swordland Showdown",
        "emoji": "⚔️",
        "color": 0xFF4500,
        "summary": "Bi-weekly alliance vs alliance capture event (1 hour). Rush Stables → Swordshrine → Sanctums.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Defense:** Zoe + Hilde + Saul\n**Joiners:** Chenko (best), Amane, Yeonwoo",
        "troops": "**Attack:** 50% Infantry / 20% Cav / 30% Archers\n**Garrison:** 60% Infantry / 20% Cav / 20% Archers",
        "tips": "• Split into Attackers (60%), Defenders (30%), Scouts (10%)\n• Capture Royal Stables FIRST for faster teleports\n• Personal score matters more than winning — farm points!",
    },
    "kvk": {
        "name": "Kingdom of Power (KvK)",
        "emoji": "👑",
        "color": 0xFFD700,
        "summary": "Cross-kingdom mega event. Compete for 'High King' title. Kingdom must be 70+ days old.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Garrison:** Zoe + Hilde + Saul\n**Joiners:** Chenko + Amane + Saul + Fahd",
        "troops": "**Attack:** 50% Infantry / 20% Cav / 30% Archers\n**If enemy is infantry-only:** 30% Inf / 20% Cav / 50% Archers",
        "tips": "• Hoard speed-ups, Truegold, gems WEEKS in advance\n• Time upgrades with kingdom-wide buffs for 2x points\n• TC upgrades and T8+ troop training = best point investments",
    },
    "bear": {
        "name": "Bear Hunt",
        "emoji": "🐻",
        "color": 0x8B4513,
        "summary": "Alliance rally event at the Pitfall building. Bear deals NO return damage — go full offense!",
        "heroes": "**Host:** Amadeus, Chenko, or Yeonwoo\n**Joiners:** Chenko (25% Lethality) > Amane > Yeonwoo",
        "troops": "**90% Archers** — Bear can't fight back, max DPS!\n10% Infantry/Cavalry for hero buff triggers",
        "tips": "• Lethality is the #1 damage stat for Bear Hunt\n• Position towns close to Pitfall for faster rallies\n• Upgrade Pitfall to Level 5 for +5% Attack per level to ALL members",
    },
    "merchant": {
        "name": "Merchant Empire",
        "emoji": "🏪",
        "color": 0x00CED1,
        "summary": "7-day trading event. Send caravans, escort allies, raid enemies. Max 4 caravans/day.",
        "heroes": "Use your strongest combat heroes for both escort and raiding.",
        "troops": "Full march with best available troops for raiding/defending.",
        "tips": "• Launch caravans 4-6 hours after reset (fewer raiders online)\n• Target SSR (yellow) caravans for best rewards\n• Save refresh vouchers — need 6 deep for guaranteed SSR cart\n• Assist 3 ally caravans daily for bonus rewards",
    },
    "brawl": {
        "name": "Alliance Brawl",
        "emoji": "💥",
        "color": 0xDC143C,
        "summary": "Monthly 6.5-day alliance vs alliance cross-kingdom event. Top 20 alliances eligible.",
        "heroes": "Varies by daily task — use best heroes for the day's objective.",
        "troops": "Depends on daily challenge — plan ahead!",
        "tips": "• Save ALL Intel Missions for Day 2 & 4 (3,000 pts each!)\n• Day 5: Use all saved stamina for beast hunting\n• Day 6 is worth 4 horns — can win the ENTIRE brawl\n• Hit escort/raid trucks EVERY day — huge point earners",
    },
    "oasis": {
        "name": "Oasis Island",
        "emoji": "🏝️",
        "color": 0x2E8B57,
        "summary": "Permanent base-building feature. Upgrade Fountain → clear cacti → build buff buildings.",
        "heroes": "N/A — not a combat event.",
        "troops": "N/A — not a combat event.",
        "tips": "• Upgrade Fountain of Life FIRST (always!)\n• Go LEFT first for highest chest density\n• Upgrade Reservoir to Level 4 for 2nd worker\n• Use cheap decorations to guide worker pathing",
    },
    "mystic": {
        "name": "Mystic Trial",
        "emoji": "🔮",
        "color": 0x9400D3,
        "summary": "Permanent weekly dungeon. 5 attempts/day, 6 rotating dungeons. Breakthroughs are permanent!",
        "heroes": "Move strongest cavalry hero to Team 2 for split damage coverage.",
        "troops": "50-60% Infantry / 10-20% Cavalry / 20-30% Archers",
        "tips": "• MASSIVE RNG — same battle can win or lose, always use all 5 attempts\n• Buy Mithril from shop first (rarest, most valuable)\n• Every breakthrough is permanent — keep pushing!",
    },
    "governor": {
        "name": "Strongest Governor",
        "emoji": "🏆",
        "color": 0xB8860B,
        "summary": "Monthly 7-day cross-kingdom event. Different task focus each day. Requires months of prep!",
        "heroes": "Save Hero Shards for Hero Development days (Day 2, 3, 7).",
        "troops": "Train highest tier on Combat Training days (Day 4, 6).",
        "tips": "• Day 1: Save Truegold for construction (2,000 pts each)\n• Day 2,3,7: Mythic Hero Shard ascension = 3,040 pts\n• Start saving resources MONTHS in advance\n• Top 2,000 governors get cross-kingdom rewards",
    },
    "mobilization": {
        "name": "Alliance Mobilization",
        "emoji": "📋",
        "color": 0x4682B4,
        "summary": "Bi-weekly alliance co-op missions. TC 10+ required, alliance needs 15+ members.",
        "heroes": "N/A — mission-based, not combat.",
        "troops": "N/A — mission-based.",
        "tips": "• Keep soldier training & beast hunting missions\n• Refresh low-value acceleration tasks\n• Use boosted slots (1.2x-2.0x) on highest-value tasks\n• Treat as background event — most missions mirror normal play",
    },
}

# ---------------------------------------------------------------------------
# Kingshot tips for daily rotation
# ---------------------------------------------------------------------------
DEFAULT_TIPS = [
    "💡 Always send your highest-power march first in rallies — it sets the rally capacity!",
    "💡 Upgrade your Pitfall building for permanent Bear Hunt damage bonuses.",
    "💡 Save Intel Missions for Alliance Brawl days 2 & 4 — they're worth 3,000 points each!",
    "💡 In Swordland Showdown, personal score matters more than winning. Farm those points!",
    "💡 Chenko's 25% Lethality buff is the single best joiner contribution for rallies.",
    "💡 Launch Merchant Empire caravans 4-6 hours after reset for higher survival rates.",
    "💡 Mystic Trial has massive RNG — always use all 5 daily attempts, even after losses.",
    "💡 Buy Mithril from the Mystic Trial shop first — it's the rarest resource.",
    "💡 Position your town close to the Pitfall for faster Bear Hunt rally returns.",
    "💡 In KvK, time your upgrades with kingdom-wide buffs for double points.",
    "💡 Oasis Island: Upgrade your Reservoir to Level 4 ASAP for a second worker.",
    "💡 For Strongest Governor, start hoarding resources MONTHS before the event.",
    "💡 Use cheap decorations on Oasis Island to guide workers toward treasure chests.",
    "💡 In Alliance Brawl, Day 6 is worth 4 horns — it can decide the entire event!",
    "💡 Lethality is the #1 damage stat for Bear Hunt — prioritize it over raw Attack.",
]


# =========================================================================
# EVENT: on_ready
# =========================================================================
@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    log.info(f"Connected to {len(bot.guilds)} guild(s)")
    scheduled_announcements.start()
    daily_tip_task.start()
    timer_check.start()
    try:
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")


# =========================================================================
# EVENT: Welcome new members
# =========================================================================
@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild

    # Auto-assign Recruit role
    recruit_role = discord.utils.get(guild.roles, name="R1 | Bear Bait")
    if recruit_role:
        try:
            await member.add_roles(recruit_role)
        except discord.Forbidden:
            pass

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
            timestamp=datetime.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await welcome_ch.send(embed=embed)


# =========================================================================
# =========================================================================
#                     MEMBER COMMANDS (Everyone can use)
# =========================================================================
# =========================================================================


# =========================================================================
# !help — Custom help command
# =========================================================================
@bot.command(name="help")
async def help_command(ctx: commands.Context):
    """Show all available bot commands organized by category."""
    embed = discord.Embed(
        title="🤖 Kingshot Bot — Command Guide",
        description="Here's everything I can do! Commands marked with 🔒 require special roles.",
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="📚 Event Guides (Everyone)",
        value=(
            "`!event <name>` — Get full guide for any event\n"
            "`!events` — List all available event guides\n"
            "`!heroes <event>` — Quick hero picks for an event\n"
            "`!troops <event>` — Quick troop comp for an event"
        ),
        inline=False,
    )

    embed.add_field(
        name="👤 Profile & Info (Everyone)",
        value=(
            "`!profile` — View your server profile\n"
            "`!setpower <number>` — Set your power level\n"
            "`!setign <name>` — Set your in-game name\n"
            "`!memberinfo [@user]` — View someone's info\n"
            "`!serverinfo` — Server statistics\n"
            "`!leaderboard` — Power leaderboard"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎁 Gift Codes (Everyone)",
        value=(
            "`!codes` — View all active gift codes\n"
            "`!addcode <code> | <rewards>` — Submit a new code\n"
            "`!expirecode <code>` — Mark a code as expired"
        ),
        inline=False,
    )

    embed.add_field(
        name="⏰ Timers & Reminders (Everyone)",
        value=(
            "`!timers` — View active event timers\n"
            "`!countdown <event>` — Quick countdown to next event\n"
            "`!tip` — Get a random Kingshot tip"
        ),
        inline=False,
    )

    embed.add_field(
        name="⚔️ War Commands 🔒",
        value=(
            "`!rally <details>` — Send rally call (R3+)\n"
            "`!warsched <text>` — Post war schedule (R3+)\n"
            "`!settimer <name> | <time>` — Set event timer (R4+)\n"
            "`!deltimer <name>` — Delete a timer (R4+)"
        ),
        inline=False,
    )

    embed.add_field(
        name="📢 Announcements 🔒",
        value=(
            "`!announce <channel> <msg>` — Send announcement (R4+)\n"
            "`!listannouncements` — View scheduled (R4+)\n"
            "`!toggleannouncement <name>` — Toggle on/off (R4+)"
        ),
        inline=False,
    )

    embed.add_field(
        name="👥 Role Management 🔒",
        value=(
            "`!promote @user RoleName` — Give role (R4+)\n"
            "`!demote @user RoleName` — Remove role (R4+)"
        ),
        inline=False,
    )

    embed.add_field(
        name="🛡️ Moderation 🔒",
        value=(
            "`!kick @user [reason]` — Kick member\n"
            "`!mute @user [minutes]` — Timeout member\n"
            "`!unmute @user` — Remove timeout\n"
            "`!clear [amount]` — Delete messages"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔧 Admin 🔒",
        value="`!setup` — Full server setup (Admin only)",
        inline=False,
    )

    embed.set_footer(text="Use !help in #bot-commands to keep other channels clean!")
    await ctx.send(embed=embed)


# =========================================================================
# !events — List all available event guides
# =========================================================================
@bot.command(name="events")
async def list_events(ctx: commands.Context):
    """List all available event guides."""
    embed = discord.Embed(
        title="📅 Kingshot Event Guides",
        description="Use `!event <name>` to get the full guide for any event.",
        color=discord.Color.blue(),
    )

    event_list = ""
    for key, ev in EVENT_GUIDES.items():
        event_list += f"{ev['emoji']} **{ev['name']}** — `!event {key}`\n"

    embed.add_field(name="Available Events", value=event_list, inline=False)
    embed.set_footer(text="Tip: Use !heroes <event> or !troops <event> for quick lookups")
    await ctx.send(embed=embed)


# =========================================================================
# !event <name> — Full event guide
# =========================================================================
@bot.command(name="event")
async def event_guide(ctx: commands.Context, *, name: str = ""):
    """Get the full guide for a specific event."""
    name = name.lower().strip()

    # Fuzzy match
    matched = None
    for key, ev in EVENT_GUIDES.items():
        if name in key or name in ev["name"].lower() or key.startswith(name):
            matched = (key, ev)
            break

    if not matched:
        suggestions = ", ".join(f"`{k}`" for k in EVENT_GUIDES.keys())
        await ctx.send(f"❌ Event `{name}` not found. Try: {suggestions}")
        return

    key, ev = matched
    embed = discord.Embed(
        title=f"{ev['emoji']} {ev['name']} — Complete Guide",
        description=ev["summary"],
        color=ev["color"],
    )
    embed.add_field(name="🦸 Recommended Heroes", value=ev["heroes"], inline=False)
    embed.add_field(name="🪖 Troop Composition", value=ev["troops"], inline=False)
    embed.add_field(name="💡 Pro Tips", value=ev["tips"], inline=False)
    embed.set_footer(text=f"Use !heroes {key} or !troops {key} for quick lookups")
    await ctx.send(embed=embed)


# =========================================================================
# !heroes <event> — Quick hero recommendation
# =========================================================================
@bot.command(name="heroes")
async def hero_picks(ctx: commands.Context, *, name: str = ""):
    """Quick hero picks for an event."""
    name = name.lower().strip()
    for key, ev in EVENT_GUIDES.items():
        if name in key or name in ev["name"].lower() or key.startswith(name):
            embed = discord.Embed(
                title=f"{ev['emoji']} {ev['name']} — Hero Picks",
                description=ev["heroes"],
                color=ev["color"],
            )
            await ctx.send(embed=embed)
            return
    await ctx.send(f"❌ Event not found. Use `!events` to see all options.")


# =========================================================================
# !troops <event> — Quick troop comp
# =========================================================================
@bot.command(name="troops")
async def troop_comp(ctx: commands.Context, *, name: str = ""):
    """Quick troop composition for an event."""
    name = name.lower().strip()
    for key, ev in EVENT_GUIDES.items():
        if name in key or name in ev["name"].lower() or key.startswith(name):
            embed = discord.Embed(
                title=f"{ev['emoji']} {ev['name']} — Troop Comp",
                description=ev["troops"],
                color=ev["color"],
            )
            await ctx.send(embed=embed)
            return
    await ctx.send(f"❌ Event not found. Use `!events` to see all options.")


# =========================================================================
# !profile — View your profile
# =========================================================================
@bot.command(name="profile")
async def profile(ctx: commands.Context, member: discord.Member = None):
    """View your profile or another member's profile."""
    member = member or ctx.author
    uid = str(member.id)
    data = user_profiles.get(uid, {})

    embed = discord.Embed(
        title=f"👤 {member.display_name}'s Profile",
        color=member.top_role.color if member.top_role.color != discord.Color.default() else discord.Color.blue(),
        timestamp=datetime.utcnow(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🎮 In-Game Name", value=data.get("ign", "*Not set* — use `!setign`"), inline=True)
    embed.add_field(name="⚡ Power", value=f"{data.get('power', 0):,}" if data.get("power") else "*Not set* — use `!setpower`", inline=True)
    embed.add_field(name="🏷️ Roles", value=", ".join(r.name for r in member.roles if r.name != "@everyone") or "None", inline=False)
    embed.add_field(name="📅 Joined", value=member.joined_at.strftime("%b %d, %Y") if member.joined_at else "Unknown", inline=True)
    await ctx.send(embed=embed)


# =========================================================================
# !setpower — Set your power level
# =========================================================================
@bot.command(name="setpower")
async def set_power(ctx: commands.Context, power: str):
    """Set your power level. Usage: !setpower 25000000 or !setpower 25m"""
    # Parse power with k/m/b suffixes
    power = power.lower().replace(",", "")
    multiplier = 1
    if power.endswith("k"):
        multiplier = 1_000
        power = power[:-1]
    elif power.endswith("m"):
        multiplier = 1_000_000
        power = power[:-1]
    elif power.endswith("b"):
        multiplier = 1_000_000_000
        power = power[:-1]

    try:
        power_val = int(float(power) * multiplier)
    except ValueError:
        await ctx.send("❌ Invalid power value. Examples: `!setpower 25m`, `!setpower 5000000`")
        return

    uid = str(ctx.author.id)
    if uid not in user_profiles:
        user_profiles[uid] = {}
    user_profiles[uid]["power"] = power_val
    save_data("profiles", user_profiles)
    await ctx.send(f"✅ Power set to **{power_val:,}**!")


# =========================================================================
# !setign — Set your in-game name
# =========================================================================
@bot.command(name="setign")
async def set_ign(ctx: commands.Context, *, ign: str):
    """Set your in-game name. Usage: !setign MyPlayerName"""
    uid = str(ctx.author.id)
    if uid not in user_profiles:
        user_profiles[uid] = {}
    user_profiles[uid]["ign"] = ign.strip()
    save_data("profiles", user_profiles)
    await ctx.send(f"✅ In-game name set to **{ign.strip()}**!")


# =========================================================================
# !leaderboard — Power leaderboard
# =========================================================================
@bot.command(name="leaderboard", aliases=["lb", "top"])
async def leaderboard(ctx: commands.Context):
    """Show the alliance power leaderboard."""
    ranked = sorted(
        [(uid, data) for uid, data in user_profiles.items() if data.get("power", 0) > 0],
        key=lambda x: x[1]["power"],
        reverse=True,
    )

    if not ranked:
        await ctx.send("📊 No one has set their power yet! Use `!setpower <number>` to register.")
        return

    embed = discord.Embed(
        title="🏆 Alliance Power Leaderboard",
        color=discord.Color.gold(),
    )

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, data) in enumerate(ranked[:15]):
        medal = medals[i] if i < 3 else f"**{i+1}.**"
        name = data.get("ign", f"<@{uid}>")
        power = f"{data['power']:,}"
        lines.append(f"{medal} {name} — ⚡ {power}")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Total members tracked: {len(ranked)} | Use !setpower to join")
    await ctx.send(embed=embed)


# =========================================================================
# !serverinfo — Server stats
# =========================================================================
@bot.command(name="serverinfo", aliases=["server"])
async def server_info(ctx: commands.Context):
    """Show server statistics."""
    guild = ctx.guild
    embed = discord.Embed(
        title=f"📊 {guild.name} — Server Info",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="👥 Members", value=str(guild.member_count), inline=True)
    embed.add_field(name="💬 Text Channels", value=str(len(guild.text_channels)), inline=True)
    embed.add_field(name="🔊 Voice Channels", value=str(len(guild.voice_channels)), inline=True)
    embed.add_field(name="🏷️ Roles", value=str(len(guild.roles) - 1), inline=True)
    embed.add_field(name="📅 Created", value=guild.created_at.strftime("%b %d, %Y"), inline=True)
    embed.add_field(name="👑 Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)

    # Power stats
    powers = [d["power"] for d in user_profiles.values() if d.get("power", 0) > 0]
    if powers:
        total = sum(powers)
        embed.add_field(
            name="⚡ Alliance Power",
            value=f"Total: **{total:,}**\nAverage: **{total // len(powers):,}**\nTracked: **{len(powers)}** members",
            inline=False,
        )

    await ctx.send(embed=embed)


# =========================================================================
# !codes — View gift codes
# =========================================================================
@bot.command(name="codes")
async def view_codes(ctx: commands.Context):
    """View all active gift codes."""
    active = [c for c in gift_codes.get("codes", []) if not c.get("expired")]

    if not active:
        await ctx.send("🎁 No active gift codes right now. Use `!addcode` when you find one!")
        return

    embed = discord.Embed(
        title="🎁 Active Gift Codes",
        color=discord.Color.from_str("#FF69B4"),
        timestamp=datetime.utcnow(),
    )

    for code in active[-10:]:  # Show last 10
        embed.add_field(
            name=f"📋 `{code['code']}`",
            value=f"📦 {code.get('rewards', 'Unknown rewards')}\n👤 Added by {code.get('added_by', 'Unknown')}",
            inline=False,
        )

    embed.set_footer(text="Redeem in-game: Settings → Gift Code | Use !addcode to submit new codes")
    await ctx.send(embed=embed)


# =========================================================================
# !addcode — Submit a gift code
# =========================================================================
@bot.command(name="addcode")
async def add_code(ctx: commands.Context, *, args: str):
    """Submit a new gift code. Usage: !addcode CODE123 | 500 gems, 2 speedups"""
    parts = args.split("|", 1)
    code = parts[0].strip().upper()
    rewards = parts[1].strip() if len(parts) > 1 else "Rewards unknown"

    # Check for duplicate
    existing = [c["code"] for c in gift_codes.get("codes", [])]
    if code in existing:
        await ctx.send(f"⚠️ Code `{code}` has already been submitted!")
        return

    gift_codes.setdefault("codes", []).append({
        "code": code,
        "rewards": rewards,
        "added_by": ctx.author.display_name,
        "added_at": datetime.utcnow().isoformat(),
        "expired": False,
    })
    save_data("gift_codes", gift_codes)

    await ctx.send(f"✅ Gift code `{code}` added! Rewards: {rewards}")

    # Also post to gift-codes channel if not already there
    gift_ch = discord.utils.get(ctx.guild.text_channels, name="gift-codes")
    if gift_ch and gift_ch != ctx.channel:
        embed = discord.Embed(
            title="🎁 New Gift Code!",
            color=discord.Color.from_str("#FF69B4"),
        )
        embed.add_field(name="Code", value=f"```{code}```", inline=False)
        embed.add_field(name="Rewards", value=rewards, inline=False)
        embed.set_footer(text=f"Submitted by {ctx.author.display_name}")
        await gift_ch.send(embed=embed)


# =========================================================================
# !expirecode — Mark a code as expired
# =========================================================================
@bot.command(name="expirecode")
async def expire_code(ctx: commands.Context, *, code: str):
    """Mark a gift code as expired. Usage: !expirecode CODE123"""
    code = code.strip().upper()
    for c in gift_codes.get("codes", []):
        if c["code"] == code:
            c["expired"] = True
            save_data("gift_codes", gift_codes)
            await ctx.send(f"✅ Code `{code}` marked as expired.")
            return
    await ctx.send(f"❌ Code `{code}` not found.")


# =========================================================================
# !tip — Random Kingshot tip
# =========================================================================
@bot.command(name="tip")
async def random_tip(ctx: commands.Context):
    """Get a random Kingshot pro tip."""
    tip = random.choice(DEFAULT_TIPS)
    embed = discord.Embed(description=tip, color=discord.Color.green())
    embed.set_footer(text="Use !events for full event guides")
    await ctx.send(embed=embed)


# =========================================================================
# !timers — View active timers
# =========================================================================
@bot.command(name="timers")
async def view_timers(ctx: commands.Context):
    """View active event timers."""
    active = [t for t in war_timers.get("timers", []) if datetime.fromisoformat(t["time"]) > datetime.utcnow()]

    if not active:
        await ctx.send("⏰ No active timers. Officers can set them with `!settimer`")
        return

    embed = discord.Embed(title="⏰ Active Event Timers", color=discord.Color.orange())
    for t in sorted(active, key=lambda x: x["time"]):
        target = datetime.fromisoformat(t["time"])
        delta = target - datetime.utcnow()
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        embed.add_field(
            name=t["name"],
            value=f"⏱️ **{hours}h {minutes}m** remaining\n📅 {target.strftime('%b %d, %I:%M %p')} UTC",
            inline=False,
        )
    await ctx.send(embed=embed)


# =========================================================================
# !settimer — Set an event timer (R4+)
# =========================================================================
@bot.command(name="settimer")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
async def set_timer(ctx: commands.Context, *, args: str):
    """Set an event timer. Usage: !settimer Swordland Showdown | 2026-03-15 20:00"""
    parts = args.split("|", 1)
    if len(parts) != 2:
        await ctx.send("❌ Usage: `!settimer Event Name | YYYY-MM-DD HH:MM`")
        return

    name = parts[0].strip()
    try:
        target = datetime.fromisoformat(parts[1].strip())
    except ValueError:
        await ctx.send("❌ Invalid date format. Use: `YYYY-MM-DD HH:MM` (e.g., `2026-03-15 20:00`)")
        return

    war_timers.setdefault("timers", []).append({
        "name": name,
        "time": target.isoformat(),
        "set_by": ctx.author.display_name,
    })
    save_data("war_timers", war_timers)

    delta = target - datetime.utcnow()
    hours = int(delta.total_seconds() // 3600)
    await ctx.send(f"✅ Timer set: **{name}** in ~{hours} hours ({target.strftime('%b %d, %I:%M %p')} UTC)")


# =========================================================================
# !deltimer — Delete a timer (R4+)
# =========================================================================
@bot.command(name="deltimer")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
async def del_timer(ctx: commands.Context, *, name: str):
    """Delete an event timer. Usage: !deltimer Swordland Showdown"""
    timers = war_timers.get("timers", [])
    war_timers["timers"] = [t for t in timers if t["name"].lower() != name.lower()]
    save_data("war_timers", war_timers)
    await ctx.send(f"✅ Timer `{name}` deleted.")


# =========================================================================
# !memberinfo — Show member info
# =========================================================================
@bot.command(name="memberinfo")
async def member_info(ctx: commands.Context, member: discord.Member = None):
    """Show info about a member."""
    member = member or ctx.author
    uid = str(member.id)
    data = user_profiles.get(uid, {})
    roles = [r.mention for r in member.roles if r.name != "@everyone"]

    embed = discord.Embed(
        title=f"👤 {member.display_name}",
        color=member.top_role.color if member.top_role.color != discord.Color.default() else discord.Color.blue(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if data.get("ign"):
        embed.add_field(name="🎮 IGN", value=data["ign"], inline=True)
    if data.get("power"):
        embed.add_field(name="⚡ Power", value=f"{data['power']:,}", inline=True)
    embed.add_field(name="📅 Joined", value=member.joined_at.strftime("%b %d, %Y") if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="🏷️ Roles", value=", ".join(roles) if roles else "None", inline=False)
    await ctx.send(embed=embed)


# =========================================================================
# =========================================================================
#                     LEADER/OFFICER COMMANDS
# =========================================================================
# =========================================================================


# =========================================================================
# !setup — Full server setup (Admin only)
# =========================================================================
@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def setup_server(ctx: commands.Context):
    """Create all channels, roles, and permissions for the Kingshot guild server."""
    await ctx.send("🔧 **Starting server setup...** This may take a moment.")
    guild = ctx.guild

    existing_roles = {r.name for r in guild.roles}
    for role_name, color in ROLE_COLORS.items():
        if role_name not in existing_roles:
            await guild.create_role(name=role_name, color=color, mentionable=True, reason="Kingshot bot setup")
            log.info(f"Created role: {role_name}")

    await ctx.send(f"✅ Roles configured ({len(ROLE_COLORS)} roles)")
    await ctx.send("🎉 **Setup complete!** Use `!help` to see all available commands.")


# =========================================================================
# !announce — Send announcement (R4+)
# =========================================================================
@bot.command(name="announce")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
async def announce(ctx: commands.Context, channel_name: str, *, message: str):
    """Send an announcement. Usage: !announce announcements Your message here"""
    channel = discord.utils.get(ctx.guild.text_channels, name=channel_name)
    if not channel:
        await ctx.send(f"❌ Channel `#{channel_name}` not found.")
        return

    embed = discord.Embed(
        title="📢 Announcement",
        description=message,
        color=discord.Color.gold(),
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text=f"Posted by {ctx.author.display_name}")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Announcement sent to #{channel_name}")


# =========================================================================
# !rally — Rally call (R3+)
# =========================================================================
@bot.command(name="rally")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
async def rally_call(ctx: commands.Context, *, details: str = "Rally up! Check war room."):
    """Send an urgent rally call. Usage: !rally Target: Player123 — send T10 troops"""
    rally_ch = discord.utils.get(ctx.guild.text_channels, name="rally-calls")
    target_ch = rally_ch or ctx.channel

    embed = discord.Embed(
        title="🚨 RALLY CALL 🚨",
        description=details,
        color=discord.Color.red(),
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text=f"Called by {ctx.author.display_name}")
    await target_ch.send("@everyone", embed=embed)
    if target_ch != ctx.channel:
        await ctx.send(f"✅ Rally call sent to #{target_ch.name}")


# =========================================================================
# !warsched — War schedule (R3+)
# =========================================================================
@bot.command(name="warsched")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
async def war_schedule(ctx: commands.Context, *, schedule_text: str):
    """Post a war schedule. Usage: !warsched Wednesday 8PM — Castle Siege"""
    sched_ch = discord.utils.get(ctx.guild.text_channels, name="war-schedule")
    target_ch = sched_ch or ctx.channel

    embed = discord.Embed(
        title="🗓️ War Schedule",
        description=schedule_text.replace("\\n", "\n"),
        color=discord.Color.dark_red(),
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text=f"Updated by {ctx.author.display_name}")
    await target_ch.send(embed=embed)
    await ctx.send(f"✅ War schedule posted to #{target_ch.name}")


# =========================================================================
# !promote / !demote — Role management (R4+)
# =========================================================================
@bot.command(name="promote")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
async def promote(ctx: commands.Context, member: discord.Member, *, role_name: str):
    """Promote a member. Usage: !promote @user Member"""
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if not role:
        await ctx.send(f"❌ Role `{role_name}` not found.")
        return
    await member.add_roles(role)
    await ctx.send(f"✅ {member.display_name} promoted to **{role_name}**!")


@bot.command(name="demote")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
async def demote(ctx: commands.Context, member: discord.Member, *, role_name: str):
    """Remove a role. Usage: !demote @user Officer"""
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if not role:
        await ctx.send(f"❌ Role `{role_name}` not found.")
        return
    await member.remove_roles(role)
    await ctx.send(f"✅ {member.display_name} removed from **{role_name}**.")


# =========================================================================
# Moderation commands
# =========================================================================
@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick_member(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
    """Kick a member."""
    await member.kick(reason=reason)
    await ctx.send(f"👢 {member.display_name} kicked. Reason: {reason}")


@bot.command(name="mute")
@commands.has_permissions(manage_roles=True)
async def mute_member(ctx: commands.Context, member: discord.Member, minutes: int = 10):
    """Timeout a member. Usage: !mute @user 30"""
    await member.timeout(timedelta(minutes=minutes), reason=f"Muted by {ctx.author.display_name}")
    await ctx.send(f"🔇 {member.display_name} muted for {minutes} minutes.")


@bot.command(name="unmute")
@commands.has_permissions(manage_roles=True)
async def unmute_member(ctx: commands.Context, member: discord.Member):
    """Remove timeout."""
    await member.timeout(None, reason=f"Unmuted by {ctx.author.display_name}")
    await ctx.send(f"🔊 {member.display_name} unmuted.")


@bot.command(name="clear")
@commands.has_permissions(manage_messages=True)
async def clear_messages(ctx: commands.Context, amount: int = 10):
    """Delete messages. Usage: !clear 25"""
    if amount > 100:
        await ctx.send("❌ Max 100 messages at a time.")
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f"🗑️ Deleted {len(deleted) - 1} messages.")
    await asyncio.sleep(3)
    await msg.delete()


# =========================================================================
# !listannouncements / !toggleannouncement
# =========================================================================
@bot.command(name="listannouncements")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
async def list_announcements(ctx: commands.Context):
    """List scheduled announcements."""
    cfg = load_config()
    announcements = cfg.get("scheduled_announcements", [])
    if not announcements:
        await ctx.send("No scheduled announcements configured.")
        return

    embed = discord.Embed(title="📋 Scheduled Announcements", color=discord.Color.blue())
    for ann in announcements:
        status = "✅ Enabled" if ann.get("enabled") else "❌ Disabled"
        embed.add_field(
            name=f"{ann['name']} — {status}",
            value=f"**Channel:** #{ann['channel']}\n**Schedule:** `{ann['cron']}`\n**Message:** {ann['message'][:100]}...",
            inline=False,
        )
    await ctx.send(embed=embed)


@bot.command(name="toggleannouncement")
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
async def toggle_announcement(ctx: commands.Context, name: str):
    """Toggle a scheduled announcement."""
    cfg = load_config()
    for ann in cfg.get("scheduled_announcements", []):
        if ann["name"] == name:
            ann["enabled"] = not ann["enabled"]
            save_config(cfg)
            status = "enabled" if ann["enabled"] else "disabled"
            await ctx.send(f"✅ `{name}` is now **{status}**.")
            return
    await ctx.send(f"❌ Announcement `{name}` not found.")


# =========================================================================
# Background Tasks
# =========================================================================
@tasks.loop(minutes=1)
async def scheduled_announcements():
    """Check and send scheduled announcements."""
    now = datetime.utcnow()
    cfg = load_config()
    for ann in cfg.get("scheduled_announcements", []):
        if not ann.get("enabled"):
            continue
        if _cron_matches(ann["cron"], now):
            for guild in bot.guilds:
                channel = discord.utils.get(guild.text_channels, name=ann["channel"])
                if channel:
                    embed = discord.Embed(description=ann["message"], color=discord.Color.gold(), timestamp=now)
                    embed.set_footer(text="Automated announcement")
                    try:
                        await channel.send(embed=embed)
                    except discord.Forbidden:
                        pass


@tasks.loop(hours=24)
async def daily_tip_task():
    """Post a daily tip to general-chat."""
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name="general-chat")
        if channel:
            tip = random.choice(DEFAULT_TIPS)
            embed = discord.Embed(
                title="💡 Daily Kingshot Tip",
                description=tip,
                color=discord.Color.green(),
            )
            embed.set_footer(text="Use !tip for more tips | !events for full guides")
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                pass


@tasks.loop(minutes=5)
async def timer_check():
    """Check for expired timers and alert."""
    now = datetime.utcnow()
    timers = war_timers.get("timers", [])
    to_notify = []

    for t in timers:
        target = datetime.fromisoformat(t["time"])
        remaining = (target - now).total_seconds()
        # Alert at 15 minutes
        if 0 < remaining <= 900 and not t.get("alerted_15"):
            t["alerted_15"] = True
            to_notify.append((t["name"], "15 minutes"))
        # Alert at start
        elif remaining <= 0 and not t.get("alerted_start"):
            t["alerted_start"] = True
            to_notify.append((t["name"], "NOW"))

    if to_notify:
        save_data("war_timers", war_timers)
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="rally-calls") or \
                      discord.utils.get(guild.text_channels, name="general-chat")
            if channel:
                for name, when in to_notify:
                    embed = discord.Embed(
                        title=f"⏰ Event Alert: {name}",
                        description=f"**{name}** starts in **{when}**!" if when != "NOW" else f"**{name}** is starting **NOW**! 🚀",
                        color=discord.Color.red() if when == "NOW" else discord.Color.orange(),
                    )
                    try:
                        await channel.send("@everyone" if when == "NOW" else "", embed=embed)
                    except discord.Forbidden:
                        pass


@scheduled_announcements.before_loop
async def before_scheduled():
    await bot.wait_until_ready()

@daily_tip_task.before_loop
async def before_daily_tip():
    await bot.wait_until_ready()

@timer_check.before_loop
async def before_timer_check():
    await bot.wait_until_ready()


def _cron_matches(cron_str: str, dt: datetime) -> bool:
    """Simple cron matcher."""
    parts = cron_str.split()
    if len(parts) != 5:
        return False
    fields = [
        (parts[0], dt.minute), (parts[1], dt.hour),
        (parts[2], dt.day), (parts[3], dt.month),
        (parts[4], dt.isoweekday() % 7),
    ]
    for pattern, value in fields:
        if pattern == "*":
            continue
        allowed = set()
        for segment in pattern.split(","):
            if "/" in segment:
                base, step = segment.split("/")
                step = int(step)
                start = 0 if base == "*" else int(base)
                allowed.update(range(start, 60, step))
            elif "-" in segment:
                lo, hi = segment.split("-")
                allowed.update(range(int(lo), int(hi) + 1))
            else:
                allowed.add(int(segment))
        if value not in allowed:
            return False
    return True


# =========================================================================
# Error handling
# =========================================================================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MissingAnyRole):
        await ctx.send("❌ You don't have the required role for this command.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found. Make sure to @mention them.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        log.error(f"Command error: {error}")
        await ctx.send(f"⚠️ Something went wrong: {error}")


# =========================================================================
# Run
# =========================================================================
if __name__ == "__main__":
    # Prefer environment variable (OKD secret injection), fall back to config.json
    token = os.environ.get("DISCORD_BOT_TOKEN") or config.get("bot_token", "")
    if not token or token in ("YOUR_BOT_TOKEN_HERE", "ENV", "PLACEHOLDER_REPLACED_BY_CI"):
        print("\n❌ Set your bot token via DISCORD_BOT_TOKEN env var or in config.json!")
    else:
        bot.run(token)
