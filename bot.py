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
        "summary": "Cross-kingdom mega event with 4 phases: Matchmaking (48h) → Prep (5d) → Battle (12h) → Field Triage. Kingdom must be 70+ days old.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Garrison:** Zoe + Hilde + Saul\n**Joiners:** Chenko + Amane + Saul + Fahd",
        "troops": "**Attack:** 50% Infantry / 20% Cav / 30% Archers\n**Defense/Garrison:** 60% Infantry / 20% Cav / 20% Archers",
        "tips": "• Phase 1 (48h): Matchmaking — kingdoms are assigned opponents\n• Phase 2 (5d): Prep — build, research, train troops\n• Phase 3 (12h): Battle Window (10:00-22:00 UTC) — intense PvP!\n• Phase 4: Field Triage — healing and cleanup\n• Points from: building, training, research, PvP kills, territory control\n• Hoard speed-ups, Truegold, gems WEEKS in advance",
    },
    "bear": {
        "name": "Bear Hunt",
        "emoji": "🐻",
        "color": 0x8B4513,
        "summary": "Alliance rally event at the Pitfall building. Bear deals NO return damage — go full offense!",
        "heroes": "**Host (Gen 4+):** Amadeus + Petra + Rosa\n**Joiner S-tier:** Vivian (new!) > Chenko > Amane\n**Lethality bonus:** 25% from each hero",
        "troops": "**Host:** 1% Infantry / 10% Cavalry / 89% Archers\n**Joiner:** 0% Inf / 20% Cav / 80% Archer",
        "tips": "• Lethality is the #1 damage stat for Bear Hunt\n• Position towns close to Pitfall for faster rallies\n• Upgrade Pitfall to Level 5 for +5% Attack per level to ALL members\n• Use the Formation button to preset offensive composition",
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
        "troops": "**Per Dungeon:**\n• Tomb of Shadows: 30% Inf / 20% Cav / 50% Archer\n• Frozen Abyss: 50% Inf / 10% Cav / 40% Archer\n• Inferno Core: 40% Inf / 30% Cav / 30% Archer\n• Storm Spire: 20% Inf / 40% Cav / 40% Archer\n• Verdant Maze: 30% Inf / 30% Cav / 40% Archer\n• Crystal Cavern: 50% Inf / 20% Cav / 30% Archer",
        "tips": "• MASSIVE RNG — same battle can win or lose, always use all 5 attempts\n• Buy Mithril from shop first (rarest, most valuable)\n• Every breakthrough is permanent — keep pushing!\n• Adapt composition based on dungeon type",
    },
    "governor": {
        "name": "Strongest Governor",
        "emoji": "🏆",
        "color": 0xB8860B,
        "summary": "Monthly 7-day cross-kingdom event. Different task focus each day. Requires months of prep!",
        "heroes": "**Day 1:** N/A\n**Day 2 & 7:** Hero Development — use Hero Shards & Forgehammers\n**Day 3 & 5:** Basic Skills Up — use skill books & research scrolls\n**Day 4 & 6:** Combat Training — train troops",
        "troops": "**Daily Schedule:**\n• Day 1: City Construction (Truegold, speedups, Governor Gear Charms)\n• Day 2: Hero Development (Mithril, Hero Shards, Forgehammers)\n• Day 3: Basic Skills Up (Skill books, research scrolls)\n• Day 4: Combat Training (Highest-tier troops)\n• Day 5: Basic Skills Up\n• Day 6: Combat Training\n• Day 7: Hero Development",
        "tips": "• Start hoarding resources MONTHS in advance\n• Pre-queue Day 1 building upgrades to finish on day reset\n• Save all Mythic Hero Shards for Days 2 & 7 (3,040 pts each)\n• Batch troop training for Days 4 & 6\n• Top 2,000 governors get cross-kingdom rewards\n• Track leaderboard position — don't burn resources early",
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
    "tri_alliance": {
        "name": "Tri-Alliance Clash",
        "emoji": "⚡",
        "color": 0xFF6347,
        "summary": "3 alliances compete in PvP territory control. Coordinate with leadership!",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Defense:** Zoe + Hilde + Saul",
        "troops": "**Attack:** 50% Infantry / 20% Cav / 30% Archers\n**Defense:** 60% Infantry / 20% Cav / 20% Archers",
        "tips": "• Territory control wins the day — map positioning is critical\n• Spread forces across multiple fronts to deny enemy resources\n• Communicate rally times with leadership for maximum impact",
    },
    "eternitys_reach": {
        "name": "Eternity's Reach",
        "emoji": "🌌",
        "color": 0x4B0082,
        "summary": "Solo progression dungeon with escalating difficulty and milestone rewards.",
        "heroes": "Use your strongest heroes for higher tier runs.",
        "troops": "Full march composition — adjust based on dungeon difficulty.",
        "tips": "• Progress as far as possible for milestone rewards\n• Each floor increases difficulty and rewards\n• Weak formations fail early — don't skip upgrades\n• Ranking rewards go to top performers across all kingdoms",
    },
    "molten_fort": {
        "name": "Molten Fort",
        "emoji": "🔥",
        "color": 0xFF4500,
        "summary": "Alliance siege event — attack/defend fortresses for points and loot.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Defense:** Zoe + Hilde + Saul",
        "troops": "**Attack:** 50% Infantry / 20% Cav / 30% Archers\n**Defense:** 60% Infantry / 20% Cav / 20% Archers",
        "tips": "• Multiple fortresses available — concentrate forces on weak targets\n• Defend from counter-attacks — garrison troops wisely\n• Coordinate with alliance on fortress assignments\n• Capture and hold as long as possible for point accumulation",
    },
    "all_out": {
        "name": "All Out (Kill Event)",
        "emoji": "💀",
        "color": 0x000000,
        "summary": "Open PvP event — attack other players or shield up. Max rewards for participation milestones.",
        "heroes": "Your strongest combat marches.",
        "troops": "Full combat composition for attacking.",
        "tips": "• ⚠️ Shield UP if not participating!\n• Target lower Town Centers for easier wins\n• Hit milestone benchmarks first, then decide if you continue\n• Pop Peace Shield immediately after milestones if you want safety\n• Coordinate with allies on target priority",
    },
    "windward_voyage": {
        "name": "Windward Voyage",
        "emoji": "⛵",
        "color": 0x1E90FF,
        "summary": "Sailing/exploration event with resource discovery and voyage milestones.",
        "heroes": "N/A — sailing event, not combat-focused.",
        "troops": "N/A — sailing event.",
        "tips": "• Discover new locations for bonus resources\n• Plan sailing routes efficiently to hit all hotspots\n• Complete voyage milestones for chest rewards\n• Some events offer trading opportunities with other players",
    },
    "suppress_mode": {
        "name": "Suppress Mode",
        "emoji": "🛡️",
        "color": 0x228B22,
        "summary": "PvE wave defense event — survive increasingly difficult enemy waves.",
        "heroes": "Build defensive-oriented hero lineups.",
        "troops": "**Defense-heavy:** 60% Infantry / 20% Cav / 20% Archers",
        "tips": "• Each wave gets progressively harder — don't under-prepare\n• Use garrison positions for additional defense layers\n• Build towers/traps if available in-game\n• Rank higher by surviving more waves or taking less damage",
    },
    "vikings_vengeance": {
        "name": "Vikings' Vengeance",
        "emoji": "🪓",
        "color": 0x8B0000,
        "summary": "Themed PvP raid event — raid opponent resources and defend yours.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Defense:** Zoe + Hilde + Saul",
        "troops": "**Attack:** 50% Infantry / 20% Cav / 30% Archers\n**Defense:** 60% Infantry / 20% Cav / 20% Archers",
        "tips": "• Target unshielded high-resource cities\n• Defend key resource buildings with garrison troops\n• Coordinate raid schedules with alliance members\n• Track raided status and raid them back if possible",
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
    "💡 Use /suggest to share your best strategies with the alliance!",
    "💡 Submit your troop compositions with /reportcomp to help the alliance optimize for events.",
    "💡 View community troop stats with /troopstats — learn what works for other players.",
    "💡 Check /viewsuggestions to see community-tested strategies for your event.",
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
# User Feedback & Crowdsourcing Commands
# =========================================================================
user_suggestions = load_data("user_suggestions", {"suggestions": []})

@bot.hybrid_command(name="suggest", description="Submit a strategy tip or troop composition for an event")
@app_commands.describe(event="Event name (e.g., bear, kvk, swordland)", suggestion="Your strategy tip or troop comp")
async def suggest(ctx, event: str, *, suggestion: str):
    """Submit a strategy tip or troop composition."""
    entry = {
        "user_id": ctx.author.id,
        "user_name": str(ctx.author),
        "event": event.lower(),
        "suggestion": suggestion,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "votes": 0,
        "status": "pending"
    }
    user_suggestions["suggestions"].append(entry)
    save_data("user_suggestions", user_suggestions)
    embed = discord.Embed(
        title="✅ Suggestion Submitted!",
        description=f"**Event:** {event}\n**Tip:** {suggestion}\n\nLeadership will review your suggestion. Thanks for contributing!",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed, ephemeral=True)

@bot.hybrid_command(name="viewsuggestions", description="View community-submitted strategies for an event")
@app_commands.describe(event="Event name to view suggestions for")
async def viewsuggestions(ctx, event: str):
    """View community suggestions for an event."""
    event_lower = event.lower()
    filtered = [s for s in user_suggestions["suggestions"] if s["event"] == event_lower and s.get("status") != "rejected"]
    if not filtered:
        await ctx.send(embed=discord.Embed(description=f"No suggestions yet for **{event}**. Use `/suggest` to add one!", color=discord.Color.orange()), ephemeral=True)
        return

    pages = []
    per_page = 5
    for i in range(0, len(filtered), per_page):
        chunk = filtered[i:i+per_page]
        desc = ""
        for j, s in enumerate(chunk, start=i+1):
            status_icon = "✅" if s.get("status") == "approved" else "⏳"
            desc += f"{status_icon} **#{j}** by <@{s['user_id']}>\n{s['suggestion']}\n👍 {s.get('votes', 0)} votes\n\n"
        embed = discord.Embed(
            title=f"📋 Community Strategies — {event.title()}",
            description=desc,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Page {i//per_page + 1}/{(len(filtered)-1)//per_page + 1} | Use /suggest to add your own")
        pages.append(embed)

    if len(pages) == 1:
        await ctx.send(embed=pages[0], ephemeral=True)
    else:
        view = PaginatorView(pages)
        await ctx.send(embed=pages[0], view=view, ephemeral=True)

@bot.hybrid_command(name="reportcomp", description="Report your troop composition results for an event")
@app_commands.describe(
    event="Event name (e.g., bear, kvk, mystic)",
    infantry="Infantry percentage (0-100)",
    cavalry="Cavalry percentage (0-100)",
    archers="Archer percentage (0-100)",
    result="How did it go? (e.g., 'Great damage, top 3 in alliance')"
)
async def reportcomp(ctx, event: str, infantry: int, cavalry: int, archers: int, *, result: str):
    """Report your troop composition and results for an event."""
    if infantry + cavalry + archers != 100:
        await ctx.send(embed=discord.Embed(description="❌ Troop percentages must add up to 100!", color=discord.Color.red()), ephemeral=True)
        return

    entry = {
        "user_id": ctx.author.id,
        "user_name": str(ctx.author),
        "event": event.lower(),
        "infantry": infantry,
        "cavalry": cavalry,
        "archers": archers,
        "result": result,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    comps = load_data("troop_reports", {"reports": []})
    comps["reports"].append(entry)
    save_data("troop_reports", comps)

    embed = discord.Embed(
        title="🪖 Troop Report Submitted!",
        description=(
            f"**Event:** {event}\n"
            f"**Composition:** {infantry}% Inf / {cavalry}% Cav / {archers}% Arch\n"
            f"**Result:** {result}\n\n"
            "Your report helps the alliance find optimal compositions!"
        ),
        color=discord.Color.green()
    )
    await ctx.send(embed=embed, ephemeral=True)

@bot.hybrid_command(name="troopstats", description="View aggregated troop composition reports for an event")
@app_commands.describe(event="Event name to view troop stats for")
async def troopstats(ctx, event: str):
    """View aggregated troop composition data from alliance members."""
    comps = load_data("troop_reports", {"reports": []})
    event_lower = event.lower()
    filtered = [r for r in comps["reports"] if r["event"] == event_lower]

    if not filtered:
        await ctx.send(embed=discord.Embed(description=f"No troop reports for **{event}** yet. Use `/reportcomp` to submit yours!", color=discord.Color.orange()), ephemeral=True)
        return

    avg_inf = sum(r["infantry"] for r in filtered) / len(filtered)
    avg_cav = sum(r["cavalry"] for r in filtered) / len(filtered)
    avg_arch = sum(r["archers"] for r in filtered) / len(filtered)

    recent = filtered[-5:]  # Last 5 reports
    recent_text = ""
    for r in reversed(recent):
        recent_text += f"<@{r['user_id']}>: {r['infantry']}%/{r['cavalry']}%/{r['archers']}% — {r['result']}\n"

    embed = discord.Embed(
        title=f"📊 Troop Stats — {event.title()}",
        description=f"Based on **{len(filtered)}** reports from alliance members.",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="📈 Average Composition",
        value=f"Infantry: {avg_inf:.0f}%\nCavalry: {avg_cav:.0f}%\nArchers: {avg_arch:.0f}%",
        inline=True
    )
    embed.add_field(name="🕐 Recent Reports", value=recent_text or "None", inline=False)
    embed.set_footer(text="Use /reportcomp to add your results!")
    await ctx.send(embed=embed, ephemeral=True)

@bot.hybrid_command(name="approvesuggestion", description="[Leadership] Approve or reject a community suggestion")
@app_commands.describe(index="Suggestion number (from /viewsuggestions)", action="approve or reject")
@app_commands.default_permissions(manage_guild=True)
async def approvesuggestion(ctx, index: int, action: str):
    """Approve or reject a community suggestion."""
    if action.lower() not in ("approve", "reject"):
        await ctx.send(embed=discord.Embed(description="❌ Action must be 'approve' or 'reject'", color=discord.Color.red()), ephemeral=True)
        return

    if index < 1 or index > len(user_suggestions["suggestions"]):
        await ctx.send(embed=discord.Embed(description=f"❌ Invalid index. Use /viewsuggestions to see valid numbers.", color=discord.Color.red()), ephemeral=True)
        return

    suggestion = user_suggestions["suggestions"][index - 1]
    suggestion["status"] = "approved" if action.lower() == "approve" else "rejected"
    suggestion["reviewed_by"] = str(ctx.author)
    save_data("user_suggestions", user_suggestions)

    status_text = "✅ Approved" if action.lower() == "approve" else "❌ Rejected"
    embed = discord.Embed(
        title=f"{status_text} Suggestion #{index}",
        description=f"**Event:** {suggestion['event']}\n**Tip:** {suggestion['suggestion']}\n**By:** {suggestion['user_name']}",
        color=discord.Color.green() if action.lower() == "approve" else discord.Color.red()
    )
    await ctx.send(embed=embed, ephemeral=True)


# =========================================================================
# Member Stats Gathering & Alliance Roster
# =========================================================================
member_stats = load_data("member_stats", {})

TROOP_TIERS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11"]
GENERATION_CHOICES = ["Gen 1", "Gen 2", "Gen 3", "Gen 4", "Gen 5"]


class StatInputModal(discord.ui.Modal, title="Enter Your Stats"):
    """Modal form for entering detailed game statistics."""
    tc_level = discord.ui.TextInput(
        label="Town Center Level",
        placeholder="e.g. 25",
        max_length=3,
        required=True,
    )
    total_power = discord.ui.TextInput(
        label="Total Power (use k/m/b)",
        placeholder="e.g. 85m or 85000000",
        max_length=15,
        required=True,
    )
    highest_troop_tier = discord.ui.TextInput(
        label="Highest Troop Tier Unlocked",
        placeholder="e.g. T9 or T11",
        max_length=4,
        required=True,
    )
    generation = discord.ui.TextInput(
        label="Server Generation (1-5)",
        placeholder="e.g. 4",
        max_length=1,
        required=True,
    )
    top_heroes = discord.ui.TextInput(
        label="Top 3 Heroes (name, star level)",
        placeholder="e.g. Amadeus 5*, Hilde 4*, Zoe 4*",
        style=discord.TextStyle.short,
        max_length=100,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)

        # Parse power with k/m/b
        pwr = self.total_power.value.lower().replace(",", "").strip()
        multiplier = 1
        if pwr.endswith("k"):
            multiplier = 1_000; pwr = pwr[:-1]
        elif pwr.endswith("m"):
            multiplier = 1_000_000; pwr = pwr[:-1]
        elif pwr.endswith("b"):
            multiplier = 1_000_000_000; pwr = pwr[:-1]
        try:
            power_val = int(float(pwr) * multiplier)
        except ValueError:
            await interaction.response.send_message("❌ Invalid power value. Use numbers like `85m` or `85000000`.", ephemeral=True)
            return

        # Parse TC level
        try:
            tc = int(self.tc_level.value.strip())
            if tc < 1 or tc > 35:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Town Center level must be a number between 1 and 35.", ephemeral=True)
            return

        # Parse generation
        try:
            gen = int(self.generation.value.strip())
            if gen < 1 or gen > 5:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Generation must be 1-5.", ephemeral=True)
            return

        # Parse troop tier
        tier_raw = self.highest_troop_tier.value.upper().strip().replace(" ", "")
        if not tier_raw.startswith("T"):
            tier_raw = "T" + tier_raw
        if tier_raw not in TROOP_TIERS:
            await interaction.response.send_message(f"❌ Invalid troop tier. Use one of: {', '.join(TROOP_TIERS)}", ephemeral=True)
            return

        # Save stats
        if uid not in member_stats:
            member_stats[uid] = {}

        member_stats[uid].update({
            "user_name": str(interaction.user),
            "tc_level": tc,
            "power": power_val,
            "highest_tier": tier_raw,
            "generation": gen,
            "top_heroes": self.top_heroes.value.strip() if self.top_heroes.value else "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        save_data("member_stats", member_stats)

        # Also sync power to user_profiles
        if uid not in user_profiles:
            user_profiles[uid] = {}
        user_profiles[uid]["power"] = power_val
        save_data("profiles", user_profiles)

        embed = discord.Embed(
            title="✅ Stats Updated!",
            color=discord.Color.green(),
        )
        embed.add_field(name="🏰 Town Center", value=f"Level {tc}", inline=True)
        embed.add_field(name="⚡ Power", value=f"{power_val:,}", inline=True)
        embed.add_field(name="🗡️ Highest Tier", value=tier_raw, inline=True)
        embed.add_field(name="🌍 Generation", value=f"Gen {gen}", inline=True)
        embed.add_field(name="🦸 Top Heroes", value=self.top_heroes.value or "Not set", inline=False)
        embed.set_footer(text="Use /mystats to view • /updatetroops to set troop counts")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TroopInputModal(discord.ui.Modal, title="Enter Your Troop Counts"):
    """Modal form for entering troop counts by type."""
    infantry = discord.ui.TextInput(
        label="Infantry Count (use k/m)",
        placeholder="e.g. 500k or 1.2m",
        max_length=15,
        required=True,
    )
    cavalry = discord.ui.TextInput(
        label="Cavalry Count (use k/m)",
        placeholder="e.g. 200k or 800000",
        max_length=15,
        required=True,
    )
    archers = discord.ui.TextInput(
        label="Archer Count (use k/m)",
        placeholder="e.g. 300k or 1m",
        max_length=15,
        required=True,
    )
    march_capacity = discord.ui.TextInput(
        label="Max March Capacity",
        placeholder="e.g. 250k or 250000",
        max_length=15,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)

        def parse_count(val):
            v = val.lower().replace(",", "").strip()
            mult = 1
            if v.endswith("k"):
                mult = 1_000; v = v[:-1]
            elif v.endswith("m"):
                mult = 1_000_000; v = v[:-1]
            elif v.endswith("b"):
                mult = 1_000_000_000; v = v[:-1]
            return int(float(v) * mult)

        try:
            inf = parse_count(self.infantry.value)
            cav = parse_count(self.cavalry.value)
            arch = parse_count(self.archers.value)
        except (ValueError, TypeError):
            await interaction.response.send_message("❌ Invalid troop count. Use numbers like `500k` or `1200000`.", ephemeral=True)
            return

        march_cap = None
        if self.march_capacity.value and self.march_capacity.value.strip():
            try:
                march_cap = parse_count(self.march_capacity.value)
            except (ValueError, TypeError):
                pass

        if uid not in member_stats:
            member_stats[uid] = {"user_name": str(interaction.user)}

        total = inf + cav + arch
        member_stats[uid].update({
            "infantry": inf,
            "cavalry": cav,
            "archers": arch,
            "total_troops": total,
            "troop_pct_inf": round(inf / total * 100) if total > 0 else 0,
            "troop_pct_cav": round(cav / total * 100) if total > 0 else 0,
            "troop_pct_arch": round(arch / total * 100) if total > 0 else 0,
            "troops_updated_at": datetime.now(timezone.utc).isoformat(),
        })
        if march_cap:
            member_stats[uid]["march_capacity"] = march_cap
        save_data("member_stats", member_stats)

        pct_inf = member_stats[uid]["troop_pct_inf"]
        pct_cav = member_stats[uid]["troop_pct_cav"]
        pct_arch = member_stats[uid]["troop_pct_arch"]

        embed = discord.Embed(
            title="🪖 Troop Counts Updated!",
            color=discord.Color.green(),
        )
        embed.add_field(name="🛡️ Infantry", value=f"{inf:,} ({pct_inf}%)", inline=True)
        embed.add_field(name="🐴 Cavalry", value=f"{cav:,} ({pct_cav}%)", inline=True)
        embed.add_field(name="🏹 Archers", value=f"{arch:,} ({pct_arch}%)", inline=True)
        embed.add_field(name="📊 Total Troops", value=f"{total:,}", inline=True)
        if march_cap:
            embed.add_field(name="🚶 March Cap", value=f"{march_cap:,}", inline=True)
        embed.set_footer(text="Leadership uses /alliancestats to plan events with this data")
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.hybrid_command(name="mystats", description="Enter or view your game stats (TC level, power, troops, heroes, generation)")
async def mystats(ctx: commands.Context):
    """Open the stats input form or view your current stats."""
    uid = str(ctx.author.id)
    data = member_stats.get(uid)

    if data and data.get("tc_level"):
        # Show current stats with an Update button
        embed = discord.Embed(
            title=f"📊 {ctx.author.display_name}'s Stats",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.add_field(name="🏰 Town Center", value=f"Level {data.get('tc_level', '?')}", inline=True)
        embed.add_field(name="⚡ Power", value=f"{data.get('power', 0):,}", inline=True)
        embed.add_field(name="🗡️ Highest Tier", value=data.get("highest_tier", "?"), inline=True)
        embed.add_field(name="🌍 Generation", value=f"Gen {data.get('generation', '?')}", inline=True)
        embed.add_field(name="🦸 Top Heroes", value=data.get("top_heroes", "Not set") or "Not set", inline=False)

        if data.get("infantry") is not None:
            troop_text = (
                f"🛡️ Infantry: {data['infantry']:,} ({data.get('troop_pct_inf', 0)}%)\n"
                f"🐴 Cavalry: {data['cavalry']:,} ({data.get('troop_pct_cav', 0)}%)\n"
                f"🏹 Archers: {data['archers']:,} ({data.get('troop_pct_arch', 0)}%)\n"
                f"📊 Total: {data.get('total_troops', 0):,}"
            )
            if data.get("march_capacity"):
                troop_text += f"\n🚶 March Cap: {data['march_capacity']:,}"
            embed.add_field(name="🪖 Troops", value=troop_text, inline=False)

        updated = data.get("updated_at", "")
        if updated:
            try:
                dt = datetime.fromisoformat(updated)
                embed.set_footer(text=f"Last updated: {dt.strftime('%b %d, %Y')}")
            except Exception:
                pass

        # Buttons to update
        view = View()
        update_btn = Button(label="Update Stats", style=discord.ButtonStyle.primary, emoji="📝")
        troops_btn = Button(label="Update Troops", style=discord.ButtonStyle.secondary, emoji="🪖")

        async def update_stats_callback(interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("You can only update your own stats.", ephemeral=True)
                return
            await interaction.response.send_modal(StatInputModal())

        async def update_troops_callback(interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("You can only update your own troops.", ephemeral=True)
                return
            await interaction.response.send_modal(TroopInputModal())

        update_btn.callback = update_stats_callback
        troops_btn.callback = update_troops_callback
        view.add_item(update_btn)
        view.add_item(troops_btn)

        await ctx.send(embed=embed, view=view, ephemeral=True)
    else:
        # First time — open the modal directly
        if ctx.interaction:
            await ctx.interaction.response.send_modal(StatInputModal())
        else:
            embed = discord.Embed(
                description="Use the slash command `/mystats` to open the stats form!",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)


@bot.hybrid_command(name="updatetroops", description="Enter your troop counts (infantry, cavalry, archers)")
async def updatetroops(ctx: commands.Context):
    """Open the troop count input form."""
    if ctx.interaction:
        await ctx.interaction.response.send_modal(TroopInputModal())
    else:
        await ctx.send("Use the slash command `/updatetroops` to open the troop form!")


@bot.hybrid_command(name="alliancestats", description="[Leadership] View alliance-wide stats summary for event planning")
@app_commands.default_permissions(manage_guild=True)
async def alliancestats(ctx: commands.Context):
    """View aggregated alliance stats for event planning."""
    if not member_stats:
        await ctx.send(embed=discord.Embed(description="No member stats yet. Ask members to use `/mystats`!", color=discord.Color.orange()), ephemeral=True)
        return

    total_members = len(member_stats)
    powers = [d.get("power", 0) for d in member_stats.values() if d.get("power", 0) > 0]
    tc_levels = [d.get("tc_level", 0) for d in member_stats.values() if d.get("tc_level", 0) > 0]
    gens = [d.get("generation", 0) for d in member_stats.values() if d.get("generation", 0) > 0]
    tiers = [d.get("highest_tier", "") for d in member_stats.values() if d.get("highest_tier")]

    # Aggregate troop counts
    total_inf = sum(d.get("infantry", 0) for d in member_stats.values())
    total_cav = sum(d.get("cavalry", 0) for d in member_stats.values())
    total_arch = sum(d.get("archers", 0) for d in member_stats.values())
    total_troops = total_inf + total_cav + total_arch
    members_with_troops = sum(1 for d in member_stats.values() if d.get("infantry") is not None)

    # TC distribution
    tc_25_plus = sum(1 for tc in tc_levels if tc >= 25)
    tc_20_24 = sum(1 for tc in tc_levels if 20 <= tc < 25)
    tc_under_20 = sum(1 for tc in tc_levels if tc < 20)

    # Generation distribution
    gen_counts = {}
    for g in gens:
        gen_counts[g] = gen_counts.get(g, 0) + 1

    # Tier distribution
    tier_counts = {}
    for t in tiers:
        tier_counts[t] = tier_counts.get(t, 0) + 1

    # Page 1: Overview
    embed1 = discord.Embed(title="📊 Alliance Stats — Overview", color=discord.Color.gold())
    embed1.add_field(name="👥 Members Registered", value=str(total_members), inline=True)
    if powers:
        embed1.add_field(name="⚡ Avg Power", value=f"{sum(powers)//len(powers):,}", inline=True)
        embed1.add_field(name="⚡ Total Power", value=f"{sum(powers):,}", inline=True)
        embed1.add_field(name="⚡ Power Range", value=f"{min(powers):,} — {max(powers):,}", inline=False)
    if tc_levels:
        embed1.add_field(name="🏰 TC Distribution", value=f"TC25+: **{tc_25_plus}** | TC20-24: **{tc_20_24}** | <TC20: **{tc_under_20}**", inline=False)
    if gen_counts:
        gen_text = " | ".join(f"Gen {g}: **{c}**" for g, c in sorted(gen_counts.items()))
        embed1.add_field(name="🌍 Generations", value=gen_text, inline=False)

    # Page 2: Troop Breakdown
    embed2 = discord.Embed(title="📊 Alliance Stats — Troop Breakdown", color=discord.Color.gold())
    embed2.add_field(name="👥 Members with Troop Data", value=f"{members_with_troops}/{total_members}", inline=True)
    if total_troops > 0:
        pct_i = round(total_inf / total_troops * 100)
        pct_c = round(total_cav / total_troops * 100)
        pct_a = round(total_arch / total_troops * 100)
        embed2.add_field(name="📊 Total Troops", value=f"{total_troops:,}", inline=True)
        embed2.add_field(name="\u200b", value="\u200b", inline=True)
        embed2.add_field(name="🛡️ Infantry", value=f"{total_inf:,} ({pct_i}%)", inline=True)
        embed2.add_field(name="🐴 Cavalry", value=f"{total_cav:,} ({pct_c}%)", inline=True)
        embed2.add_field(name="🏹 Archers", value=f"{total_arch:,} ({pct_a}%)", inline=True)

        # Event readiness assessment
        readiness = []
        if pct_a >= 70:
            readiness.append("✅ **Bear Hunt:** Great archer ratio for max DPS")
        elif pct_a >= 50:
            readiness.append("⚠️ **Bear Hunt:** Could use more archers (aim for 80%+)")
        else:
            readiness.append("❌ **Bear Hunt:** Need significantly more archers")

        if 40 <= pct_i <= 60 and pct_a >= 20:
            readiness.append("✅ **Swordland/KvK:** Balanced PvP composition")
        else:
            readiness.append("⚠️ **Swordland/KvK:** Aim for 50% Inf / 20% Cav / 30% Arch")

        if pct_i >= 50:
            readiness.append("✅ **Garrison/Defense:** Strong infantry front line")
        else:
            readiness.append("⚠️ **Garrison/Defense:** Need more infantry for tanking")

        embed2.add_field(name="🎯 Event Readiness", value="\n".join(readiness), inline=False)
    else:
        embed2.add_field(name="⚠️ No Troop Data", value="Ask members to use `/updatetroops`", inline=False)

    if tier_counts:
        tier_text = " | ".join(f"{t}: **{c}**" for t, c in sorted(tier_counts.items(), key=lambda x: TROOP_TIERS.index(x[0]) if x[0] in TROOP_TIERS else 0))
        embed2.add_field(name="🗡️ Tier Distribution", value=tier_text, inline=False)

    # Page 3: Top Members
    embed3 = discord.Embed(title="📊 Alliance Stats — Top Members", color=discord.Color.gold())
    sorted_by_power = sorted(
        [(uid, d) for uid, d in member_stats.items() if d.get("power", 0) > 0],
        key=lambda x: x[1]["power"], reverse=True
    )
    top_text = ""
    for i, (uid, d) in enumerate(sorted_by_power[:15], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
        name = d.get("user_name", "Unknown").split("#")[0]
        tc = d.get("tc_level", "?")
        tier = d.get("highest_tier", "?")
        heroes = d.get("top_heroes", "")
        hero_short = f" | {heroes}" if heroes else ""
        top_text += f"{medal} **{name}** — {d['power']:,} | TC{tc} | {tier}{hero_short}\n"
    embed3.add_field(name="🏆 Top 15 by Power", value=top_text or "No data", inline=False)
    embed3.set_footer(text="Use /mystats to register • /updatetroops for troop counts")

    pages = [embed1, embed2, embed3]
    view = PaginatorView(pages)
    await ctx.send(embed=pages[0], view=view, ephemeral=True)


@bot.hybrid_command(name="eventready", description="[Leadership] Check alliance readiness for a specific event")
@app_commands.describe(event="Event to check readiness for (bear, kvk, swordland, mystic, brawl)")
@app_commands.default_permissions(manage_guild=True)
async def eventready(ctx: commands.Context, event: str):
    """Check how ready the alliance is for a specific event based on member stats."""
    event_lower = event.lower()
    if not member_stats:
        await ctx.send(embed=discord.Embed(description="No member stats yet. Ask members to use `/mystats`!", color=discord.Color.orange()), ephemeral=True)
        return

    # Define ideal compositions and requirements per event
    event_reqs = {
        "bear": {
            "name": "Bear Hunt",
            "emoji": "🐻",
            "ideal_inf": 1, "ideal_cav": 10, "ideal_arch": 89,
            "joiner_inf": 0, "joiner_cav": 20, "joiner_arch": 80,
            "min_tc": 15,
            "notes": "Focus: Lethality stat. All archers, no defense needed.\nHost: Amadeus + Petra + Rosa (Gen 4+)\nJoiners S-tier: Vivian > Chenko > Amane",
        },
        "kvk": {
            "name": "Kingdom of Power (KvK)",
            "emoji": "👑",
            "ideal_inf": 50, "ideal_cav": 20, "ideal_arch": 30,
            "min_tc": 20,
            "notes": "4 phases. Battle Window: 12h (10:00-22:00 UTC)\nAttack: Amadeus + Hilde + Marlin\nDefense: Zoe + Hilde + Saul",
        },
        "swordland": {
            "name": "Swordland Showdown",
            "emoji": "⚔️",
            "ideal_inf": 50, "ideal_cav": 20, "ideal_arch": 30,
            "min_tc": 15,
            "notes": "Rush Stables first. Personal score > winning.\nGarrison: 60% Inf / 20% Cav / 20% Arch",
        },
        "mystic": {
            "name": "Mystic Trial",
            "emoji": "🔮",
            "ideal_inf": 50, "ideal_cav": 20, "ideal_arch": 30,
            "min_tc": 10,
            "notes": "Varies by dungeon. 5 attempts/day, massive RNG.\nTomb: 30/20/50 | Frozen: 50/10/40 | Inferno: 40/30/30\nStorm: 20/40/40 | Verdant: 30/30/40 | Crystal: 50/20/30",
        },
        "brawl": {
            "name": "Alliance Brawl",
            "emoji": "💥",
            "ideal_inf": 50, "ideal_cav": 20, "ideal_arch": 30,
            "min_tc": 20,
            "notes": "6.5-day event. Save Intel Missions for Days 2 & 4.\nDay 6 = 4 horns — decisive day!",
        },
    }

    req = event_reqs.get(event_lower)
    if not req:
        events_list = ", ".join(event_reqs.keys())
        await ctx.send(embed=discord.Embed(description=f"Unknown event. Choose from: {events_list}", color=discord.Color.red()), ephemeral=True)
        return

    # Analyze members
    eligible = []
    not_ready = []
    no_data = []
    min_tc = req.get("min_tc", 1)

    for uid, d in member_stats.items():
        tc = d.get("tc_level", 0)
        if tc == 0:
            no_data.append(d.get("user_name", "Unknown"))
            continue
        if tc >= min_tc:
            eligible.append(d)
        else:
            not_ready.append(d)

    # Aggregate eligible troops
    elig_inf = sum(d.get("infantry", 0) for d in eligible)
    elig_cav = sum(d.get("cavalry", 0) for d in eligible)
    elig_arch = sum(d.get("archers", 0) for d in eligible)
    elig_total = elig_inf + elig_cav + elig_arch

    embed = discord.Embed(
        title=f"{req['emoji']} {req['name']} — Readiness Report",
        color=discord.Color.gold(),
    )
    embed.add_field(name="✅ Eligible (TC{min_tc}+)", value=str(len(eligible)), inline=True)
    embed.add_field(name="❌ Not Ready", value=str(len(not_ready)), inline=True)
    embed.add_field(name="❓ No Data", value=str(len(no_data)), inline=True)

    if elig_total > 0:
        cur_inf = round(elig_inf / elig_total * 100)
        cur_cav = round(elig_cav / elig_total * 100)
        cur_arch = round(elig_arch / elig_total * 100)

        comp_text = (
            f"**Current:** {cur_inf}% Inf / {cur_cav}% Cav / {cur_arch}% Arch\n"
            f"**Ideal:** {req['ideal_inf']}% Inf / {req['ideal_cav']}% Cav / {req['ideal_arch']}% Arch"
        )
        embed.add_field(name="🪖 Troop Composition", value=comp_text, inline=False)

        # Gap analysis
        gaps = []
        diff_inf = cur_inf - req["ideal_inf"]
        diff_cav = cur_cav - req["ideal_cav"]
        diff_arch = cur_arch - req["ideal_arch"]
        if abs(diff_inf) > 10:
            direction = "too many" if diff_inf > 0 else "need more"
            gaps.append(f"🛡️ Infantry: {direction} ({abs(diff_inf)}% off)")
        if abs(diff_cav) > 10:
            direction = "too many" if diff_cav > 0 else "need more"
            gaps.append(f"🐴 Cavalry: {direction} ({abs(diff_cav)}% off)")
        if abs(diff_arch) > 10:
            direction = "too many" if diff_arch > 0 else "need more"
            gaps.append(f"🏹 Archers: {direction} ({abs(diff_arch)}% off)")

        if gaps:
            embed.add_field(name="⚠️ Composition Gaps", value="\n".join(gaps), inline=False)
        else:
            embed.add_field(name="✅ Composition", value="Alliance troop balance looks good for this event!", inline=False)

    embed.add_field(name="📋 Event Notes", value=req["notes"], inline=False)

    if no_data:
        names = ", ".join(n.split("#")[0] for n in no_data[:10])
        if len(no_data) > 10:
            names += f" +{len(no_data) - 10} more"
        embed.add_field(name="❓ Missing Stats", value=f"Members without data: {names}\nAsk them to use `/mystats`", inline=False)

    embed.set_footer(text="Data based on member-submitted stats via /mystats and /updatetroops")
    await ctx.send(embed=embed, ephemeral=True)


# =========================================================================
# Event Optimization Engine
# =========================================================================

# Hero database with roles, stats, and event affinity
HERO_DB = {
    # Name: {role, type, lethality_buff, best_events[], captain_events[], joiner_events[], tier}
    "amadeus":   {"role": "captain",  "type": "attack",  "lethality": 25, "atk_buff": 30, "tier": "S",
                  "captain_for": ["bear", "kvk", "swordland", "tri_alliance", "molten_fort", "all_out"],
                  "joiner_for": []},
    "petra":     {"role": "captain",  "type": "attack",  "lethality": 20, "atk_buff": 25, "tier": "S",
                  "captain_for": ["bear"],
                  "joiner_for": ["kvk", "swordland"]},
    "rosa":      {"role": "support",  "type": "attack",  "lethality": 15, "atk_buff": 20, "tier": "A",
                  "captain_for": ["bear"],
                  "joiner_for": ["bear"]},
    "vivian":    {"role": "joiner",   "type": "attack",  "lethality": 25, "atk_buff": 20, "tier": "S",
                  "captain_for": [],
                  "joiner_for": ["bear", "kvk", "swordland", "all_out"]},
    "chenko":    {"role": "joiner",   "type": "attack",  "lethality": 25, "atk_buff": 15, "tier": "S",
                  "captain_for": [],
                  "joiner_for": ["bear", "kvk", "swordland", "all_out", "tri_alliance"]},
    "amane":     {"role": "joiner",   "type": "attack",  "lethality": 20, "atk_buff": 15, "tier": "A",
                  "captain_for": [],
                  "joiner_for": ["bear", "kvk", "swordland", "all_out"]},
    "hilde":     {"role": "flex",     "type": "attack",  "lethality": 15, "atk_buff": 25, "tier": "S",
                  "captain_for": ["kvk", "swordland", "tri_alliance", "molten_fort"],
                  "joiner_for": ["bear"]},
    "marlin":    {"role": "flex",     "type": "attack",  "lethality": 10, "atk_buff": 20, "tier": "A",
                  "captain_for": ["kvk", "swordland"],
                  "joiner_for": ["tri_alliance", "molten_fort"]},
    "zoe":       {"role": "captain",  "type": "defense", "lethality": 5,  "atk_buff": 10, "tier": "S",
                  "captain_for": ["kvk", "swordland", "tri_alliance", "molten_fort", "sanctuary"],
                  "joiner_for": []},
    "saul":      {"role": "flex",     "type": "defense", "lethality": 5,  "atk_buff": 15, "tier": "A",
                  "captain_for": ["sanctuary"],
                  "joiner_for": ["kvk", "swordland"]},
    "fahd":      {"role": "joiner",   "type": "support", "lethality": 10, "atk_buff": 10, "tier": "B",
                  "captain_for": [],
                  "joiner_for": ["kvk", "brawl"]},
    "yeonwoo":   {"role": "joiner",   "type": "attack",  "lethality": 15, "atk_buff": 15, "tier": "A",
                  "captain_for": [],
                  "joiner_for": ["bear", "swordland"]},
    "diana":     {"role": "utility",  "type": "support", "lethality": 0,  "atk_buff": 5,  "tier": "A",
                  "captain_for": ["cesares", "desert_trial"],
                  "joiner_for": []},
    "yanu":      {"role": "joiner",   "type": "attack",  "lethality": 25, "atk_buff": 10, "tier": "A",
                  "captain_for": [],
                  "joiner_for": ["bear"]},
}

# Event formation presets with role requirements
EVENT_FORMATIONS = {
    "bear": {
        "name": "Bear Hunt",
        "emoji": "🐻",
        "type": "rally",
        "needs_rally_leader": True,
        "host_comp": {"infantry": 1, "cavalry": 10, "archers": 89},
        "joiner_comp": {"infantry": 0, "cavalry": 20, "archers": 80},
        "ideal_captain_heroes": ["amadeus", "petra", "rosa"],
        "ideal_joiner_heroes": ["vivian", "chenko", "amane", "yanu", "yeonwoo"],
        "scoring_stat": "lethality",
        "min_tc": 15,
        "min_tier": "T6",
        "roles_needed": {"rally_leader": 1, "joiners": 20},
        "notes": "Bear deals NO return damage. Pure offense. Lethality > ATK > everything else.",
    },
    "kvk": {
        "name": "Kingdom of Power (KvK)",
        "emoji": "👑",
        "type": "mixed",
        "needs_rally_leader": True,
        "attack_comp": {"infantry": 50, "cavalry": 20, "archers": 30},
        "defense_comp": {"infantry": 60, "cavalry": 20, "archers": 20},
        "ideal_captain_heroes": ["amadeus", "hilde", "marlin"],
        "ideal_garrison_heroes": ["zoe", "hilde", "saul"],
        "ideal_joiner_heroes": ["chenko", "amane", "vivian", "fahd"],
        "scoring_stat": "atk_buff",
        "min_tc": 20,
        "min_tier": "T8",
        "roles_needed": {"rally_leader": 3, "garrison_captain": 2, "attackers": 10, "defenders": 5},
        "notes": "Battle Window: 12h (10:00-22:00 UTC). Need both attack and garrison teams.",
    },
    "swordland": {
        "name": "Swordland Showdown",
        "emoji": "⚔️",
        "type": "mixed",
        "needs_rally_leader": True,
        "attack_comp": {"infantry": 50, "cavalry": 20, "archers": 30},
        "defense_comp": {"infantry": 60, "cavalry": 20, "archers": 20},
        "ideal_captain_heroes": ["amadeus", "hilde", "marlin"],
        "ideal_garrison_heroes": ["zoe", "hilde", "saul"],
        "ideal_joiner_heroes": ["chenko", "amane", "vivian", "yeonwoo"],
        "scoring_stat": "atk_buff",
        "min_tc": 15,
        "min_tier": "T7",
        "roles_needed": {"attackers": 12, "defenders": 6, "scouts": 2},
        "notes": "60% Attack / 30% Defend / 10% Scout. Rush Royal Stables FIRST.",
    },
    "brawl": {
        "name": "Alliance Brawl",
        "emoji": "💥",
        "type": "alliance",
        "needs_rally_leader": False,
        "ideal_comp": {"infantry": 50, "cavalry": 20, "archers": 30},
        "ideal_captain_heroes": ["amadeus", "hilde"],
        "ideal_joiner_heroes": ["chenko", "amane", "fahd"],
        "scoring_stat": "atk_buff",
        "min_tc": 20,
        "min_tier": "T8",
        "roles_needed": {"top_20": 20},
        "notes": "Day 6 = 4 horns. Save Intel Missions for Days 2 & 4.",
    },
    "sanctuary": {
        "name": "Sanctuary Battle",
        "emoji": "🏛️",
        "type": "mixed",
        "needs_rally_leader": True,
        "attack_comp": {"infantry": 50, "cavalry": 30, "archers": 20},
        "defense_comp": {"infantry": 50, "cavalry": 20, "archers": 30},
        "ideal_captain_heroes": ["amadeus", "hilde"],
        "ideal_garrison_heroes": ["zoe", "saul"],
        "ideal_joiner_heroes": ["chenko", "amane"],
        "scoring_stat": "atk_buff",
        "min_tc": 15,
        "min_tier": "T6",
        "roles_needed": {"rally_leader": 2, "garrison_captain": 1, "fighters": 10},
        "notes": "Hold outposts 30+ min. Teleport key leaders, march secondary troops.",
    },
    "tri_alliance": {
        "name": "Tri-Alliance Clash",
        "emoji": "⚡",
        "type": "mixed",
        "needs_rally_leader": True,
        "attack_comp": {"infantry": 50, "cavalry": 20, "archers": 30},
        "defense_comp": {"infantry": 60, "cavalry": 20, "archers": 20},
        "ideal_captain_heroes": ["amadeus", "hilde", "marlin"],
        "ideal_garrison_heroes": ["zoe", "hilde", "saul"],
        "ideal_joiner_heroes": ["chenko", "vivian"],
        "scoring_stat": "atk_buff",
        "min_tc": 20,
        "min_tier": "T8",
        "roles_needed": {"rally_leader": 2, "garrison_captain": 2, "fighters": 15},
        "notes": "3-way PvP. Territory control wins. Spread forces across fronts.",
    },
    "all_out": {
        "name": "All Out (Kill Event)",
        "emoji": "💀",
        "type": "pvp",
        "needs_rally_leader": True,
        "attack_comp": {"infantry": 50, "cavalry": 20, "archers": 30},
        "ideal_captain_heroes": ["amadeus", "hilde", "marlin"],
        "ideal_joiner_heroes": ["chenko", "vivian", "amane"],
        "scoring_stat": "atk_buff",
        "min_tc": 15,
        "min_tier": "T7",
        "roles_needed": {"rally_leader": 3, "attackers": 10, "shielded": 999},
        "notes": "⚠️ Shield if not participating! Hit milestones then shield up.",
    },
}


def _parse_member_heroes(heroes_str: str) -> list:
    """Parse hero string like 'Amadeus 5*, Hilde 4*, Zoe 4*' into structured list."""
    if not heroes_str:
        return []
    heroes = []
    for part in heroes_str.split(","):
        part = part.strip()
        if not part:
            continue
        # Try to extract star level
        stars = 0
        name = part
        for suffix in ["5*", "4*", "3*", "2*", "1*", "5", "4", "3", "2", "1"]:
            if part.endswith(suffix):
                name = part[:-len(suffix)].strip()
                stars = int(suffix[0])
                break
        heroes.append({"name": name.lower(), "stars": stars})
    return heroes


def _score_member_for_event(uid: str, data: dict, event_key: str) -> dict:
    """Score a member's fitness for a specific event role. Returns scoring dict."""
    formation = EVENT_FORMATIONS.get(event_key)
    if not formation:
        return {"score": 0, "roles": [], "issues": []}

    score = 0
    roles = []
    issues = []

    power = data.get("power", 0)
    tc = data.get("tc_level", 0)
    tier = data.get("highest_tier", "T1")
    gen = data.get("generation", 1)
    heroes = _parse_member_heroes(data.get("top_heroes", ""))
    hero_names = [h["name"] for h in heroes]

    tier_num = int(tier.replace("T", "")) if tier.startswith("T") else 1
    min_tier_num = int(formation.get("min_tier", "T1").replace("T", ""))

    # TC check
    if tc < formation.get("min_tc", 1):
        issues.append(f"TC{tc} below minimum TC{formation['min_tc']}")
        score -= 50

    # Tier check
    if tier_num < min_tier_num:
        issues.append(f"{tier} below minimum {formation.get('min_tier', 'T1')}")
        score -= 30

    # Power scoring (normalized to 100M baseline)
    score += min(power / 1_000_000, 200)  # Up to 200 pts for power

    # Tier bonus
    score += tier_num * 10  # T11 = 110, T8 = 80, etc.

    # TC bonus
    score += tc * 3  # TC25 = 75

    # Hero matching for captain role
    captain_heroes = formation.get("ideal_captain_heroes", [])
    captain_match = sum(1 for h in hero_names if h in captain_heroes)
    if captain_match >= 2:
        roles.append("rally_leader")
        score += captain_match * 40

    # Hero matching for garrison role
    garrison_heroes = formation.get("ideal_garrison_heroes", [])
    garrison_match = sum(1 for h in hero_names if h in garrison_heroes)
    if garrison_match >= 2:
        roles.append("garrison_captain")
        score += garrison_match * 35

    # Hero matching for joiner role
    joiner_heroes = formation.get("ideal_joiner_heroes", [])
    joiner_match = sum(1 for h in hero_names if h in joiner_heroes)
    if joiner_match > 0:
        roles.append("joiner")
        score += joiner_match * 25

    # Hero star level bonus
    for h in heroes:
        if h["name"] in captain_heroes or h["name"] in joiner_heroes or h["name"] in garrison_heroes:
            score += h["stars"] * 8

    # Troop composition fitness
    inf = data.get("infantry", 0)
    cav = data.get("cavalry", 0)
    arch = data.get("archers", 0)
    total = inf + cav + arch
    if total > 0:
        pct_inf = inf / total * 100
        pct_arch = arch / total * 100

        if event_key == "bear":
            # Bear needs archers
            if pct_arch >= 70:
                score += 50
                roles.append("optimal_comp")
            elif pct_arch >= 50:
                score += 20
            else:
                issues.append("Low archer ratio for Bear Hunt")
        else:
            # PvP events need balanced comp
            if 35 <= pct_inf <= 65 and pct_arch >= 15:
                score += 30
                roles.append("balanced_comp")

    # March capacity bonus
    march_cap = data.get("march_capacity", 0)
    if march_cap > 0:
        score += min(march_cap / 10_000, 50)

    # If no heroes listed, flag it
    if not heroes:
        issues.append("No heroes listed — use /mystats to add")

    # No troops data
    if total == 0:
        issues.append("No troop data — use /updatetroops")

    return {
        "score": round(score, 1),
        "roles": roles,
        "issues": issues,
        "power": power,
        "tc": tc,
        "tier": tier,
        "heroes": hero_names,
        "gen": gen,
    }


@bot.hybrid_command(name="optimize", description="[Leadership] Get AI-optimized team setup for an event")
@app_commands.describe(event="Event to optimize for (bear, kvk, swordland, brawl, sanctuary, tri_alliance, all_out)")
@app_commands.default_permissions(manage_guild=True)
async def optimize(ctx: commands.Context, event: str):
    """Generate optimized team composition, rally leaders, and role assignments for an event."""
    event_lower = event.lower()
    formation = EVENT_FORMATIONS.get(event_lower)
    if not formation:
        events_list = ", ".join(EVENT_FORMATIONS.keys())
        await ctx.send(embed=discord.Embed(
            description=f"Unknown event. Choose from: `{events_list}`",
            color=discord.Color.red()
        ), ephemeral=True)
        return

    if not member_stats:
        await ctx.send(embed=discord.Embed(
            description="No member stats yet! Ask members to use `/mystats` and `/updatetroops` first.",
            color=discord.Color.orange()
        ), ephemeral=True)
        return

    # Score every member
    scored = []
    for uid, data in member_stats.items():
        result = _score_member_for_event(uid, data, event_lower)
        result["uid"] = uid
        result["name"] = data.get("user_name", "Unknown").split("#")[0]
        scored.append(result)

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Assign roles
    rally_leaders = []
    garrison_captains = []
    attackers = []
    joiners = []
    benched = []

    assigned_uids = set()
    roles_needed = formation.get("roles_needed", {})

    # Pass 1: Rally Leaders (highest score + has captain heroes)
    rl_needed = roles_needed.get("rally_leader", 0)
    for m in scored:
        if len(rally_leaders) >= rl_needed:
            break
        if "rally_leader" in m["roles"] and m["uid"] not in assigned_uids and not m["issues"]:
            rally_leaders.append(m)
            assigned_uids.add(m["uid"])

    # Fill with best available if not enough
    for m in scored:
        if len(rally_leaders) >= rl_needed:
            break
        if m["uid"] not in assigned_uids and m["score"] > 100 and m.get("tc", 0) >= formation.get("min_tc", 1):
            rally_leaders.append(m)
            assigned_uids.add(m["uid"])

    # Pass 2: Garrison Captains
    gc_needed = roles_needed.get("garrison_captain", 0)
    for m in scored:
        if len(garrison_captains) >= gc_needed:
            break
        if "garrison_captain" in m["roles"] and m["uid"] not in assigned_uids:
            garrison_captains.append(m)
            assigned_uids.add(m["uid"])

    # Pass 3: Remaining fighters/joiners
    fighters_needed = roles_needed.get("attackers", 0) + roles_needed.get("fighters", 0) + roles_needed.get("joiners", 0) + roles_needed.get("top_20", 0)
    for m in scored:
        if len(attackers) + len(joiners) >= fighters_needed:
            break
        if m["uid"] not in assigned_uids:
            if m["score"] > 50 and not any("below minimum" in i for i in m["issues"]):
                if "joiner" in m["roles"]:
                    joiners.append(m)
                else:
                    attackers.append(m)
                assigned_uids.add(m["uid"])

    # Everyone else
    for m in scored:
        if m["uid"] not in assigned_uids:
            benched.append(m)

    # Build pages
    pages = []

    # Page 1: Overview & Optimal Formation
    e1 = discord.Embed(
        title=f"{formation['emoji']} {formation['name']} — Optimized Setup",
        description=formation["notes"],
        color=discord.Color.gold(),
    )
    comp = formation.get("host_comp") or formation.get("attack_comp") or formation.get("ideal_comp", {})
    comp_text = " / ".join(f"**{v}%** {k.title()}" for k, v in comp.items())
    e1.add_field(name="🪖 Optimal Attack Formation", value=comp_text, inline=False)

    def_comp = formation.get("joiner_comp") or formation.get("defense_comp")
    if def_comp:
        label = "Joiner Formation" if "joiner_comp" in formation else "Defense Formation"
        def_text = " / ".join(f"**{v}%** {k.title()}" for k, v in def_comp.items())
        e1.add_field(name=f"🛡️ {label}", value=def_text, inline=False)

    # Hero recommendations
    capt_heroes = formation.get("ideal_captain_heroes", [])
    garr_heroes = formation.get("ideal_garrison_heroes", [])
    join_heroes = formation.get("ideal_joiner_heroes", [])
    hero_text = ""
    if capt_heroes:
        hero_text += f"**Captain/Host:** {', '.join(h.title() for h in capt_heroes)}\n"
    if garr_heroes:
        hero_text += f"**Garrison:** {', '.join(h.title() for h in garr_heroes)}\n"
    if join_heroes:
        hero_text += f"**Joiners:** {', '.join(h.title() for h in join_heroes)}\n"
    e1.add_field(name="🦸 Recommended Heroes", value=hero_text or "Any strong heroes", inline=False)

    eligible_count = sum(1 for m in scored if not any("below minimum" in i for i in m["issues"]))
    e1.add_field(name="👥 Eligible Members", value=f"{eligible_count}/{len(scored)} registered", inline=True)
    e1.add_field(name="📊 Data Quality", value=f"{sum(1 for m in scored if m.get('heroes')):}/{len(scored)} have heroes listed", inline=True)
    e1.set_footer(text="Page 1/4 — Overview")
    pages.append(e1)

    # Page 2: Rally Leaders & Garrison
    e2 = discord.Embed(
        title=f"{formation['emoji']} {formation['name']} — Rally Leaders & Garrison",
        color=discord.Color.red(),
    )
    if rally_leaders:
        rl_text = ""
        for i, m in enumerate(rally_leaders, 1):
            heroes_display = ", ".join(h.title() for h in m["heroes"][:3]) if m["heroes"] else "N/A"
            rl_text += f"**{i}. {m['name']}** — {m['power']:,} power | {m['tier']} | TC{m['tc']}\n"
            rl_text += f"   Heroes: {heroes_display} | Score: {m['score']}\n"
        e2.add_field(name=f"🚩 Rally Leaders ({len(rally_leaders)}/{rl_needed})", value=rl_text, inline=False)
    else:
        e2.add_field(name="🚩 Rally Leaders", value="⚠️ No qualified rally leaders found!\nNeed members with high power + captain heroes.", inline=False)

    if garrison_captains:
        gc_text = ""
        for i, m in enumerate(garrison_captains, 1):
            heroes_display = ", ".join(h.title() for h in m["heroes"][:3]) if m["heroes"] else "N/A"
            gc_text += f"**{i}. {m['name']}** — {m['power']:,} power | {m['tier']} | TC{m['tc']}\n"
            gc_text += f"   Heroes: {heroes_display} | Score: {m['score']}\n"
        e2.add_field(name=f"🏰 Garrison ({len(garrison_captains)}/{gc_needed})", value=gc_text, inline=False)
    elif gc_needed > 0:
        e2.add_field(name="🏰 Garrison Captains", value="⚠️ No garrison captains found!\nNeed members with Zoe, Hilde, or Saul.", inline=False)

    # Shortfall warnings
    shortfalls = []
    if len(rally_leaders) < rl_needed:
        shortfalls.append(f"Need **{rl_needed - len(rally_leaders)}** more rally leader(s)")
    if len(garrison_captains) < gc_needed:
        shortfalls.append(f"Need **{gc_needed - len(garrison_captains)}** more garrison captain(s)")
    if shortfalls:
        e2.add_field(name="🔴 Shortfalls", value="\n".join(shortfalls), inline=False)

    e2.set_footer(text="Page 2/4 — Leadership Assignments")
    pages.append(e2)

    # Page 3: Fighter Assignments
    e3 = discord.Embed(
        title=f"{formation['emoji']} {formation['name']} — Fighter Roster",
        color=discord.Color.blue(),
    )
    if joiners or attackers:
        fighters_all = attackers + joiners
        fighters_all.sort(key=lambda x: x["score"], reverse=True)
        f_text = ""
        for i, m in enumerate(fighters_all[:20], 1):
            role_tag = "🏹" if "optimal_comp" in m["roles"] else "⚔️" if "balanced_comp" in m["roles"] else "👤"
            heroes_short = ", ".join(h.title() for h in m["heroes"][:2]) if m["heroes"] else ""
            f_text += f"{role_tag} **{m['name']}** — {m['power']:,} | {m['tier']} | {heroes_short}\n"
        e3.add_field(name=f"⚔️ Assigned Fighters ({len(fighters_all)})", value=f_text or "None", inline=False)
    else:
        e3.add_field(name="⚔️ Fighters", value="No fighters assigned — need more members with stats!", inline=False)

    if benched:
        b_text = ""
        for m in benched[:10]:
            issue_text = " | ".join(m["issues"][:2]) if m["issues"] else "Low score"
            b_text += f"⏸️ **{m['name']}** — {issue_text}\n"
        if len(benched) > 10:
            b_text += f"*+{len(benched) - 10} more benched*\n"
        e3.add_field(name=f"⏸️ Bench / Need Improvement ({len(benched)})", value=b_text, inline=False)

    e3.set_footer(text="Page 3/4 — Fighter Roster")
    pages.append(e3)

    # Page 4: Troop Analysis & Recommendations
    e4 = discord.Embed(
        title=f"{formation['emoji']} {formation['name']} — Troop Analysis & Action Items",
        color=discord.Color.green(),
    )

    # Aggregate troops from assigned members only
    assigned_members = rally_leaders + garrison_captains + attackers + joiners
    a_inf = sum(member_stats.get(m["uid"], {}).get("infantry", 0) for m in assigned_members)
    a_cav = sum(member_stats.get(m["uid"], {}).get("cavalry", 0) for m in assigned_members)
    a_arch = sum(member_stats.get(m["uid"], {}).get("archers", 0) for m in assigned_members)
    a_total = a_inf + a_cav + a_arch

    if a_total > 0:
        cur_inf = round(a_inf / a_total * 100)
        cur_cav = round(a_cav / a_total * 100)
        cur_arch = round(a_arch / a_total * 100)

        ideal = comp
        analysis = f"**Current (assigned):** {cur_inf}% Inf / {cur_cav}% Cav / {cur_arch}% Arch\n"
        analysis += f"**Ideal:** {ideal.get('infantry', 0)}% Inf / {ideal.get('cavalry', 0)}% Cav / {ideal.get('archers', 0)}% Arch\n"

        # Specific recommendations
        diff_arch = cur_arch - ideal.get("archers", 0)
        diff_inf = cur_inf - ideal.get("infantry", 0)
        if abs(diff_arch) > 10 or abs(diff_inf) > 10:
            analysis += "\n**⚠️ Adjustments needed:**\n"
            if diff_arch < -10:
                analysis += f"• Train **{abs(diff_arch)}%** more archers\n"
            elif diff_arch > 15:
                analysis += f"• Alliance is archer-heavy (+{diff_arch}%); reassign some to infantry\n"
            if diff_inf < -10:
                analysis += f"• Train **{abs(diff_inf)}%** more infantry\n"
            elif diff_inf > 15:
                analysis += f"• Alliance is infantry-heavy (+{diff_inf}%); reassign some to archers\n"
        else:
            analysis += "\n✅ Troop balance is within acceptable range!"

        e4.add_field(name="📊 Troop Composition Analysis", value=analysis, inline=False)

    # Action items
    actions = []
    members_no_heroes = [m["name"] for m in scored if not m["heroes"]]
    members_no_troops = [m["name"] for m in scored if member_stats.get(m["uid"], {}).get("infantry") is None]
    members_low_tc = [m["name"] for m in scored if m.get("tc", 0) < formation.get("min_tc", 1) and m.get("tc", 0) > 0]

    if members_no_heroes:
        actions.append(f"**{len(members_no_heroes)}** members need to add heroes via `/mystats`")
    if members_no_troops:
        actions.append(f"**{len(members_no_troops)}** members need to add troop counts via `/updatetroops`")
    if members_low_tc:
        actions.append(f"**{len(members_low_tc)}** members below TC{formation.get('min_tc', 1)} minimum")
    if len(rally_leaders) < rl_needed:
        needed_heroes = ", ".join(h.title() for h in capt_heroes)
        actions.append(f"Recruit rally leaders with: {needed_heroes}")
    if not actions:
        actions.append("✅ Alliance is well-prepared for this event!")

    e4.add_field(name="📋 Action Items", value="\n".join(f"• {a}" for a in actions), inline=False)

    # Quick summary
    total_assigned = len(assigned_members)
    total_power_assigned = sum(m["power"] for m in assigned_members)
    e4.add_field(name="📈 Summary", value=(
        f"**Assigned:** {total_assigned} members\n"
        f"**Combined Power:** {total_power_assigned:,}\n"
        f"**Rally Leaders:** {len(rally_leaders)}/{rl_needed}\n"
        f"**Avg Score:** {sum(m['score'] for m in assigned_members) / max(len(assigned_members), 1):.0f}"
    ), inline=False)

    e4.set_footer(text="Page 4/4 — Analysis & Actions | Data from /mystats + /updatetroops")
    pages.append(e4)

    view = PaginatorView(pages)
    await ctx.send(embed=pages[0], view=view, ephemeral=True)


@bot.hybrid_command(name="myfit", description="Check how well you fit into each event and get personal recommendations")
async def myfit(ctx: commands.Context):
    """See your personal fitness score and role recommendations for each event."""
    uid = str(ctx.author.id)
    data = member_stats.get(uid)

    if not data or not data.get("tc_level"):
        await ctx.send(embed=discord.Embed(
            description="You haven't entered your stats yet! Use `/mystats` first to register your TC level, power, heroes, etc.",
            color=discord.Color.orange()
        ), ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🎯 {ctx.author.display_name}'s Event Fitness Report",
        description="Your best role for each event based on your stats, heroes, and troops.",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)

    # Score for each event
    event_scores = []
    for event_key, formation in EVENT_FORMATIONS.items():
        result = _score_member_for_event(uid, data, event_key)
        event_scores.append((event_key, formation, result))

    event_scores.sort(key=lambda x: x[2]["score"], reverse=True)

    for event_key, formation, result in event_scores:
        score = result["score"]
        roles = result["roles"]
        issues = result["issues"]

        # Rating
        if score >= 200:
            rating = "⭐⭐⭐ Excellent"
        elif score >= 120:
            rating = "⭐⭐ Good"
        elif score >= 60:
            rating = "⭐ Average"
        else:
            rating = "❌ Not Ready"

        # Best role
        if "rally_leader" in roles:
            best_role = "🚩 Rally Leader"
        elif "garrison_captain" in roles:
            best_role = "🏰 Garrison Captain"
        elif "optimal_comp" in roles:
            best_role = "🏹 Optimized DPS"
        elif "joiner" in roles:
            best_role = "⚔️ Joiner"
        elif "balanced_comp" in roles:
            best_role = "⚔️ Fighter"
        else:
            best_role = "📋 Support"

        issue_text = f"\n⚠️ {issues[0]}" if issues else ""
        embed.add_field(
            name=f"{formation['emoji']} {formation['name']}",
            value=f"{rating} (Score: {score})\nBest Role: {best_role}{issue_text}",
            inline=True,
        )

    # Personal improvement tips
    tips = []
    if not data.get("top_heroes"):
        tips.append("Add your heroes via `/mystats` for better role matching")
    if data.get("infantry") is None:
        tips.append("Add troop counts via `/updatetroops` for composition scoring")
    tier_num = int(data.get("highest_tier", "T1").replace("T", "")) if data.get("highest_tier", "").startswith("T") else 1
    if tier_num < 8:
        tips.append(f"Push to T8+ troops for eligibility in KvK and Brawl")
    if data.get("tc_level", 0) < 25:
        tips.append(f"Push TC to 25 for maximum event access")

    if tips:
        embed.add_field(name="💡 Improvement Tips", value="\n".join(f"• {t}" for t in tips), inline=False)

    embed.set_footer(text="Scores based on power, TC, troop tier, heroes, and composition")
    await ctx.send(embed=embed, ephemeral=True)


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
