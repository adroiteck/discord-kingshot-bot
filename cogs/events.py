"""Events cog — event guides, cycle, tips."""
import discord
from discord.ext import commands
from discord import app_commands
from typing import List
import random
from datetime import datetime, timedelta, timezone

from utils import (
    PaginatorView, load_event_cycle, get_cycle_day, get_active_events,
    get_upcoming_events, event_name_autocomplete, save_data, load_data,
    utc_now, cooldown, EVENT_CYCLE_PATH
)
import json

# Event Guides Database
EVENT_GUIDES = {
    "swordland": {"name": "Swordland Showdown", "emoji": "⚔️", "color": 0xFF4500,
        "summary": "Bi-weekly alliance vs alliance capture event (1 hour). Rush Stables → Swordshrine → Sanctums.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Defense:** Zoe + Hilde + Saul\n**Joiners:** Chenko (best), Amane, Yeonwoo",
        "troops": "**Attack:** 50% Infantry / 20% Cav / 30% Archers\n**Garrison:** 60% Infantry / 20% Cav / 20% Archers",
        "tips": "• Split into Attackers (60%), Defenders (30%), Scouts (10%)\n• Capture Royal Stables FIRST for faster teleports\n• Personal score matters more than winning — farm points!"},
    "kvk": {"name": "Kingdom of Power (KvK)", "emoji": "👑", "color": 0xFFD700,
        "summary": "Cross-kingdom mega event with 4 phases: Matchmaking (48h) → Prep (5d) → Battle (12h) → Field Triage. Kingdom must be 70+ days old.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Garrison:** Zoe + Hilde + Saul\n**Joiners:** Chenko + Amane + Saul + Fahd",
        "troops": "**Attack:** 50% Infantry / 20% Cav / 30% Archers\n**Defense/Garrison:** 60% Infantry / 20% Cav / 20% Archers",
        "tips": "• Phase 1 (48h): Matchmaking\n• Phase 2 (5d): Prep — build, research, train\n• Phase 3 (12h): Battle Window (10:00-22:00 UTC)\n• Phase 4: Field Triage\n• Hoard speed-ups, Truegold, gems WEEKS in advance"},
    "bear": {"name": "Bear Hunt", "emoji": "🐻", "color": 0x8B4513,
        "summary": "Alliance rally event at the Pitfall building. Bear deals NO return damage — go full offense!",
        "heroes": "**Host (Gen 4+):** Amadeus + Petra + Rosa\n**Joiner S-tier:** Vivian (new!) > Chenko > Amane\n**Lethality bonus:** 25% from each hero",
        "troops": "**Host:** 1% Infantry / 10% Cavalry / 89% Archers\n**Joiner:** 0% Inf / 20% Cav / 80% Archer",
        "tips": "• Lethality is the #1 damage stat for Bear Hunt\n• Position towns close to Pitfall for faster rallies\n• Upgrade Pitfall to Level 5 for +5% Attack per level"},
    "merchant": {"name": "Merchant Empire", "emoji": "🏪", "color": 0x00CED1,
        "summary": "7-day trading event. Send caravans, escort allies, raid enemies. Max 4 caravans/day.",
        "heroes": "Use your strongest combat heroes for both escort and raiding.",
        "troops": "Full march with best available troops for raiding/defending.",
        "tips": "• Launch caravans 4-6 hours after reset\n• Target SSR (yellow) caravans\n• Save refresh vouchers — need 6 deep for guaranteed SSR\n• Assist 3 ally caravans daily"},
    "brawl": {"name": "Alliance Brawl", "emoji": "💥", "color": 0xDC143C,
        "summary": "Monthly 6.5-day alliance vs alliance cross-kingdom event. Top 20 alliances eligible.",
        "heroes": "Varies by daily task — use best heroes for the day's objective.",
        "troops": "Depends on daily challenge — plan ahead!",
        "tips": "• Save ALL Intel Missions for Day 2 & 4 (3,000 pts each!)\n• Day 5: Use all saved stamina for beast hunting\n• Day 6 is worth 4 horns — can win the ENTIRE brawl"},
    "oasis": {"name": "Oasis Island", "emoji": "🏝️", "color": 0x2E8B57,
        "summary": "Permanent base-building feature. Upgrade Fountain → clear cacti → build buff buildings.",
        "heroes": "N/A — not a combat event.", "troops": "N/A — not a combat event.",
        "tips": "• Upgrade Fountain of Life FIRST\n• Go LEFT first for highest chest density\n• Upgrade Reservoir to Level 4 for 2nd worker"},
    "mystic": {"name": "Mystic Trial", "emoji": "🔮", "color": 0x9400D3,
        "summary": "Permanent weekly dungeon. 5 attempts/day, 6 rotating dungeons. Breakthroughs are permanent!",
        "heroes": "Move strongest cavalry hero to Team 2 for split damage coverage.",
        "troops": "**Per Dungeon:**\n• Tomb of Shadows: 30/20/50\n• Frozen Abyss: 50/10/40\n• Inferno Core: 40/30/30\n• Storm Spire: 20/40/40\n• Verdant Maze: 30/30/40\n• Crystal Cavern: 50/20/30",
        "tips": "• MASSIVE RNG — always use all 5 daily attempts\n• Buy Mithril from shop first\n• Every breakthrough is permanent"},
    "governor": {"name": "Strongest Governor", "emoji": "🏆", "color": 0xB8860B,
        "summary": "Monthly 7-day cross-kingdom event. Different task focus each day. Requires months of prep!",
        "heroes": "**Day 2 & 7:** Hero Shards & Forgehammers\n**Day 3 & 5:** Skill books & research scrolls\n**Day 4 & 6:** Train troops",
        "troops": "• Day 1: City Construction\n• Day 2: Hero Development\n• Day 3: Basic Skills Up\n• Day 4: Combat Training\n• Day 5-7: Repeat cycle",
        "tips": "• Start hoarding resources MONTHS in advance\n• Save Mythic Hero Shards for Days 2 & 7\n• Top 2,000 governors get cross-kingdom rewards"},
    "mobilization": {"name": "Alliance Mobilization", "emoji": "📋", "color": 0x4682B4,
        "summary": "Bi-weekly alliance co-op missions. TC 10+ required, alliance needs 15+ members.",
        "heroes": "N/A — mission-based.", "troops": "N/A — mission-based.",
        "tips": "• Keep soldier training & beast hunting missions\n• Refresh low-value acceleration tasks\n• Use boosted slots on highest-value tasks"},
    "tri_alliance": {"name": "Tri-Alliance Clash", "emoji": "⚡", "color": 0xFF6347,
        "summary": "3 alliances compete in PvP territory control.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Defense:** Zoe + Hilde + Saul",
        "troops": "**Attack:** 50% Inf / 20% Cav / 30% Arch\n**Defense:** 60% Inf / 20% Cav / 20% Arch",
        "tips": "• Territory control wins — map positioning is critical\n• Spread forces across multiple fronts\n• Coordinate rally times with leadership"},
    "eternitys_reach": {"name": "Eternity's Reach", "emoji": "🌌", "color": 0x4B0082,
        "summary": "Solo progression dungeon with escalating difficulty and milestone rewards.",
        "heroes": "Use your strongest heroes for higher tier runs.",
        "troops": "Full march composition — adjust based on dungeon difficulty.",
        "tips": "• Progress as far as possible for milestone rewards\n• Each floor increases difficulty and rewards\n• Ranking rewards go to top performers"},
    "molten_fort": {"name": "Molten Fort", "emoji": "🔥", "color": 0xFF4500,
        "summary": "Alliance siege event — attack/defend fortresses for points.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Defense:** Zoe + Hilde + Saul",
        "troops": "**Attack:** 50% Inf / 20% Cav / 30% Arch\n**Defense:** 60% Inf / 20% Cav / 20% Arch",
        "tips": "• Concentrate forces on weak targets\n• Defend from counter-attacks\n• Capture and hold as long as possible"},
    "all_out": {"name": "All Out (Kill Event)", "emoji": "💀", "color": 0x000000,
        "summary": "Open PvP event — attack other players or shield up.",
        "heroes": "Your strongest combat marches.",
        "troops": "Full combat composition for attacking.",
        "tips": "• ⚠️ Shield UP if not participating!\n• Target lower Town Centers\n• Hit milestone benchmarks first\n• Pop Peace Shield immediately after milestones"},
    "windward_voyage": {"name": "Windward Voyage", "emoji": "⛵", "color": 0x1E90FF,
        "summary": "Sailing/exploration event with resource discovery and voyage milestones.",
        "heroes": "N/A — sailing event.", "troops": "N/A — sailing event.",
        "tips": "• Discover new locations for bonus resources\n• Plan sailing routes efficiently\n• Complete voyage milestones for chest rewards"},
    "suppress_mode": {"name": "Suppress Mode", "emoji": "🛡️", "color": 0x228B22,
        "summary": "PvE wave defense event — survive increasingly difficult enemy waves.",
        "heroes": "Build defensive-oriented hero lineups.",
        "troops": "**Defense-heavy:** 60% Inf / 20% Cav / 20% Arch",
        "tips": "• Each wave gets progressively harder\n• Use garrison positions for additional defense\n• Rank higher by surviving more waves"},
    "vikings_vengeance": {"name": "Vikings' Vengeance", "emoji": "🪓", "color": 0x8B0000,
        "summary": "Themed PvP raid event — raid opponent resources and defend yours.",
        "heroes": "**Attack:** Amadeus + Hilde + Marlin\n**Defense:** Zoe + Hilde + Saul",
        "troops": "**Attack:** 50% Inf / 20% Cav / 30% Arch\n**Defense:** 60% Inf / 20% Cav / 20% Arch",
        "tips": "• Target unshielded high-resource cities\n• Defend key resource buildings\n• Coordinate raid schedules with alliance"},
}

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
    "💡 Submit your troop compositions with /reportcomp to help optimize for events.",
    "💡 Use /mystats to register your stats — helps leadership plan events better.",
    "💡 Check /viewsuggestions to see community-tested strategies for your event.",
]


