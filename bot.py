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
  3. Use /setup in your server to create all channels and roles
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
from typing import List, Optional
import json
import asyncio
import logging
import random
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from zoneinfo import ZoneInfo, available_timezones
from discord.ui import View, Button, Select, button, select

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


def _utc_from_iso(iso_str: str) -> datetime:
    """Parse an ISO datetime string and ensure it's UTC-aware."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# Load event cycle data
EVENT_CYCLE_PATH = Path(__file__).parent / "event_cycle.json"
def load_event_cycle():
    if EVENT_CYCLE_PATH.exists():
        with open(EVENT_CYCLE_PATH) as f:
            return json.load(f)
    return {"cycle_anchor": "2026-03-06", "cycle_length_days": 28, "events": []}

def get_cycle_day(dt=None):
    """Get the current day in the event cycle (0-27)."""
    cycle = load_event_cycle()
    anchor = datetime.strptime(cycle["cycle_anchor"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if dt is None:
        dt = datetime.now(timezone.utc)
    delta = (dt - anchor).days
    return delta % cycle.get("cycle_length_days", 28)

def get_active_events(dt=None):
    """Get events that are active on the given date."""
    cycle = load_event_cycle()
    cycle_len = cycle.get("cycle_length_days", 28)
    day = get_cycle_day(dt)
    active = []
    for ev in cycle.get("events", []):
        start = ev["cycle_day_start"]
        duration = ev.get("duration_days", 1)
        recurring = ev.get("recurring_every_days")
        if recurring:
            # Check if this recurring event is active today
            for offset in range(0, cycle_len, recurring):
                ev_start = (start + offset) % cycle_len
                if ev_start <= day < ev_start + duration:
                    active.append(ev)
                    break
        else:
            end = start + duration
            if end <= cycle_len:
                if start <= day < end:
                    active.append(ev)
            else:
                # Event wraps around cycle boundary
                if day >= start or day < (end % cycle_len):
                    active.append(ev)
    return active

def get_upcoming_events(days_ahead=7, dt=None):
    """Get events starting in the next N days."""
    cycle = load_event_cycle()
    cycle_len = cycle.get("cycle_length_days", 28)
    if dt is None:
        dt = datetime.now(timezone.utc)
    today = get_cycle_day(dt)
    upcoming = []
    for ev in cycle.get("events", []):
        start = ev["cycle_day_start"]
        recurring = ev.get("recurring_every_days")
        if recurring:
            for offset in range(0, cycle_len, recurring):
                ev_start = (start + offset) % cycle_len
                days_until = (ev_start - today) % cycle_len
                if 0 < days_until <= days_ahead:
                    start_date = dt + timedelta(days=days_until)
                    upcoming.append((days_until, start_date, ev))
            # Also check if starting today
            for offset in range(0, cycle_len, recurring):
                ev_start = (start + offset) % cycle_len
                if ev_start == today:
                    upcoming.append((0, dt, ev))
        else:
            days_until = (start - today) % cycle_len
            if days_until == 0:
                upcoming.append((0, dt, ev))
            elif days_until <= days_ahead:
                start_date = dt + timedelta(days=days_until)
                upcoming.append((days_until, start_date, ev))
    # Sort by days until start
    upcoming.sort(key=lambda x: x[0])
    # Deduplicate by event name
    seen = set()
    deduped = []
    for item in upcoming:
        if item[2]["name"] not in seen:
            seen.add(item[2]["name"])
            deduped.append(item)
    return deduped

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents, help_command=None)

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
gift_codes = load_data("gift_codes", {"codes": []})
user_profiles = load_data("profiles", {})
war_timers = load_data("war_timers", {"timers": []})
daily_tips = load_data("daily_tips", {"tips": []})
user_timezones = load_data("user_timezones", {})
war_signups = load_data("war_signups", {"signups": []})
power_history = load_data("power_history", {})
reminder_optins = load_data("reminder_optins", {"users": []})
last_announcement_fires = {}

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
# UI Views & Components
# ---------------------------------------------------------------------------

class ConfirmView(View):
    """Confirmation dialog with Confirm/Cancel buttons."""
    def __init__(self):
        super().__init__()
        self.confirmed = False

    @button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        self.confirmed = False
        await interaction.response.defer()
        self.stop()


class EventSelectView(View):
    """Dropdown menu to select and view event guides."""
    def __init__(self, event_guides: dict):
        super().__init__()
        self._event_guides = event_guides
        options = [
            discord.SelectOption(label=f"{ev['emoji']} {ev['name']}", value=key)
            for key, ev in event_guides.items()
        ]
        sel = Select(
            placeholder="Choose an event to view...",
            min_values=1,
            max_values=1,
            options=options,
        )
        sel.callback = self._select_callback
        self.add_item(sel)

    async def _select_callback(self, interaction: discord.Interaction):
        key = interaction.data["values"][0]
        ev = self._event_guides[key]
        embed = discord.Embed(
            title=f"{ev['emoji']} {ev['name']} — Complete Guide",
            description=ev["summary"],
            color=ev["color"],
        )
        embed.add_field(name="🦸 Recommended Heroes", value=ev["heroes"], inline=False)
        embed.add_field(name="🪖 Troop Composition", value=ev["troops"], inline=False)
        embed.add_field(name="💡 Pro Tips", value=ev["tips"], inline=False)
        embed.set_footer(text=f"Use /heroes {key} or /troops {key} for quick lookups")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RallyView(View):
    """Rally call with Join/Can't Make It buttons."""
    def __init__(self):
        super().__init__()
        self.joined = []
        self.declined = []

    @button(label="✅ Join Rally", style=discord.ButtonStyle.success)
    async def join_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id not in self.joined:
            self.joined.append(interaction.user.id)
        if interaction.user.id in self.declined:
            self.declined.remove(interaction.user.id)
        await interaction.response.defer()

    @button(label="❌ Can't Make It", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id not in self.declined:
            self.declined.append(interaction.user.id)
        if interaction.user.id in self.joined:
            self.joined.remove(interaction.user.id)
        await interaction.response.defer()


class PaginatorView(View):
    """Paginated embed with Previous/Next buttons and page counter."""
    def __init__(self, embeds: List[discord.Embed]):
        super().__init__()
        self.embeds = embeds
        self.current_page = 0

    @button(label="◀️", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(
                embed=self.embeds[self.current_page],
                view=self
            )
        else:
            await interaction.response.defer()

    @button(label="▶️", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            await interaction.response.edit_message(
                embed=self.embeds[self.current_page],
                view=self
            )
        else:
            await interaction.response.defer()

    async def on_timeout(self):
        # Remove buttons when interaction expires
        pass


class WarSignupView(View):
    """War signup buttons for tracking attendance."""
    def __init__(self, event_name: str):
        super().__init__()
        self.event_name = event_name
        self.confirmed = []
        self.declined = []
        self.maybe = []

    @button(label="✅ Sign Up", style=discord.ButtonStyle.success)
    async def signup_button(self, interaction: discord.Interaction, button: Button):
        uid = interaction.user.id
        if uid not in self.confirmed:
            self.confirmed.append(uid)
        if uid in self.declined:
            self.declined.remove(uid)
        if uid in self.maybe:
            self.maybe.remove(uid)
        await interaction.response.defer()

    @button(label="❌ Can't Make It", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        uid = interaction.user.id
        if uid not in self.declined:
            self.declined.append(uid)
        if uid in self.confirmed:
            self.confirmed.remove(uid)
        if uid in self.maybe:
            self.maybe.remove(uid)
        await interaction.response.defer()

    @button(label="❓ Maybe", style=discord.ButtonStyle.blurple)
    async def maybe_button(self, interaction: discord.Interaction, button: Button):
        uid = interaction.user.id
        if uid not in self.maybe:
            self.maybe.append(uid)
        if uid in self.confirmed:
            self.confirmed.remove(uid)
        if uid in self.declined:
            self.declined.remove(uid)
        await interaction.response.defer()


class RolePanelView(View):
    """Persistent role selection buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="R5 | Alliance Leader", style=discord.ButtonStyle.primary, custom_id="role_r5")
    async def r5_button(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name="R5 | Alliance Leader")
        if role:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message("❌ Removed **R5 | Alliance Leader**", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Added **R5 | Alliance Leader**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Role not found", ephemeral=True)

    @button(label="R4 | Leadership", style=discord.ButtonStyle.primary, custom_id="role_r4")
    async def r4_button(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name="R4 | Leadership")
        if role:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message("❌ Removed **R4 | Leadership**", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Added **R4 | Leadership**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Role not found", ephemeral=True)

    @button(label="R3 | TC25+", style=discord.ButtonStyle.primary, custom_id="role_r3")
    async def r3_button(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name="R3 | TC25+")
        if role:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message("❌ Removed **R3 | TC25+**", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Added **R3 | TC25+**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Role not found", ephemeral=True)

    @button(label="R2 | TC24-", style=discord.ButtonStyle.primary, custom_id="role_r2")
    async def r2_button(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name="R2 | TC24-")
        if role:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message("❌ Removed **R2 | TC24-**", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Added **R2 | TC24-**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Role not found", ephemeral=True)

    @button(label="R1 | Bear Bait", style=discord.ButtonStyle.primary, custom_id="role_r1")
    async def r1_button(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name="R1 | Bear Bait")
        if role:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message("❌ Removed **R1 | Bear Bait**", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Added **R1 | Bear Bait**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Role not found", ephemeral=True)


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

    # Register persistent views for role panel
    bot.add_view(RolePanelView())

    scheduled_announcements.start()
    daily_tip_task.start()
    timer_check.start()
    event_cycle_reminder.start()
    try:
        guild_obj = discord.Object(id=int(config.get("guild_id", "0")))
        bot.tree.copy_global_to(guild=guild_obj)
        synced = await bot.tree.sync(guild=guild_obj)
        log.info(f"Synced {len(synced)} slash command(s) to guild {config.get('guild_id')}")
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
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await welcome_ch.send(embed=embed)


# =========================================================================
# =========================================================================
#                     MEMBER COMMANDS (Everyone can use)
# =========================================================================
# =========================================================================


# =========================================================================
# /help — Custom help command
# =========================================================================
@bot.hybrid_command(name="help")
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
            "`/event <name>` — Get full guide for any event\n"
            "`/events` — List all available event guides\n"
            "`/heroes <event>` — Quick hero picks for an event\n"
            "`/troops <event>` — Quick troop comp for an event"
        ),
        inline=False,
    )

    embed.add_field(
        name="👤 Profile & Info (Everyone)",
        value=(
            "`/profile` — View your server profile\n"
            "`/setpower <number>` — Set your power level\n"
            "`/setign <name>` — Set your in-game name\n"
            "`/memberinfo [@user]` — View someone's info\n"
            "`/serverinfo` — Server statistics\n"
            "`/leaderboard` — Power leaderboard"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎁 Gift Codes (Everyone)",
        value=(
            "`/codes` — View all active gift codes\n"
            "`/addcode <code> | <rewards>` — Submit a new code\n"
            "`/expirecode <code>` — Mark a code as expired"
        ),
        inline=False,
    )

    embed.add_field(
        name="⏰ Timers & Reminders (Everyone)",
        value=(
            "`/timers` — View active event timers\n"
            "`/countdown <event>` — Quick countdown to next event\n"
            "`/tip` — Get a random Kingshot tip"
        ),
        inline=False,
    )

    embed.add_field(
        name="⚔️ War Commands 🔒",
        value=(
            "`/rally <details>` — Send rally call (R3+)\n"
            "`/warsched <text>` — Post war schedule (R3+)\n"
            "`/settimer <name> | <time>` — Set event timer (R4+)\n"
            "`/deltimer <name>` — Delete a timer (R4+)"
        ),
        inline=False,
    )

    embed.add_field(
        name="🕐 Timezone (Everyone)",
        value=(
            "`/timezone <tz>` — Set your timezone (EST, PST, etc.)\n"
            "`/localtime [time]` — Convert UTC to your local time\n"
            "`/timezone` — View your current timezone"
        ),
        inline=False,
    )

    embed.add_field(
        name="📢 Announcements 🔒",
        value=(
            "`/announce <channel> <msg>` — Send announcement (R4+)\n"
            "`/listannouncements` — View scheduled (R4+)\n"
            "`/toggleannouncement <name>` — Toggle on/off (R4+)"
        ),
        inline=False,
    )

    embed.add_field(
        name="👥 Role Management 🔒",
        value=(
            "`/promote @user RoleName` — Give role (R4+)\n"
            "`/demote @user RoleName` — Remove role (R4+)"
        ),
        inline=False,
    )

    embed.add_field(
        name="🛡️ Moderation 🔒",
        value=(
            "`/kick @user [reason]` — Kick member\n"
            "`/mute @user [minutes]` — Timeout member\n"
            "`/unmute @user` — Remove timeout\n"
            "`/clear [amount]` — Delete messages"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔧 Admin 🔒",
        value="`/setup` — Full server setup (Admin only)",
        inline=False,
    )

    embed.set_footer(text="Use /help in #bot-commands to keep other channels clean!")
    await ctx.send(embed=embed, ephemeral=True)


# =========================================================================
# Autocomplete helpers for slash commands
# =========================================================================
async def event_name_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Autocomplete event names for slash commands."""
    choices = []
    for key, ev in EVENT_GUIDES.items():
        if current.lower() in key or current.lower() in ev["name"].lower():
            choices.append(app_commands.Choice(name=f"{ev['emoji']} {ev['name']}", value=key))
    return choices[:25]


async def timer_name_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Autocomplete active timer names."""
    active = [t for t in war_timers.get("timers", []) if _utc_from_iso(t["time"]) > datetime.now(timezone.utc)]
    choices = []
    for t in active:
        if current.lower() in t["name"].lower():
            choices.append(app_commands.Choice(name=t["name"], value=t["name"]))
    return choices[:25]


async def timezone_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Autocomplete timezone names."""
    common_tzs = [
        ("EST (US Eastern)", "EST"), ("CST (US Central)", "CST"),
        ("MST (US Mountain)", "MST"), ("PST (US Pacific)", "PST"),
        ("GMT (London)", "GMT"), ("CET (Central Europe)", "CET"),
        ("EET (Eastern Europe)", "EET"), ("MSK (Moscow)", "MSK"),
        ("IST (India)", "IST"), ("JST (Japan)", "JST"),
        ("KST (Korea)", "KST"), ("CST-Asia (China)", "CST-Asia"),
        ("SGT (Singapore)", "SGT"), ("AEST (Australia)", "AEST"),
        ("NZST (New Zealand)", "NZST"), ("BRT (Brazil)", "BRT"),
        ("GST (Gulf/Dubai)", "GST"), ("PHT (Philippines)", "PHT"),
        ("HKT (Hong Kong)", "HKT"), ("SAST (South Africa)", "SAST"),
    ]
    if not current:
        return [app_commands.Choice(name=name, value=val) for name, val in common_tzs[:25]]
    choices = [
        app_commands.Choice(name=name, value=val)
        for name, val in common_tzs if current.lower() in name.lower() or current.lower() in val.lower()
    ]
    # Also search IANA names if user types something specific
    if len(choices) < 10 and len(current) >= 3:
        for tz in sorted(available_timezones()):
            if current.lower() in tz.lower():
                city = tz.split("/")[-1].replace("_", " ")
                choices.append(app_commands.Choice(name=f"{city} ({tz})", value=tz))
                if len(choices) >= 25:
                    break
    return choices[:25]


async def announcement_name_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Autocomplete announcement names."""
    cfg = load_config()
    choices = []
    for ann in cfg.get("scheduled_announcements", []):
        name = ann["name"]
        if current.lower() in name.lower():
            status = "ON" if ann.get("enabled") else "OFF"
            choices.append(app_commands.Choice(name=f"{name} [{status}]", value=name))
    return choices[:25]


# =========================================================================
# /events — List all available event guides (with dropdown)
# =========================================================================
@bot.hybrid_command(name="events")
async def list_events(ctx: commands.Context):
    """List all available event guides with interactive dropdown."""
    embed = discord.Embed(
        title="📅 Kingshot Event Guides",
        description="Select an event from the dropdown to view the full guide!",
        color=discord.Color.blue(),
    )

    event_list = ""
    for key, ev in EVENT_GUIDES.items():
        event_list += f"{ev['emoji']} {ev['name']}\n"

    embed.add_field(name="Available Events", value=event_list, inline=False)
    embed.set_footer(text="Tip: Use /heroes <event> or /troops <event> for quick lookups")
    await ctx.send(embed=embed, view=EventSelectView(EVENT_GUIDES))


# =========================================================================
# /event <name> — Full event guide
# =========================================================================
@bot.hybrid_command(name="event")
@app_commands.autocomplete(name=event_name_autocomplete)
@app_commands.describe(name="The event to get a guide for")
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
    embed.set_footer(text=f"Use /heroes {key} or /troops {key} for quick lookups")
    await ctx.send(embed=embed)


# =========================================================================
# /heroes <event> — Quick hero recommendation
# =========================================================================
@bot.hybrid_command(name="heroes")
@app_commands.autocomplete(name=event_name_autocomplete)
@app_commands.describe(name="The event to get hero picks for")
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
    await ctx.send(f"❌ Event not found. Use `/events` to see all options.")


# =========================================================================
# /troops <event> — Quick troop comp
# =========================================================================
@bot.hybrid_command(name="troops")
@app_commands.autocomplete(name=event_name_autocomplete)
@app_commands.describe(name="The event to get troop composition for")
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
    await ctx.send(f"❌ Event not found. Use `/events` to see all options.")


# =========================================================================
# /profile — View your profile
# =========================================================================
@bot.hybrid_command(name="profile")
@app_commands.describe(member="The member to view (leave empty for yourself)")
async def profile(ctx: commands.Context, member: discord.Member = None):
    """View your profile or another member's profile."""
    viewing_self = member is None
    member = member or ctx.author
    uid = str(member.id)
    data = user_profiles.get(uid, {})

    embed = discord.Embed(
        title=f"👤 {member.display_name}'s Profile",
        color=member.top_role.color if member.top_role.color != discord.Color.default() else discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🎮 In-Game Name", value=data.get("ign", "*Not set* — use `/setign`"), inline=True)
    embed.add_field(name="⚡ Power", value=f"{data.get('power', 0):,}" if data.get("power") else "*Not set* — use `/setpower`", inline=True)
    embed.add_field(name="🏷️ Roles", value=", ".join(r.name for r in member.roles if r.name != "@everyone") or "None", inline=False)
    embed.add_field(name="📅 Joined", value=member.joined_at.strftime("%b %d, %Y") if member.joined_at else "Unknown", inline=True)
    await ctx.send(embed=embed, ephemeral=viewing_self)


# =========================================================================
# /setpower — Set your power level
# =========================================================================
@bot.hybrid_command(name="setpower")
@app_commands.describe(power="Your power level (e.g. 25m, 5000000, 1.2b)")
async def set_power(ctx: commands.Context, power: str):
    """Set your power level. Usage: /setpower 25000000 or /setpower 25m"""
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
        await ctx.send("❌ Invalid power value. Examples: `/setpower 25m`, `/setpower 5000000`")
        return

    uid = str(ctx.author.id)
    if uid not in user_profiles:
        user_profiles[uid] = {}

    # Track power history
    old_power = user_profiles[uid].get("power", 0)
    if uid not in power_history:
        power_history[uid] = []
    power_history[uid].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "old_power": old_power,
        "new_power": power_val,
    })
    save_data("power_history", power_history)

    user_profiles[uid]["power"] = power_val
    save_data("profiles", user_profiles)
    await ctx.send(f"✅ Power set to **{power_val:,}**!")


# =========================================================================
# /setign — Set your in-game name
# =========================================================================
@bot.hybrid_command(name="setign")
@app_commands.describe(ign="Your in-game name")
async def set_ign(ctx: commands.Context, *, ign: str):
    """Set your in-game name. Usage: /setign MyPlayerName"""
    uid = str(ctx.author.id)
    if uid not in user_profiles:
        user_profiles[uid] = {}
    user_profiles[uid]["ign"] = ign.strip()
    save_data("profiles", user_profiles)
    await ctx.send(f"✅ In-game name set to **{ign.strip()}**!")


# =========================================================================
# /leaderboard — Power leaderboard
# =========================================================================
@bot.hybrid_command(name="leaderboard", aliases=["lb", "top"])
async def leaderboard(ctx: commands.Context):
    """Show the alliance power leaderboard (paginated)."""
    ranked = sorted(
        [(uid, data) for uid, data in user_profiles.items() if data.get("power", 0) > 0],
        key=lambda x: x[1]["power"],
        reverse=True,
    )

    if not ranked:
        await ctx.send("📊 No one has set their power yet! Use `/setpower <number>` to register.")
        return

    # Create embeds for pagination (10 entries per page)
    embeds = []
    medals = ["🥇", "🥈", "🥉"]
    per_page = 10

    for page_num in range(0, len(ranked), per_page):
        page_data = ranked[page_num:page_num + per_page]
        lines = []
        for i, (uid, data) in enumerate(page_data):
            actual_rank = page_num + i
            medal = medals[actual_rank] if actual_rank < 3 else f"**{actual_rank + 1}.**"
            name = data.get("ign", f"<@{uid}>")
            power = f"{data['power']:,}"
            lines.append(f"{medal} {name} — ⚡ {power}")

        embed = discord.Embed(
            title="🏆 Alliance Power Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        page_num_display = (page_num // per_page) + 1
        total_pages = (len(ranked) + per_page - 1) // per_page
        embed.set_footer(text=f"Page {page_num_display}/{total_pages} | Total: {len(ranked)} members")
        embeds.append(embed)

    if len(embeds) == 1:
        await ctx.send(embed=embeds[0])
    else:
        view = PaginatorView(embeds)
        await ctx.send(embed=embeds[0], view=view)


# =========================================================================
# /serverinfo — Server stats
# =========================================================================
@bot.hybrid_command(name="serverinfo", aliases=["server"])
async def server_info(ctx: commands.Context):
    """Show server statistics."""
    guild = ctx.guild
    embed = discord.Embed(
        title=f"📊 {guild.name} — Server Info",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
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
# /codes — View gift codes
# =========================================================================
@bot.hybrid_command(name="codes")
async def view_codes(ctx: commands.Context):
    """View all active gift codes (ephemeral, paginated)."""
    active = [c for c in gift_codes.get("codes", []) if not c.get("expired")]

    if not active:
        await ctx.send("🎁 No active gift codes right now. Use `/addcode` when you find one!", ephemeral=True)
        return

    # Create embeds for pagination (5 codes per page)
    embeds = []
    per_page = 5

    for page_num in range(0, len(active), per_page):
        page_data = active[page_num:page_num + per_page]
        embed = discord.Embed(
            title="🎁 Active Gift Codes",
            color=discord.Color.from_str("#FF69B4"),
            timestamp=datetime.now(timezone.utc),
        )

        for code in page_data:
            embed.add_field(
                name=f"📋 `{code['code']}`",
                value=f"📦 {code.get('rewards', 'Unknown rewards')}\n👤 Added by {code.get('added_by', 'Unknown')}",
                inline=False,
            )

        page_num_display = (page_num // per_page) + 1
        total_pages = (len(active) + per_page - 1) // per_page
        embed.set_footer(text=f"Page {page_num_display}/{total_pages} | Redeem: Settings → Gift Code | Use /addcode to submit")
        embeds.append(embed)

    if len(embeds) == 1:
        await ctx.send(embed=embeds[0], ephemeral=True)
    else:
        view = PaginatorView(embeds)
        await ctx.send(embed=embeds[0], view=view, ephemeral=True)


# =========================================================================
# /addcode — Submit a gift code
# =========================================================================
@bot.hybrid_command(name="addcode")
@app_commands.describe(args="Code and rewards: CODE123 | 500 gems, 2 speedups")
async def add_code(ctx: commands.Context, *, args: str):
    """Submit a new gift code. Usage: /addcode CODE123 | 500 gems, 2 speedups"""
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
        "added_at": datetime.now(timezone.utc).isoformat(),
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
# /expirecode — Mark a code as expired
# =========================================================================
@bot.hybrid_command(name="expirecode")
@app_commands.describe(code="The gift code to mark as expired")
async def expire_code(ctx: commands.Context, *, code: str):
    """Mark a gift code as expired. Usage: /expirecode CODE123"""
    code = code.strip().upper()
    for c in gift_codes.get("codes", []):
        if c["code"] == code:
            c["expired"] = True
            save_data("gift_codes", gift_codes)
            await ctx.send(f"✅ Code `{code}` marked as expired.")
            return
    await ctx.send(f"❌ Code `{code}` not found.")


# =========================================================================
# /tip — Random Kingshot tip
# =========================================================================
@bot.hybrid_command(name="tip")
async def random_tip(ctx: commands.Context):
    """Get a random Kingshot pro tip (ephemeral)."""
    tip = random.choice(DEFAULT_TIPS)
    embed = discord.Embed(description=tip, color=discord.Color.green())
    embed.set_footer(text="Use /events for full event guides")
    await ctx.send(embed=embed, ephemeral=True)


# =========================================================================
# /timers — View active timers
# =========================================================================
@bot.hybrid_command(name="timers", aliases=["timer"])
async def view_timers(ctx: commands.Context):
    """View active event timers (ephemeral)."""
    active = [t for t in war_timers.get("timers", []) if _utc_from_iso(t["time"]) > datetime.now(timezone.utc)]

    if not active:
        await ctx.send("⏰ No active timers. Officers can set them with `/settimer`", ephemeral=True)
        return

    embed = discord.Embed(title="⏰ Active Event Timers", color=discord.Color.orange())
    for t in sorted(active, key=lambda x: x["time"]):
        target = _utc_from_iso(t["time"])
        delta = target - datetime.now(timezone.utc)
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        embed.add_field(
            name=t["name"],
            value=f"⏱️ **{hours}h {minutes}m** remaining\n📅 {target.strftime('%b %d, %I:%M %p')} UTC",
            inline=False,
        )
    await ctx.send(embed=embed, ephemeral=True)


# =========================================================================
# /settimer — Set an event timer (R4+)
# =========================================================================
@bot.hybrid_command(name="settimer")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
@app_commands.describe(args="Event name | UTC datetime (e.g. Swordland | 2026-03-15 20:00)")
async def set_timer(ctx: commands.Context, *, args: str):
    """Set an event timer (all times in UTC). Usage: /settimer Swordland Showdown | 2026-03-15 20:00"""
    parts = args.split("|", 1)
    if len(parts) != 2:
        await ctx.send("❌ Usage: `/settimer Event Name | YYYY-MM-DD HH:MM` (all times are UTC)")
        return

    name = parts[0].strip()
    try:
        target = _utc_from_iso(parts[1].strip())
    except ValueError:
        await ctx.send("❌ Invalid date format. Use: `YYYY-MM-DD HH:MM` (e.g., `2026-03-15 20:00`)")
        return

    war_timers.setdefault("timers", []).append({
        "name": name,
        "time": target.isoformat(),
        "set_by": ctx.author.display_name,
    })
    save_data("war_timers", war_timers)

    delta = target - datetime.now(timezone.utc)
    hours = int(delta.total_seconds() // 3600)
    await ctx.send(f"✅ Timer set: **{name}** in ~{hours} hours ({target.strftime('%b %d, %I:%M %p')} UTC)")


# =========================================================================
# /deltimer — Delete a timer (R4+)
# =========================================================================
@bot.hybrid_command(name="deltimer")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
@app_commands.autocomplete(name=timer_name_autocomplete)
@app_commands.describe(name="The timer to delete")
async def del_timer(ctx: commands.Context, *, name: str):
    """Delete an event timer. Usage: /deltimer Swordland Showdown"""
    timers = war_timers.get("timers", [])
    war_timers["timers"] = [t for t in timers if t["name"].lower() != name.lower()]
    save_data("war_timers", war_timers)
    await ctx.send(f"✅ Timer `{name}` deleted.")


# =========================================================================
# /timezone — Set or view your local timezone
# =========================================================================
# Common timezone aliases for easier input
TZ_ALIASES = {
    "est": "America/New_York", "edt": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago",
    "mst": "America/Denver", "mdt": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "gmt": "Europe/London", "bst": "Europe/London",
    "cet": "Europe/Berlin", "cest": "Europe/Berlin",
    "eet": "Europe/Bucharest", "eest": "Europe/Bucharest",
    "ist": "Asia/Kolkata",
    "jst": "Asia/Tokyo",
    "kst": "Asia/Seoul",
    "cst_asia": "Asia/Shanghai", "cst-asia": "Asia/Shanghai",
    "aest": "Australia/Sydney", "aedt": "Australia/Sydney",
    "nzst": "Pacific/Auckland", "nzdt": "Pacific/Auckland",
    "brt": "America/Sao_Paulo", "brst": "America/Sao_Paulo",
    "msk": "Europe/Moscow",
    "sgt": "Asia/Singapore",
    "hkt": "Asia/Hong_Kong",
    "pht": "Asia/Manila",
    "wib": "Asia/Jakarta",
    "gulf": "Asia/Dubai", "gst": "Asia/Dubai",
    "ast": "Asia/Riyadh",
    "cat": "Africa/Johannesburg", "sast": "Africa/Johannesburg",
}


def _resolve_timezone(tz_input: str) -> Optional[str]:
    """Resolve a timezone input to a valid IANA timezone name."""
    tz_lower = tz_input.strip().lower().replace(" ", "_")

    # Check aliases first
    if tz_lower in TZ_ALIASES:
        return TZ_ALIASES[tz_lower]

    # Check for UTC offset format like UTC+5, UTC-3, GMT+8
    import re
    offset_match = re.match(r"^(?:utc|gmt)\s*([+-])?\s*(\d{1,2})(?::(\d{2}))?$", tz_lower)
    if offset_match:
        sign = offset_match.group(1) or "+"
        hours = int(offset_match.group(2))
        mins = int(offset_match.group(3) or 0)
        # Map common UTC offsets to IANA names
        total_offset = hours * 60 + mins
        if sign == "-":
            total_offset = -total_offset
        # Try Etc/GMT (note: Etc/GMT signs are INVERTED)
        if mins == 0:
            etc_name = f"Etc/GMT{'+' if total_offset <= 0 else '-'}{abs(hours)}"
            if etc_name in available_timezones():
                return etc_name
        return None

    # Direct IANA name match (case-insensitive search)
    all_tzs = available_timezones()
    for tz in all_tzs:
        if tz.lower() == tz_lower:
            return tz

    # Partial match — search city names
    matches = [tz for tz in all_tzs if tz_lower in tz.lower()]
    if len(matches) == 1:
        return matches[0]

    return None


@bot.hybrid_command(name="timezone", aliases=["tz", "settz"])
@app_commands.autocomplete(tz_input=timezone_autocomplete)
@app_commands.describe(tz_input="Your timezone (e.g. EST, America/New_York, UTC+5)")
async def set_timezone(ctx: commands.Context, *, tz_input: str = None):
    """Set or view your timezone. Usage: /timezone EST or /timezone America/New_York"""
    uid = str(ctx.author.id)

    if not tz_input:
        # Show current timezone (ephemeral)
        current = user_timezones.get(uid)
        if current:
            now_utc = datetime.now(timezone.utc)
            now_local = now_utc.astimezone(ZoneInfo(current))
            await ctx.send(
                f"🕐 Your timezone is set to **{current}**\n"
                f"Current UTC time: **{now_utc.strftime('%b %d, %I:%M %p')} UTC**\n"
                f"Your local time: **{now_local.strftime('%b %d, %I:%M %p %Z')}**\n\n"
                f"To change it: `/timezone <timezone>`",
                ephemeral=True
            )
        else:
            await ctx.send(
                "🕐 You haven't set a timezone yet. All times are displayed in **UTC**.\n\n"
                "Set yours with: `/timezone <timezone>`\n"
                "Examples: `/timezone EST`, `/timezone America/New_York`, `/timezone UTC+5`\n\n"
                "Common codes: `EST`, `CST`, `PST`, `GMT`, `CET`, `IST`, `JST`, `KST`, `AEST`",
                ephemeral=True
            )
        return

    resolved = _resolve_timezone(tz_input)
    if not resolved:
        await ctx.send(
            f"❌ Could not find timezone `{tz_input}`.\n\n"
            "Try one of these formats:\n"
            "• Abbreviation: `EST`, `PST`, `CET`, `IST`, `JST`, `KST`\n"
            "• IANA name: `America/New_York`, `Europe/London`, `Asia/Tokyo`\n"
            "• UTC offset: `UTC+5`, `UTC-3`, `GMT+8`"
        )
        return

    user_timezones[uid] = resolved
    save_data("user_timezones", user_timezones)

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(resolved))
    await ctx.send(
        f"✅ Timezone set to **{resolved}**\n"
        f"Current UTC time: **{now_utc.strftime('%b %d, %I:%M %p')} UTC**\n"
        f"Your local time: **{now_local.strftime('%b %d, %I:%M %p %Z')}**"
    )


@bot.hybrid_command(name="localtime", aliases=["lt", "convert"])
@app_commands.describe(utc_time_str="UTC time to convert (e.g. 2026-03-15 20:00). Leave empty for current time.")
async def local_time(ctx: commands.Context, *, utc_time_str: str = None):
    """Convert a UTC time to your local timezone (ephemeral). Usage: /localtime 2026-03-15 20:00"""
    uid = str(ctx.author.id)
    user_tz = user_timezones.get(uid)

    if not user_tz:
        await ctx.send(
            "❌ You haven't set your timezone yet!\n"
            "Set it first with: `/timezone EST` (or your timezone)\n\n"
            "Common codes: `EST`, `CST`, `PST`, `GMT`, `CET`, `IST`, `JST`, `KST`, `AEST`",
            ephemeral=True
        )
        return

    if not utc_time_str:
        # Show current time in both UTC and local
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(ZoneInfo(user_tz))
        # Also show all active timers in local time
        active = [t for t in war_timers.get("timers", []) if _utc_from_iso(t["time"]) > datetime.now(timezone.utc)]
        embed = discord.Embed(
            title="🕐 Time Conversion",
            color=discord.Color.teal(),
        )
        embed.add_field(name="UTC Now", value=f"**{now_utc.strftime('%b %d, %I:%M %p')} UTC**", inline=True)
        embed.add_field(name=f"Your Time ({user_tz.split('/')[-1]})", value=f"**{now_local.strftime('%b %d, %I:%M %p %Z')}**", inline=True)

        if active:
            timer_lines = []
            for t in sorted(active, key=lambda x: x["time"])[:5]:
                target_utc = _utc_from_iso(t["time"]).replace(tzinfo=timezone.utc)
                target_local = target_utc.astimezone(ZoneInfo(user_tz))
                timer_lines.append(
                    f"**{t['name']}**\n"
                    f"  UTC: {target_utc.strftime('%b %d, %I:%M %p')} UTC\n"
                    f"  You: {target_local.strftime('%b %d, %I:%M %p %Z')}"
                )
            embed.add_field(name="⏰ Active Timers (Local Time)", value="\n".join(timer_lines), inline=False)

        embed.set_footer(text=f"Your timezone: {user_tz} | Change with /timezone")
        await ctx.send(embed=embed, ephemeral=True)
        return

    # Parse the provided UTC time
    try:
        target_utc = datetime.fromisoformat(utc_time_str.strip()).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            target_utc = datetime.strptime(utc_time_str.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            await ctx.send("❌ Invalid time format. Use: `/localtime 2026-03-15 20:00`", ephemeral=True)
            return

    target_local = target_utc.astimezone(ZoneInfo(user_tz))
    await ctx.send(
        f"🕐 **Time Conversion:**\n"
        f"UTC: **{target_utc.strftime('%b %d, %Y — %I:%M %p')} UTC**\n"
        f"Your time ({user_tz.split('/')[-1]}): **{target_local.strftime('%b %d, %Y — %I:%M %p %Z')}**",
        ephemeral=True
    )


# =========================================================================
# /memberinfo — Show member info
# =========================================================================
@bot.hybrid_command(name="memberinfo")
@app_commands.describe(member="The member to view info for")
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
# /setup — Full server setup (Admin only)
# =========================================================================
@bot.hybrid_command(name="setup")
@app_commands.default_permissions(administrator=True)
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
    await ctx.send("🎉 **Setup complete!** Use `/help` to see all available commands.")


# =========================================================================
# /announce — Send announcement (R4+)
# =========================================================================
@bot.hybrid_command(name="announce")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
@app_commands.describe(channel_name="Channel to post in", message="Announcement text")
async def announce(ctx: commands.Context, channel_name: str, *, message: str):
    """Send an announcement. Usage: /announce announcements Your message here"""
    channel = discord.utils.get(ctx.guild.text_channels, name=channel_name)
    if not channel:
        await ctx.send(f"❌ Channel `#{channel_name}` not found.")
        return

    embed = discord.Embed(
        title="📢 Announcement",
        description=message,
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Posted by {ctx.author.display_name}")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Announcement sent to #{channel_name}")


# =========================================================================
# /rally — Rally call (R3+)
# =========================================================================
@bot.hybrid_command(name="rally")
@app_commands.default_permissions(manage_messages=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
@app_commands.describe(details="Rally details (target, troops, etc.)")
async def rally_call(ctx: commands.Context, *, details: str = "Rally up! Check war room."):
    """Send an urgent rally call. Usage: /rally Target: Player123 — send T10 troops"""
    rally_ch = discord.utils.get(ctx.guild.text_channels, name="rally-calls")
    target_ch = rally_ch or ctx.channel

    embed = discord.Embed(
        title="🚨 RALLY CALL 🚨",
        description=details,
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Called by {ctx.author.display_name}")
    await target_ch.send("@everyone", embed=embed)
    if target_ch != ctx.channel:
        await ctx.send(f"✅ Rally call sent to #{target_ch.name}")


# =========================================================================
# /warsched — War schedule (R3+)
# =========================================================================
@bot.hybrid_command(name="warsched")
@app_commands.default_permissions(manage_messages=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
@app_commands.describe(schedule_text="War schedule details")
async def war_schedule(ctx: commands.Context, *, schedule_text: str):
    """Post a war schedule. Usage: /warsched Wednesday 8PM — Castle Siege"""
    sched_ch = discord.utils.get(ctx.guild.text_channels, name="war-schedule")
    target_ch = sched_ch or ctx.channel

    embed = discord.Embed(
        title="🗓️ War Schedule",
        description=schedule_text.replace("\\n", "\n"),
        color=discord.Color.dark_red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Updated by {ctx.author.display_name}")
    await target_ch.send(embed=embed)
    await ctx.send(f"✅ War schedule posted to #{target_ch.name}")


# =========================================================================
# /promote / /demote — Role management (R4+)
# =========================================================================
@bot.hybrid_command(name="promote")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
@app_commands.describe(member="The member to promote", role_name="The role to assign")
async def promote(ctx: commands.Context, member: discord.Member, *, role_name: str):
    """Promote a member. Usage: /promote @user Member"""
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if not role:
        await ctx.send(f"❌ Role `{role_name}` not found.")
        return
    await member.add_roles(role)
    await ctx.send(f"✅ {member.display_name} promoted to **{role_name}**!")


@bot.hybrid_command(name="demote")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
@app_commands.describe(member="The member to demote", role_name="The role to remove")
async def demote(ctx: commands.Context, member: discord.Member, *, role_name: str):
    """Remove a role. Usage: /demote @user Officer"""
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if not role:
        await ctx.send(f"❌ Role `{role_name}` not found.")
        return
    await member.remove_roles(role)
    await ctx.send(f"✅ {member.display_name} removed from **{role_name}**.")


# =========================================================================
# Moderation commands
# =========================================================================
@bot.hybrid_command(name="kick")
@app_commands.default_permissions(kick_members=True)
@commands.has_permissions(kick_members=True)
@app_commands.describe(member="The member to kick", reason="Reason for kicking")
async def kick_member(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
    """Kick a member."""
    await member.kick(reason=reason)
    await ctx.send(f"👢 {member.display_name} kicked. Reason: {reason}")


@bot.hybrid_command(name="mute")
@app_commands.default_permissions(moderate_members=True)
@commands.has_permissions(manage_roles=True)
@app_commands.describe(member="The member to mute", minutes="Duration in minutes (default: 10)")
async def mute_member(ctx: commands.Context, member: discord.Member, minutes: int = 10):
    """Timeout a member. Usage: /mute @user 30"""
    await member.timeout(timedelta(minutes=minutes), reason=f"Muted by {ctx.author.display_name}")
    await ctx.send(f"🔇 {member.display_name} muted for {minutes} minutes.")


@bot.hybrid_command(name="unmute")
@app_commands.default_permissions(moderate_members=True)
@commands.has_permissions(manage_roles=True)
@app_commands.describe(member="The member to unmute")
async def unmute_member(ctx: commands.Context, member: discord.Member):
    """Remove timeout."""
    await member.timeout(None, reason=f"Unmuted by {ctx.author.display_name}")
    await ctx.send(f"🔊 {member.display_name} unmuted.")


@bot.hybrid_command(name="clear")
@app_commands.default_permissions(manage_messages=True)
@commands.has_permissions(manage_messages=True)
@app_commands.describe(amount="Number of messages to delete (max 100)")
async def clear_messages(ctx: commands.Context, amount: int = 10):
    """Delete messages. Usage: /clear 25"""
    if amount > 100:
        await ctx.send("❌ Max 100 messages at a time.")
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f"🗑️ Deleted {len(deleted) - 1} messages.")
    await asyncio.sleep(3)
    await msg.delete()


# =========================================================================
# /listannouncements / /toggleannouncement
# =========================================================================
@bot.hybrid_command(name="listannouncements")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
async def list_announcements(ctx: commands.Context):
    """List scheduled announcements (paginated)."""
    cfg = load_config()
    announcements = cfg.get("scheduled_announcements", [])
    if not announcements:
        await ctx.send("No scheduled announcements configured.")
        return

    # Create embeds for pagination (5 per page)
    embeds = []
    per_page = 5

    for page_num in range(0, len(announcements), per_page):
        page_data = announcements[page_num:page_num + per_page]
        embed = discord.Embed(title="📋 Scheduled Announcements", color=discord.Color.blue())
        for ann in page_data:
            status = "✅ Enabled" if ann.get("enabled") else "❌ Disabled"
            embed.add_field(
                name=f"{ann['name']} — {status}",
                value=f"**Channel:** #{ann['channel']}\n**Schedule:** `{ann['cron']}`\n**Message:** {ann['message'][:100]}...",
                inline=False,
            )
        page_num_display = (page_num // per_page) + 1
        total_pages = (len(announcements) + per_page - 1) // per_page
        embed.set_footer(text=f"Page {page_num_display}/{total_pages}")
        embeds.append(embed)

    if len(embeds) == 1:
        await ctx.send(embed=embeds[0])
    else:
        view = PaginatorView(embeds)
        await ctx.send(embed=embeds[0], view=view)


@bot.hybrid_command(name="toggleannouncement")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
@app_commands.autocomplete(name=announcement_name_autocomplete)
@app_commands.describe(name="The announcement to toggle on/off")
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
# Event Cycle Commands
# =========================================================================
@bot.hybrid_command(name="nextevent", aliases=["next", "upcoming"])
async def next_event_cmd(ctx: commands.Context):
    """Show the next upcoming events."""
    upcoming = get_upcoming_events(days_ahead=7)
    if not upcoming:
        await ctx.send("📅 No upcoming events found in the next 7 days.")
        return

    embed = discord.Embed(
        title="📅 Upcoming Events (Next 7 Days)",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )
    for days_until, start_date, ev in upcoming[:10]:
        if days_until == 0:
            timing = "🔴 **Active NOW**"
        elif days_until == 1:
            timing = "⏰ **Tomorrow**"
        else:
            timing = f"📆 In **{days_until} days** ({start_date.strftime('%a %m/%d')})"
        duration = ev.get("duration_days", 1)
        embed.add_field(
            name=f"{ev['emoji']} {ev['name']}",
            value=f"{timing}\nDuration: {duration} day{'s' if duration != 1 else ''} | Type: {ev.get('type', 'event').title()}",
            inline=False,
        )
    embed.set_footer(text="Use /schedule for the full cycle | /setanchor to adjust cycle")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="schedule", aliases=["cycle", "eventcycle"])
async def show_schedule(ctx: commands.Context):
    """Show the full 4-week event cycle schedule."""
    cycle = load_event_cycle()
    anchor = datetime.strptime(cycle["cycle_anchor"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    cycle_len = cycle.get("cycle_length_days", 28)
    today_cycle_day = get_cycle_day()
    now = datetime.now(timezone.utc)

    embed = discord.Embed(
        title="📋 4-Week Event Cycle",
        description=f"Cycle anchor: {cycle['cycle_anchor']} | Today: Day {today_cycle_day + 1}/28",
        color=discord.Color.purple(),
    )

    # Group events by week
    for week in range(4):
        week_events = []
        for ev in cycle.get("events", []):
            if ev.get("recurring_every_days"):
                continue  # Skip recurring for the overview
            start = ev["cycle_day_start"]
            if week * 7 <= start < (week + 1) * 7:
                day_in_week = start - (week * 7)
                days_label = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                start_date = anchor + timedelta(days=start)
                active = "🔴 " if today_cycle_day == start else ""
                week_events.append(
                    f"{active}{ev['emoji']} **{ev['name']}** — Day {start + 1} ({ev.get('duration_days', 1)}d)"
                )

        if week_events:
            embed.add_field(
                name=f"{'▶️' if week * 7 <= today_cycle_day < (week + 1) * 7 else '📅'} Week {week + 1}",
                value="\n".join(week_events) or "No events",
                inline=False,
            )

    # Add recurring events
    recurring = [ev for ev in cycle.get("events", []) if ev.get("recurring_every_days")]
    if recurring:
        rec_text = "\n".join(f"{ev['emoji']} **{ev['name']}** — every {ev['recurring_every_days']} days" for ev in recurring)
        embed.add_field(name="🔄 Recurring", value=rec_text, inline=False)

    embed.set_footer(text="Officers: /setanchor YYYY-MM-DD to adjust cycle start")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="today", aliases=["active", "now"])
async def today_events(ctx: commands.Context):
    """Show events active right now."""
    active = get_active_events()
    if not active:
        await ctx.send("📅 No events are active right now.")
        return

    embed = discord.Embed(
        title="🔴 Active Events Right Now",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    # Discord embed total limit is 6000 chars. Show brief summary per event.
    for ev in active:
        reminder = ev.get("reminder", "Event is active!")
        # Extract just the first line/sentence as a brief summary
        brief = reminder.split("\n")[0]
        if len(brief) > 200:
            brief = brief[:197] + "..."
        brief += f"\n*Use `/tips {ev['name'].lower()}` for full strategy guide*"
        embed.add_field(
            name=f"{ev['emoji']} {ev['name']} ({ev.get('type', 'event').title()})",
            value=brief,
            inline=False,
        )
    embed.set_footer(text=f"Cycle Day {get_cycle_day() + 1}/28 | Use /tips <event> for full guide")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="tips", aliases=["eventtips", "strategy"])
@app_commands.autocomplete(event_name=event_name_autocomplete)
@app_commands.describe(event_name="The event to get strategy tips for")
async def event_tips(ctx: commands.Context, *, event_name: str = None):
    """Show full strategy & prep tips for an event. Usage: /tips <event name>"""
    if not event_name:
        await ctx.send("❓ Usage: `/tips <event name>` — e.g. `/tips bear hunt`, `/tips strongest governor`\n"
                        "Use `/today` to see active events or `/schedule` for the full cycle.")
        return

    cycle = load_event_cycle()
    search = event_name.lower().strip()
    matches = []
    for ev in cycle.get("events", []):
        if search in ev["name"].lower() or ev["name"].lower() in search:
            matches.append(ev)

    if not matches:
        # Fuzzy: check if any word matches
        for ev in cycle.get("events", []):
            name_words = ev["name"].lower().split()
            search_words = search.split()
            if any(sw in name_words for sw in search_words):
                matches.append(ev)

    if not matches:
        event_names = ", ".join(f"`{ev['name']}`" for ev in cycle.get("events", []))
        await ctx.send(f"❌ No event found matching **{event_name}**.\n\nAvailable events: {event_names}")
        return

    for ev in matches[:3]:
        reminder = ev.get("reminder", "No tips available for this event.")
        # Split into chunks if over 4096 chars (embed description limit)
        if len(reminder) <= 4096:
            embed = discord.Embed(
                title=f"{ev['emoji']} {ev['name']} — Strategy & Prep Guide",
                description=reminder,
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Duration", value=f"{ev.get('duration_days', 1)} day(s)", inline=True)
            embed.add_field(name="Type", value=ev.get("type", "event").title(), inline=True)
            embed.set_footer(text="🤖 Kingshot Bot | Use /today for active events")
            await ctx.send(embed=embed)
        else:
            # Very long tip — send as plain text
            await ctx.send(f"**{ev['emoji']} {ev['name']} — Strategy & Prep Guide**\n\n{reminder[:2000]}")


@bot.hybrid_command(name="setanchor")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R4 | Leadership", "R5 | Alliance Leader")
async def set_anchor(ctx: commands.Context, date_str: str):
    """Set the cycle anchor date. Usage: /setanchor 2026-03-06"""
    try:
        new_anchor = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        await ctx.send("❌ Invalid date format. Use: `/setanchor YYYY-MM-DD`")
        return

    cycle = load_event_cycle()
    cycle["cycle_anchor"] = date_str
    with open(EVENT_CYCLE_PATH, "w") as f:
        json.dump(cycle, f, indent=2)

    new_day = get_cycle_day()
    await ctx.send(f"✅ Cycle anchor updated to **{date_str}**. Today is now Cycle Day **{new_day + 1}/28**.")


# =========================================================================
# /warsignup — Create war signup (R4+)
# =========================================================================
@bot.hybrid_command(name="warsignup")
@app_commands.default_permissions(manage_guild=True)
@commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
@app_commands.describe(event_name="The event to create signup for")
async def war_signup(ctx: commands.Context, *, event_name: str):
    """Create a war signup embed with attendance tracking. Usage: /warsignup Swordland Showdown"""
    embed = discord.Embed(
        title=f"🚨 {event_name} Signup",
        description="Click the buttons below to sign up for this event!",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="✅ Confirmed", value="0", inline=True)
    embed.add_field(name="❌ Declined", value="0", inline=True)
    embed.add_field(name="❓ Maybe", value="0", inline=True)
    embed.set_footer(text=f"Created by {ctx.author.display_name}")

    view = WarSignupView(event_name)
    msg = await ctx.send(embed=embed, view=view)

    # Store signup in data
    war_signups["signups"].append({
        "event_name": event_name,
        "message_id": msg.id,
        "channel_id": ctx.channel.id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "confirmed": [],
        "declined": [],
        "maybe": [],
    })
    save_data("war_signups", war_signups)


# =========================================================================
# /attendance — Show attendance for past signups
# =========================================================================
@bot.hybrid_command(name="attendance")
async def show_attendance(ctx: commands.Context, event_name: str = None):
    """Show attendance stats for a war signup event."""
    signups = war_signups.get("signups", [])
    if not signups:
        await ctx.send("📊 No war signups found.")
        return

    if event_name:
        signup = next((s for s in signups if s["event_name"].lower() == event_name.lower()), None)
        if not signup:
            await ctx.send(f"❌ Event `{event_name}` not found in signups.")
            return
    else:
        # Show most recent
        signup = signups[-1] if signups else None
        if not signup:
            await ctx.send("📊 No war signups found.")
            return

    embed = discord.Embed(
        title=f"📊 Attendance: {signup['event_name']}",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="✅ Confirmed", value=str(len(signup.get("confirmed", []))), inline=True)
    embed.add_field(name="❌ Declined", value=str(len(signup.get("declined", []))), inline=True)
    embed.add_field(name="❓ Maybe", value=str(len(signup.get("maybe", []))), inline=True)
    total = len(signup.get("confirmed", [])) + len(signup.get("declined", [])) + len(signup.get("maybe", []))
    embed.add_field(name="Total Signups", value=str(total), inline=False)
    await ctx.send(embed=embed)


# =========================================================================
# /powerhistory — Show power change history
# =========================================================================
@bot.hybrid_command(name="powerhistory")
async def power_history_cmd(ctx: commands.Context, member: discord.Member = None):
    """Show power change history for a user."""
    member = member or ctx.author
    uid = str(member.id)
    history = power_history.get(uid, [])

    if not history:
        await ctx.send(f"📈 No power history found for {member.display_name}.")
        return

    embed = discord.Embed(
        title=f"📈 Power History: {member.display_name}",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )

    # Show last 10 entries
    for entry in history[-10:]:
        ts = datetime.fromisoformat(entry["timestamp"]).strftime("%b %d, %Y")
        old = f"{entry['old_power']:,}"
        new = f"{entry['new_power']:,}"
        change = entry["new_power"] - entry["old_power"]
        change_str = f"+{change:,}" if change >= 0 else f"{change:,}"
        embed.add_field(
            name=f"📅 {ts}",
            value=f"{old} → {new} ({change_str})",
            inline=False,
        )

    await ctx.send(embed=embed)


# =========================================================================
# /countdown — Live countdown to next event
# =========================================================================
@bot.hybrid_command(name="countdown")
@app_commands.autocomplete(event_name=event_name_autocomplete)
@app_commands.describe(event_name="The event to countdown to")
async def countdown_cmd(ctx: commands.Context, *, event_name: str):
    """Show countdown to the next occurrence of an event."""
    cycle = load_event_cycle()
    search = event_name.lower().strip()

    # Find matching event in cycle
    matched_ev = None
    for ev in cycle.get("events", []):
        if search in ev["name"].lower() or ev["name"].lower() in search:
            matched_ev = ev
            break

    if not matched_ev:
        await ctx.send(f"❌ Event `{event_name}` not found. Use `/schedule` to see all events.")
        return

    # Calculate next occurrence
    upcoming = get_upcoming_events(days_ahead=365)
    next_occurrence = None
    for days_until, start_date, ev in upcoming:
        if ev["name"].lower() == matched_ev["name"].lower():
            next_occurrence = (days_until, start_date, ev)
            break

    if not next_occurrence:
        await ctx.send(f"❌ Could not find next occurrence of {matched_ev['name']}.")
        return

    days_until, start_date, ev = next_occurrence
    now = datetime.now(timezone.utc)
    delta = start_date - now

    days = delta.days
    hours = (delta.seconds // 3600) % 24
    minutes = (delta.seconds % 3600) // 60
    seconds = delta.seconds % 60

    embed = discord.Embed(
        title=f"⏱️ Countdown: {ev['emoji']} {ev['name']}",
        description=f"**{days}** days, **{hours}** hours, **{minutes}** minutes, **{seconds}** seconds",
        color=ev.get("color", discord.Color.blue()),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Starts", value=start_date.strftime("%b %d, %Y at %I:%M %p UTC"), inline=True)
    embed.add_field(name="Duration", value=f"{ev.get('duration_days', 1)} day(s)", inline=True)
    embed.set_footer(text="Time to prepare!")
    await ctx.send(embed=embed)


# =========================================================================
# /rolepanel — Post role selection panel (Admin only)
# =========================================================================
@bot.hybrid_command(name="rolepanel")
@app_commands.default_permissions(administrator=True)
@commands.has_permissions(administrator=True)
async def role_panel(ctx: commands.Context):
    """Post role selection panel in this channel. Users can click to toggle roles."""
    embed = discord.Embed(
        title="🏷️ Select Your Alliance Role",
        description="Click the buttons below to add or remove roles. You can have multiple roles!",
        color=discord.Color.purple(),
    )
    embed.add_field(name="R5 | Alliance Leader", value="Guild leader", inline=False)
    embed.add_field(name="R4 | Leadership", value="Officers", inline=False)
    embed.add_field(name="R3 | TC25+", value="Command Center 25+", inline=False)
    embed.add_field(name="R2 | TC24-", value="Command Center 24 or below", inline=False)
    embed.add_field(name="R1 | Bear Bait", value="Members", inline=False)

    await ctx.send(embed=embed, view=RolePanelView())
    await ctx.send("✅ Role panel posted!")


# =========================================================================
# /remindme — Opt-in to event DM reminders
# =========================================================================
@bot.hybrid_command(name="remindme")
async def remind_me(ctx: commands.Context):
    """Opt-in to receive DM reminders before events (60 minutes before)."""
    uid = str(ctx.author.id)
    users = reminder_optins.get("users", [])

    if uid in users:
        users.remove(uid)
        await ctx.send("❌ You've been removed from event reminders. Use `/remindme` again to re-enable.")
    else:
        users.append(uid)
        await ctx.send("✅ You'll now receive DM reminders 60 minutes before events you've signed up for!")

    reminder_optins["users"] = users
    save_data("reminder_optins", reminder_optins)


# =========================================================================
# Background Tasks
# =========================================================================
@tasks.loop(minutes=1)
async def scheduled_announcements():
    """Check and send scheduled announcements (with cron double-fire prevention)."""
    now = datetime.now(timezone.utc)
    cfg = load_config()
    for ann in cfg.get("scheduled_announcements", []):
        if not ann.get("enabled"):
            continue
        if _cron_matches(ann["cron"], now):
            ann_name = ann.get("name", ann["channel"])
            # Only fire if we haven't fired this minute already
            if last_announcement_fires.get(ann_name) != now.strftime("%Y-%m-%d %H:%M"):
                last_announcement_fires[ann_name] = now.strftime("%Y-%m-%d %H:%M")
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
            embed.set_footer(text="Use /tip for more tips | /events for full guides")
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                pass


@tasks.loop(minutes=5)
async def timer_check():
    """Check for expired timers, send alerts, DM reminders, and cleanup old timers."""
    now = datetime.now(timezone.utc)
    timers = war_timers.get("timers", [])
    to_notify = []
    to_remove = []

    for t in timers:
        target = _utc_from_iso(t["time"])
        remaining = (target - now).total_seconds()

        # Alert at 60 minutes for DM reminders
        if 0 < remaining <= 3600 and not t.get("alerted_60"):
            t["alerted_60"] = True
            # DM users who signed up or have timezone set
            users_to_remind = set()
            for signup in war_signups.get("signups", []):
                if signup["event_name"].lower() in t["name"].lower():
                    users_to_remind.update(signup.get("confirmed", []))
            # Also check if user has timezone + reminders enabled
            for uid_str in reminder_optins.get("users", []):
                if uid_str in user_timezones:
                    users_to_remind.add(int(uid_str))

            for uid in users_to_remind:
                try:
                    user = await bot.fetch_user(uid)
                    user_tz = user_timezones.get(str(uid), "UTC")
                    target_local = target.astimezone(ZoneInfo(user_tz) if user_tz != "UTC" else timezone.utc)
                    embed = discord.Embed(
                        title=f"⏰ Event Reminder: {t['name']}",
                        description=f"**{t['name']}** starts in **1 hour**!",
                        color=discord.Color.blue(),
                    )
                    embed.add_field(
                        name="Your Local Time",
                        value=target_local.strftime("%b %d, %I:%M %p %Z"),
                        inline=False,
                    )
                    await user.send(embed=embed)
                except Exception:
                    pass

        # Alert at 15 minutes
        if 0 < remaining <= 900 and not t.get("alerted_15"):
            t["alerted_15"] = True
            to_notify.append((t["name"], "15 minutes"))

        # Alert at start
        elif remaining <= 0 and not t.get("alerted_start"):
            t["alerted_start"] = True
            to_notify.append((t["name"], "NOW"))

        # Cleanup timers that expired more than 24 hours ago
        if remaining < -86400:
            to_remove.append(t)
            log.info(f"Cleaning up expired timer: {t['name']}")

    # Remove expired timers
    for t in to_remove:
        timers.remove(t)

    if to_notify or to_remove:
        save_data("war_timers", war_timers)

    if to_notify:
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


@tasks.loop(hours=1)
async def event_cycle_reminder():
    """Automatically announce events starting today based on the 4-week cycle."""
    now = datetime.now(timezone.utc)
    # Only fire at 8:00 UTC
    if now.hour != 8:
        return

    cycle = load_event_cycle()
    cycle_len = cycle.get("cycle_length_days", 28)
    today = get_cycle_day(now)
    channel_name = cycle.get("reminder_channel", "announcements")

    # Track what we already announced today to avoid duplicates
    announced_today = load_data("cycle_announced", {"date": "", "events": []})
    today_str = now.strftime("%Y-%m-%d")
    if announced_today.get("date") != today_str:
        announced_today = {"date": today_str, "events": []}

    for ev in cycle.get("events", []):
        name = ev["name"]
        if name in announced_today["events"]:
            continue

        start = ev["cycle_day_start"]
        recurring = ev.get("recurring_every_days")
        is_starting_today = False

        if recurring:
            for offset in range(0, cycle_len, recurring):
                if (start + offset) % cycle_len == today:
                    is_starting_today = True
                    break
        else:
            if start == today:
                is_starting_today = True

        if is_starting_today:
            for guild in bot.guilds:
                channel = discord.utils.get(guild.text_channels, name=channel_name)
                if channel:
                    duration = ev.get("duration_days", 1)
                    embed = discord.Embed(
                        title=f"{ev['emoji']} {name} — Starts Today!",
                        description=ev.get("reminder", f"{name} event is starting!"),
                        color=discord.Color.gold(),
                        timestamp=now,
                    )
                    embed.add_field(name="Duration", value=f"{duration} day{'s' if duration != 1 else ''}", inline=True)
                    embed.add_field(name="Type", value=ev.get("type", "event").title(), inline=True)
                    embed.add_field(name="Cycle Day", value=f"{today + 1}/28", inline=True)
                    embed.set_footer(text="🤖 Auto-reminder | Use /nextevent for upcoming events")
                    try:
                        await channel.send(embed=embed)
                        announced_today["events"].append(name)
                    except discord.Forbidden:
                        pass

    # Also announce events starting TOMORROW as a heads-up
    tomorrow = (today + 1) % cycle_len
    for ev in cycle.get("events", []):
        name = ev["name"]
        tomorrow_key = f"tomorrow_{name}"
        if tomorrow_key in announced_today["events"]:
            continue

        start = ev["cycle_day_start"]
        recurring = ev.get("recurring_every_days")
        is_tomorrow = False

        if recurring:
            for offset in range(0, cycle_len, recurring):
                if (start + offset) % cycle_len == tomorrow:
                    is_tomorrow = True
                    break
        else:
            if start == tomorrow:
                is_tomorrow = True

        if is_tomorrow and ev.get("type") in ("alliance", "pvp", "competitive"):
            for guild in bot.guilds:
                channel = discord.utils.get(guild.text_channels, name=channel_name)
                if channel:
                    embed = discord.Embed(
                        title=f"📢 {ev['emoji']} {name} — Starts Tomorrow!",
                        description=f"**{name}** begins tomorrow. Start preparing now!",
                        color=discord.Color.orange(),
                        timestamp=now,
                    )
                    embed.set_footer(text="🤖 Auto-reminder | Heads up for alliance/PvP events")
                    try:
                        await channel.send(embed=embed)
                        announced_today["events"].append(tomorrow_key)
                    except discord.Forbidden:
                        pass

    save_data("cycle_announced", announced_today)


@scheduled_announcements.before_loop
async def before_scheduled():
    await bot.wait_until_ready()

@daily_tip_task.before_loop
async def before_daily_tip():
    await bot.wait_until_ready()

@event_cycle_reminder.before_loop
async def before_event_cycle():
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