class EventSelectView(discord.ui.View):
    def __init__(self, event_guides: dict):
        super().__init__()
        options = [discord.SelectOption(label=f"{ev['emoji']} {ev['name']}", value=key) for key, ev in event_guides.items()][:25]
        sel = discord.ui.Select(placeholder="Choose an event to view...", min_values=1, max_values=1, options=options)
        sel.callback = self._select_callback
        self.add_item(sel)

    async def _select_callback(self, interaction: discord.Interaction):
        key = interaction.data["values"][0]
        ev = EVENT_GUIDES[key]
        embed = discord.Embed(title=f"{ev['emoji']} {ev['name']} — Complete Guide", description=ev["summary"], color=ev["color"])
        embed.add_field(name="🦸 Recommended Heroes", value=ev["heroes"], inline=False)
        embed.add_field(name="🪖 Troop Composition", value=ev["troops"], inline=False)
        embed.add_field(name="💡 Pro Tips", value=ev["tips"], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="events")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def list_events(self, ctx: commands.Context):
        """List all available event guides with interactive dropdown."""
        embed = discord.Embed(title="📅 Kingshot Event Guides", description="Select an event from the dropdown!", color=discord.Color.blue())
        event_list = "\n".join(f"{ev['emoji']} {ev['name']}" for ev in EVENT_GUIDES.values())
        embed.add_field(name="Available Events", value=event_list, inline=False)
        await ctx.send(embed=embed, view=EventSelectView(EVENT_GUIDES), ephemeral=True)

    @commands.hybrid_command(name="event")
    @app_commands.autocomplete(name=event_name_autocomplete)
    @app_commands.describe(name="The event to get a guide for")
    async def event_guide(self, ctx: commands.Context, *, name: str = ""):
        """Get the full guide for a specific event."""
        name = name.lower().strip()
        matched = None
        for key, ev in EVENT_GUIDES.items():
            if name in key or name in ev["name"].lower() or key.startswith(name):
                matched = (key, ev); break
        if not matched:
            suggestions = ", ".join(f"`{k}`" for k in EVENT_GUIDES.keys())
            await ctx.send(f"❌ Event `{name}` not found. Try: {suggestions}", ephemeral=True); return
        key, ev = matched
        embed = discord.Embed(title=f"{ev['emoji']} {ev['name']} — Complete Guide", description=ev["summary"], color=ev["color"])
        embed.add_field(name="🦸 Recommended Heroes", value=ev["heroes"], inline=False)
        embed.add_field(name="🪖 Troop Composition", value=ev["troops"], inline=False)
        embed.add_field(name="💡 Pro Tips", value=ev["tips"], inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="heroes")
    @app_commands.autocomplete(name=event_name_autocomplete)
    @app_commands.describe(name="The event to get hero picks for")
    async def hero_picks(self, ctx: commands.Context, *, name: str = ""):
        """Quick hero picks for an event."""
        name = name.lower().strip()
        for key, ev in EVENT_GUIDES.items():
            if name in key or name in ev["name"].lower() or key.startswith(name):
                embed = discord.Embed(title=f"{ev['emoji']} {ev['name']} — Hero Picks", description=ev["heroes"], color=ev["color"])
                await ctx.send(embed=embed, ephemeral=True); return
        await ctx.send("❌ Event not found. Use `/events` to see all options.", ephemeral=True)

    @commands.hybrid_command(name="troops")
    @app_commands.autocomplete(name=event_name_autocomplete)
    @app_commands.describe(name="The event to get troop composition for")
    async def troop_comp(self, ctx: commands.Context, *, name: str = ""):
        """Quick troop composition for an event."""
        name = name.lower().strip()
        for key, ev in EVENT_GUIDES.items():
            if name in key or name in ev["name"].lower() or key.startswith(name):
                embed = discord.Embed(title=f"{ev['emoji']} {ev['name']} — Troop Comp", description=ev["troops"], color=ev["color"])
                await ctx.send(embed=embed, ephemeral=True); return
        await ctx.send("❌ Event not found. Use `/events` to see all options.", ephemeral=True)

    @commands.hybrid_command(name="tip")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def random_tip(self, ctx: commands.Context):
        """Get a random Kingshot pro tip."""
        embed = discord.Embed(description=random.choice(DEFAULT_TIPS), color=discord.Color.green())
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="tips", aliases=["eventtips", "strategy"])
    @app_commands.autocomplete(event_name=event_name_autocomplete)
    @app_commands.describe(event_name="The event to get strategy tips for")
    async def event_tips(self, ctx: commands.Context, *, event_name: str = None):
        """Show full strategy & prep tips for an event."""
        if not event_name:
            await ctx.send("❓ Usage: `/tips <event name>` — e.g. `/tips bear hunt`", ephemeral=True); return
        cycle = load_event_cycle()
        search = event_name.lower().strip()
        matches = [ev for ev in cycle.get("events", []) if search in ev["name"].lower() or ev["name"].lower() in search]
        if not matches:
            for ev in cycle.get("events", []):
                if any(sw in ev["name"].lower().split() for sw in search.split()):
                    matches.append(ev)
        if not matches:
            await ctx.send(f"❌ No event found matching **{event_name}**.", ephemeral=True); return
        for ev in matches[:3]:
            reminder = ev.get("reminder", "No tips available.")
            embed = discord.Embed(title=f"{ev['emoji']} {ev['name']} — Strategy Guide", description=reminder[:4096], color=discord.Color.green())
            embed.add_field(name="Duration", value=f"{ev.get('duration_days', 1)} day(s)", inline=True)
            await ctx.send(embed=embed)

    @commands.hybrid_command(name="today", aliases=["active", "now"])
    async def today_events(self, ctx: commands.Context):
        """Show events active right now."""
        active = get_active_events()
        if not active:
            await ctx.send("📅 No events are active right now.", ephemeral=True); return
        embed = discord.Embed(title="🔴 Active Events Right Now", color=discord.Color.red(), timestamp=utc_now())
        for ev in active:
            brief = ev.get("reminder", "Event is active!").split("\n")[0][:200]
            embed.add_field(name=f"{ev['emoji']} {ev['name']}", value=brief, inline=False)
        embed.set_footer(text=f"Cycle Day {get_cycle_day() + 1}/28")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="nextevent", aliases=["next", "upcoming"])
    async def next_event(self, ctx: commands.Context):
        """Show the next upcoming events."""
        upcoming = get_upcoming_events(days_ahead=7)
        if not upcoming:
            await ctx.send("📅 No upcoming events in the next 7 days.", ephemeral=True); return
        embed = discord.Embed(title="📅 Upcoming Events (Next 7 Days)", color=discord.Color.blue(), timestamp=utc_now())
        for days_until, start_date, ev in upcoming[:10]:
            timing = "🔴 **Active NOW**" if days_until == 0 else f"⏰ **Tomorrow**" if days_until == 1 else f"📆 In **{days_until} days** ({start_date.strftime('%a %m/%d')})"
            embed.add_field(name=f"{ev['emoji']} {ev['name']}", value=f"{timing}\nDuration: {ev.get('duration_days', 1)}d", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="schedule", aliases=["cycle", "eventcycle"])
    async def show_schedule(self, ctx: commands.Context):
        """Show the full 4-week event cycle schedule."""
        cycle = load_event_cycle()
        today_cd = get_cycle_day()
        embed = discord.Embed(title="📋 4-Week Event Cycle", description=f"Today: Day {today_cd + 1}/28", color=discord.Color.purple())
        for week in range(4):
            week_events = []
            for ev in cycle.get("events", []):
                if ev.get("recurring_every_days"): continue
                start = ev["cycle_day_start"]
                if week * 7 <= start < (week + 1) * 7:
                    active = "🔴 " if today_cd == start else ""
                    week_events.append(f"{active}{ev['emoji']} **{ev['name']}** — Day {start+1} ({ev.get('duration_days',1)}d)")
            if week_events:
                icon = "▶️" if week * 7 <= today_cd < (week + 1) * 7 else "📅"
                embed.add_field(name=f"{icon} Week {week+1}", value="\n".join(week_events), inline=False)
        recurring = [ev for ev in cycle.get("events", []) if ev.get("recurring_every_days")]
        if recurring:
            embed.add_field(name="🔄 Recurring", value="\n".join(f"{ev['emoji']} **{ev['name']}** — every {ev['recurring_every_days']}d" for ev in recurring), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="setanchor")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R4 | Leadership", "R5 | Alliance Leader")
    async def set_anchor(self, ctx: commands.Context, date_str: str):
        """Set the cycle anchor date. Usage: /setanchor 2026-03-06"""
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            await ctx.send("❌ Invalid date format. Use: YYYY-MM-DD", ephemeral=True); return
        cycle = load_event_cycle()
        cycle["cycle_anchor"] = date_str
        with open(EVENT_CYCLE_PATH, "w") as f:
            json.dump(cycle, f, indent=2)
        await ctx.send(f"✅ Cycle anchor updated to **{date_str}**. Today is Cycle Day **{get_cycle_day() + 1}/28**.")

    @commands.hybrid_command(name="countdown")
    @app_commands.autocomplete(event_name=event_name_autocomplete)
    @app_commands.describe(event_name="Event to count down to")
    async def countdown_cmd(self, ctx: commands.Context, *, event_name: str):
        """Show countdown to a specific event."""
        upcoming = get_upcoming_events(days_ahead=28)
        search = event_name.lower()
        for days_until, start_date, ev in upcoming:
            if search in ev["name"].lower():
                if days_until == 0:
                    await ctx.send(f"{ev['emoji']} **{ev['name']}** is **active NOW**!", ephemeral=True)
                else:
                    await ctx.send(f"{ev['emoji']} **{ev['name']}** starts in **{days_until} day(s)** ({start_date.strftime('%a %b %d')})", ephemeral=True)
                return
        await ctx.send(f"❌ Event `{event_name}` not found in upcoming cycle.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Events(bot))
