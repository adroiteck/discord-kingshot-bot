"""War cog — rally, war schedule, signups, kill tracking, scouting, migration."""
import discord
from discord.ext import commands
from discord import app_commands
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from discord.ui import View, Button, button
import re

from utils import (
    load_data, save_data, utc_now, utc_from_iso, format_delta,
    parse_power, cooldown, timer_name_autocomplete, ConfirmView,
    PaginatorView, LEADER_ROLES, load_config, save_config, get_channel,
    resolve_timezone
)

# In-memory stores
war_timers = load_data("war_timers", {"timers": []})
war_signups = load_data("war_signups", {"signups": []})
kill_log = load_data("kill_log", {"kills": []})
scout_reports = load_data("scout_reports", {"reports": []})
territory_log = load_data("territory_log", {"entries": []})
rally_sessions = load_data("rally_sessions", {"rallies": []})


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
class RallyView(View):
    """Rally call with Join/Can't Make It buttons."""
    def __init__(self):
        super().__init__(timeout=3600)
        self.joined = []
        self.declined = []

    @button(label="✅ Join Rally", style=discord.ButtonStyle.success)
    async def join_button(self, interaction: discord.Interaction, btn: Button):
        uid = interaction.user.id
        if uid not in self.joined:
            self.joined.append(uid)
        if uid in self.declined:
            self.declined.remove(uid)
        await interaction.response.send_message(
            f"✅ You joined the rally! ({len(self.joined)} joined)", ephemeral=True
        )

    @button(label="❌ Can't Make It", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, btn: Button):
        uid = interaction.user.id
        if uid not in self.declined:
            self.declined.append(uid)
        if uid in self.joined:
            self.joined.remove(uid)
        await interaction.response.send_message("Noted — maybe next time!", ephemeral=True)


class WarSignupView(View):
    """War signup buttons for tracking attendance."""
    def __init__(self, event_name: str):
        super().__init__(timeout=86400)
        self.event_name = event_name
        self.confirmed = []
        self.declined = []
        self.maybe = []

    @button(label="✅ Sign Up", style=discord.ButtonStyle.success)
    async def signup_button(self, interaction: discord.Interaction, btn: Button):
        uid = interaction.user.id
        for lst in (self.declined, self.maybe):
            if uid in lst:
                lst.remove(uid)
        if uid not in self.confirmed:
            self.confirmed.append(uid)
        await interaction.response.send_message(
            f"✅ Signed up for **{self.event_name}**! ({len(self.confirmed)} confirmed)", ephemeral=True
        )

    @button(label="❌ Can't Make It", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, btn: Button):
        uid = interaction.user.id
        for lst in (self.confirmed, self.maybe):
            if uid in lst:
                lst.remove(uid)
        if uid not in self.declined:
            self.declined.append(uid)
        await interaction.response.defer()

    @button(label="❓ Maybe", style=discord.ButtonStyle.blurple)
    async def maybe_button(self, interaction: discord.Interaction, btn: Button):
        uid = interaction.user.id
        for lst in (self.confirmed, self.declined):
            if uid in lst:
                lst.remove(uid)
        if uid not in self.maybe:
            self.maybe.append(uid)
        await interaction.response.defer()


class RallyCoordView(View):
    """Enhanced rally coordinator with join/leave + march count tracking."""
    def __init__(self, rally_id: str, target: str, caller: str):
        super().__init__(timeout=7200)
        self.rally_id = rally_id
        self.target = target
        self.caller = caller
        self.participants: dict[int, dict] = {}  # uid -> {name, march_size}

    @button(label="⚔️ Join Rally", style=discord.ButtonStyle.success)
    async def join_btn(self, interaction: discord.Interaction, btn: Button):
        uid = interaction.user.id
        self.participants[uid] = {
            "name": interaction.user.display_name,
            "march_size": 0,
        }
        await interaction.response.send_message(
            f"✅ You joined the rally on **{self.target}**!\n"
            f"Use the **Set March** button to enter your march size.",
            ephemeral=True,
        )
        await self._update_embed(interaction)

    @button(label="📊 Set March", style=discord.ButtonStyle.primary)
    async def march_btn(self, interaction: discord.Interaction, btn: Button):
        uid = interaction.user.id
        if uid not in self.participants:
            await interaction.response.send_message("Join the rally first!", ephemeral=True)
            return
        await interaction.response.send_modal(MarchSizeModal(self))

    @button(label="🚪 Leave Rally", style=discord.ButtonStyle.danger)
    async def leave_btn(self, interaction: discord.Interaction, btn: Button):
        uid = interaction.user.id
        if uid in self.participants:
            del self.participants[uid]
        await interaction.response.send_message("Left the rally.", ephemeral=True)
        await self._update_embed(interaction)

    async def _update_embed(self, interaction: discord.Interaction):
        total_marches = sum(p.get("march_size", 0) for p in self.participants.values())
        roster = "\n".join(
            f"• {p['name']} — {p['march_size']:,} troops" if p['march_size'] else f"• {p['name']} — *march TBD*"
            for p in self.participants.values()
        ) or "No participants yet"
        embed = discord.Embed(
            title=f"🚨 RALLY: {self.target}",
            description=f"Called by **{self.caller}**\n\n**Roster ({len(self.participants)}):**\n{roster}",
            color=discord.Color.red(),
        )
        embed.add_field(name="📊 Total March Power", value=f"{total_marches:,} troops" if total_marches else "TBD", inline=True)
        embed.add_field(name="👥 Participants", value=str(len(self.participants)), inline=True)
        try:
            await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            import logging
            logging.getLogger("kingshot-bot").error(f"Failed to update rally embed: {e}")


class MarchSizeModal(discord.ui.Modal, title="Set March Size"):
    march = discord.ui.TextInput(
        label="March Size (troops)", placeholder="e.g. 250k or 250000", max_length=15, required=True
    )

    def __init__(self, rally_view: RallyCoordView):
        super().__init__()
        self.rally_view = rally_view

    async def on_submit(self, interaction: discord.Interaction):
        uid = interaction.user.id
        val = self.march.value.lower().replace(",", "").strip()
        mult = 1
        if val.endswith("k"):
            mult = 1_000; val = val[:-1]
        elif val.endswith("m"):
            mult = 1_000_000; val = val[:-1]
        try:
            size = int(float(val) * mult)
        except ValueError:
            await interaction.response.send_message("❌ Invalid number.", ephemeral=True)
            return
        if uid in self.rally_view.participants:
            self.rally_view.participants[uid]["march_size"] = size
        await interaction.response.send_message(f"✅ March size set to **{size:,}** troops.", ephemeral=True)
        await self.rally_view._update_embed(interaction)


# ---------------------------------------------------------------------------
# War Cog
# ---------------------------------------------------------------------------
class War(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- /rally ---
    @commands.hybrid_command(name="rally")
    @app_commands.default_permissions(manage_messages=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
    @app_commands.describe(details="Rally details (target, troops, etc.)")
    @cooldown(10)
    async def rally_call(self, ctx: commands.Context, *, details: str = "Rally up! Check war room."):
        """Send an urgent rally call."""
        rally_ch = get_channel(ctx.guild, "rally-calls")
        target_ch = rally_ch or ctx.channel
        embed = discord.Embed(
            title="🚨 RALLY CALL 🚨", description=details,
            color=discord.Color.red(), timestamp=utc_now(),
        )
        embed.set_footer(text=f"Called by {ctx.author.display_name}")
        view = RallyView()
        await target_ch.send("@everyone", embed=embed, view=view)
        if target_ch != ctx.channel:
            await ctx.send(f"✅ Rally call sent to #{target_ch.name}")

    # --- /rally_create --- NEW
    @commands.hybrid_command(name="rally_create")
    @app_commands.default_permissions(manage_messages=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
    @app_commands.describe(target="Target name/coords", notes="Additional notes")
    @cooldown(15)
    async def rally_create(self, ctx: commands.Context, target: str, *, notes: str = ""):
        """Create a coordinated rally with real-time join/leave tracking."""
        rally_id = f"r{int(utc_now().timestamp())}"
        embed = discord.Embed(
            title=f"🚨 RALLY: {target}",
            description=f"Called by **{ctx.author.display_name}**\n{notes}\n\n**Roster (0):**\nNo participants yet",
            color=discord.Color.red(),
        )
        embed.add_field(name="📊 Total March Power", value="TBD", inline=True)
        embed.add_field(name="👥 Participants", value="0", inline=True)
        view = RallyCoordView(rally_id, target, ctx.author.display_name)
        rally_ch = get_channel(ctx.guild, "rally-calls")
        target_ch = rally_ch or ctx.channel
        msg = await target_ch.send("@everyone", embed=embed, view=view)
        rally_sessions["rallies"].append({
            "id": rally_id, "target": target, "caller": ctx.author.display_name,
            "message_id": msg.id, "channel_id": target_ch.id, "created_at": utc_now().isoformat(),
        })
        save_data("rally_sessions", rally_sessions)
        if target_ch != ctx.channel:
            await ctx.send(f"✅ Rally created in #{target_ch.name}")

    # --- /rally_status --- NEW
    @commands.hybrid_command(name="rally_status")
    @cooldown(15)
    async def rally_status(self, ctx: commands.Context):
        """View active rallies."""
        recent = rally_sessions.get("rallies", [])[-5:]
        if not recent:
            await ctx.send("No recent rallies.", ephemeral=True)
            return
        embed = discord.Embed(title="🚨 Recent Rallies", color=discord.Color.red())
        for r in reversed(recent):
            embed.add_field(
                name=f"Target: {r['target']}",
                value=f"Called by {r['caller']} | {r['created_at'][:16]}",
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /warsched ---
    @commands.hybrid_command(name="warsched")
    @app_commands.default_permissions(manage_messages=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
    @app_commands.describe(schedule_text="War schedule details")
    @cooldown(15)
    async def war_schedule(self, ctx: commands.Context, *, schedule_text: str):
        """Post a war schedule."""
        sched_ch = get_channel(ctx.guild, "war-schedule")
        target_ch = sched_ch or ctx.channel
        embed = discord.Embed(
            title="🗓️ War Schedule", description=schedule_text.replace("\\n", "\n"),
            color=discord.Color.dark_red(), timestamp=utc_now(),
        )
        embed.set_footer(text=f"Updated by {ctx.author.display_name}")
        await target_ch.send(embed=embed)
        await ctx.send(f"✅ War schedule posted to #{target_ch.name}")

    # --- /warsignup ---
    @commands.hybrid_command(name="warsignup")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
    @app_commands.describe(event_name="The event to create signup for")
    @cooldown(15)
    async def war_signup(self, ctx: commands.Context, *, event_name: str):
        """Create a war signup embed with attendance tracking."""
        embed = discord.Embed(
            title=f"🚨 {event_name} Signup",
            description="Click the buttons below to sign up for this event!",
            color=discord.Color.blue(), timestamp=utc_now(),
        )
        embed.add_field(name="✅ Confirmed", value="0", inline=True)
        embed.add_field(name="❌ Declined", value="0", inline=True)
        embed.add_field(name="❓ Maybe", value="0", inline=True)
        embed.set_footer(text=f"Created by {ctx.author.display_name}")
        view = WarSignupView(event_name)
        msg = await ctx.send(embed=embed, view=view)
        war_signups["signups"].append({
            "event_name": event_name, "message_id": msg.id,
            "channel_id": ctx.channel.id, "created_at": utc_now().isoformat(),
            "confirmed": [], "declined": [], "maybe": [],
        })
        save_data("war_signups", war_signups)

    # --- /attendance ---
    @commands.hybrid_command(name="attendance")
    @cooldown(15)
    async def show_attendance(self, ctx: commands.Context, event_name: str = None):
        """Show attendance stats for a war signup event."""
        signups = war_signups.get("signups", [])
        if not signups:
            await ctx.send("📊 No war signups found.", ephemeral=True)
            return
        if event_name:
            signup = next((s for s in signups if s["event_name"].lower() == event_name.lower()), None)
            if not signup:
                await ctx.send(f"❌ Event `{event_name}` not found.", ephemeral=True)
                return
        else:
            signup = signups[-1]
        embed = discord.Embed(
            title=f"📊 Attendance: {signup['event_name']}",
            color=discord.Color.green(), timestamp=utc_now(),
        )
        embed.add_field(name="✅ Confirmed", value=str(len(signup.get("confirmed", []))), inline=True)
        embed.add_field(name="❌ Declined", value=str(len(signup.get("declined", []))), inline=True)
        embed.add_field(name="❓ Maybe", value=str(len(signup.get("maybe", []))), inline=True)
        total = len(signup.get("confirmed", [])) + len(signup.get("declined", [])) + len(signup.get("maybe", []))
        embed.add_field(name="Total Signups", value=str(total), inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    # --- /timers ---
    @commands.hybrid_command(name="timers", aliases=["timer"])
    @cooldown(10)
    async def view_timers(self, ctx: commands.Context):
        """View active event timers."""
        active = [t for t in war_timers.get("timers", []) if utc_from_iso(t["time"]) > utc_now()]
        if not active:
            await ctx.send("⏰ No active timers. Officers can set them with `/settimer`", ephemeral=True)
            return
        embed = discord.Embed(title="⏰ Active Event Timers", color=discord.Color.orange())
        for t in sorted(active, key=lambda x: x["time"]):
            target = utc_from_iso(t["time"])
            delta = target - utc_now()
            embed.add_field(
                name=t["name"],
                value=f"⏱️ **{format_delta(delta)}** remaining\n📅 {target.strftime('%b %d, %I:%M %p')} UTC",
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /settimer ---
    @commands.hybrid_command(name="settimer")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
    @app_commands.describe(args="Name | datetime | tz (e.g. Swordland | 2026-03-15 20:00 | EST)")
    async def set_timer(self, ctx: commands.Context, *, args: str):
        """Set an event timer. Format: Name | YYYY-MM-DD HH:MM | timezone (tz optional)"""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 2:
            await ctx.send("❌ Usage: `/settimer Event Name | YYYY-MM-DD HH:MM` (optionally add `| EST` or your timezone)")
            return
        name = parts[0]
        time_str = parts[1]
        tz_input = parts[2] if len(parts) >= 3 else None

        # Parse time
        try:
            target = utc_from_iso(time_str)
        except ValueError:
            try:
                target = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
            except ValueError:
                await ctx.send("❌ Invalid date format. Use: `YYYY-MM-DD HH:MM`")
                return

        # Apply timezone if provided, otherwise check user's stored tz, fallback to UTC
        if tz_input:
            resolved_tz = resolve_timezone(tz_input)
            if not resolved_tz:
                await ctx.send(f"❌ Unknown timezone `{tz_input}`. Try: EST, PST, UTC, America/New_York", ephemeral=True)
                return
        else:
            # Try user's stored timezone
            user_tz_data = load_data("user_timezones", {})
            resolved_tz = user_tz_data.get(str(ctx.author.id))

        if resolved_tz:
            from zoneinfo import ZoneInfo
            local_tz = ZoneInfo(resolved_tz)
            if target.tzinfo is None:
                target = target.replace(tzinfo=local_tz)
            # Convert to UTC for storage
            target = target.astimezone(timezone.utc)
            tz_note = f" ({resolved_tz})"
        else:
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            tz_note = " (UTC)"

        if target <= utc_now():
            await ctx.send("❌ That time is in the past!", ephemeral=True)
            return

        war_timers.setdefault("timers", []).append({
            "name": name, "time": target.isoformat(), "set_by": ctx.author.display_name,
        })
        save_data("war_timers", war_timers)
        delta = target - utc_now()
        await ctx.send(f"✅ Timer set: **{name}** in ~{format_delta(delta)} ({target.strftime('%b %d, %I:%M %p')} UTC){tz_note}")

    # --- /deltimer ---
    @commands.hybrid_command(name="deltimer")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership", "R3 | TC25+")
    @app_commands.autocomplete(name=timer_name_autocomplete)
    @app_commands.describe(name="The timer to delete")
    async def del_timer(self, ctx: commands.Context, *, name: str):
        """Delete an event timer."""
        timers = war_timers.get("timers", [])
        war_timers["timers"] = [t for t in timers if t["name"].lower() != name.lower()]
        save_data("war_timers", war_timers)
        await ctx.send(f"✅ Timer `{name}` deleted.")

    # --- /reportkill --- NEW
    @commands.hybrid_command(name="reportkill")
    @app_commands.describe(
        target="Target player name", troops_killed="Troops killed (e.g. 50k)",
        power_destroyed="Power destroyed (e.g. 2m)", notes="Optional notes"
    )
    @cooldown(5)
    async def report_kill(self, ctx: commands.Context, target: str, troops_killed: str, power_destroyed: str = "0", *, notes: str = ""):
        """Report a PvP kill for tracking."""
        tk = parse_power(troops_killed) or 0
        pd = parse_power(power_destroyed) or 0
        entry = {
            "reporter_id": ctx.author.id, "reporter": ctx.author.display_name,
            "target": target, "troops_killed": tk, "power_destroyed": pd,
            "notes": notes, "timestamp": utc_now().isoformat(),
        }
        kill_log["kills"].append(entry)
        save_data("kill_log", kill_log)
        embed = discord.Embed(
            title="💀 Kill Reported!", color=discord.Color.dark_red(),
            description=f"**Target:** {target}\n**Troops Killed:** {tk:,}\n**Power Destroyed:** {pd:,}",
        )
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)
        embed.set_footer(text=f"Reported by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # --- /killers --- NEW
    @commands.hybrid_command(name="killers")
    @cooldown(30)
    async def killers_leaderboard(self, ctx: commands.Context):
        """Show kill leaderboard."""
        kills = kill_log.get("kills", [])
        if not kills:
            await ctx.send("💀 No kills reported yet. Use `/reportkill` after PvP!", ephemeral=True)
            return
        # Aggregate by reporter
        agg: dict[int, dict] = {}
        for k in kills:
            rid = k["reporter_id"]
            if rid not in agg:
                agg[rid] = {"name": k["reporter"], "total_kills": 0, "total_power": 0, "count": 0}
            agg[rid]["total_kills"] += k["troops_killed"]
            agg[rid]["total_power"] += k["power_destroyed"]
            agg[rid]["count"] += 1
        ranked = sorted(agg.values(), key=lambda x: x["total_kills"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(ranked[:15]):
            m = medals[i] if i < 3 else f"**{i+1}.**"
            lines.append(f"{m} {r['name']} — {r['total_kills']:,} killed | {r['total_power']:,} power | {r['count']} reports")
        embed = discord.Embed(
            title="💀 Kill Leaderboard", description="\n".join(lines),
            color=discord.Color.dark_red(),
        )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /mykills --- NEW
    @commands.hybrid_command(name="mykills")
    @cooldown(15)
    async def my_kills(self, ctx: commands.Context):
        """View your kill history."""
        kills = [k for k in kill_log.get("kills", []) if k["reporter_id"] == ctx.author.id]
        if not kills:
            await ctx.send("💀 No kills reported. Use `/reportkill` after PvP!", ephemeral=True)
            return
        total_tk = sum(k["troops_killed"] for k in kills)
        total_pd = sum(k["power_destroyed"] for k in kills)
        embed = discord.Embed(
            title=f"💀 {ctx.author.display_name}'s Kill Log",
            description=f"**Total Reports:** {len(kills)}\n**Troops Killed:** {total_tk:,}\n**Power Destroyed:** {total_pd:,}",
            color=discord.Color.dark_red(),
        )
        for k in kills[-5:]:
            ts = k["timestamp"][:10]
            embed.add_field(
                name=f"vs {k['target']} ({ts})",
                value=f"💀 {k['troops_killed']:,} killed | ⚡ {k['power_destroyed']:,} power",
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /scout --- NEW
    @commands.hybrid_command(name="scout")
    @app_commands.describe(
        target="Target player/alliance", power="Their estimated power (e.g. 50m)",
        troops="Troop details", notes="Additional intel"
    )
    @cooldown(10)
    async def scout_report(self, ctx: commands.Context, target: str, power: str = "0", *, troops: str = "", notes: str = ""):
        """Submit a scouting report on an enemy player or alliance."""
        pwr = parse_power(power) or 0
        entry = {
            "scout_id": ctx.author.id, "scout": ctx.author.display_name,
            "target": target, "power": pwr, "troops": troops, "notes": notes,
            "timestamp": utc_now().isoformat(),
        }
        scout_reports["reports"].append(entry)
        save_data("scout_reports", scout_reports)
        embed = discord.Embed(
            title="🔍 Scout Report Filed", color=discord.Color.teal(),
            description=f"**Target:** {target}\n**Est. Power:** {pwr:,}\n**Troops:** {troops or 'N/A'}",
        )
        if notes:
            embed.add_field(name="Intel Notes", value=notes, inline=False)
        embed.set_footer(text=f"Scouted by {ctx.author.display_name}")
        intel_ch = get_channel(ctx.guild, "intel")
        target_ch = intel_ch or ctx.channel
        await target_ch.send(embed=embed)
        if target_ch != ctx.channel:
            await ctx.send("✅ Scout report filed to #intel", ephemeral=True)

    # --- /threat --- NEW
    @commands.hybrid_command(name="threat")
    @app_commands.describe(target="Target to search for in scout reports")
    @cooldown(15)
    async def threat_assessment(self, ctx: commands.Context, *, target: str = ""):
        """View scouting intel on a target or all recent reports."""
        reports = scout_reports.get("reports", [])
        if target:
            reports = [r for r in reports if target.lower() in r["target"].lower()]
        if not reports:
            await ctx.send("🔍 No scout reports found. Use `/scout` to file one!", ephemeral=True)
            return
        embed = discord.Embed(title=f"🔍 Intel: {target or 'All Targets'}", color=discord.Color.teal())
        for r in reports[-10:]:
            embed.add_field(
                name=f"{r['target']} — {r['timestamp'][:10]}",
                value=f"⚡ {r['power']:,} | 🪖 {r['troops'] or 'N/A'}\n📝 {r.get('notes', '-')}\n🔍 By {r['scout']}",
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /scouts --- NEW
    @commands.hybrid_command(name="scouts")
    @cooldown(30)
    async def scouts_leaderboard(self, ctx: commands.Context):
        """Show top scouts by report count."""
        reports = scout_reports.get("reports", [])
        if not reports:
            await ctx.send("🔍 No scout reports yet.", ephemeral=True)
            return
        agg: dict[int, dict] = {}
        for r in reports:
            sid = r["scout_id"]
            if sid not in agg:
                agg[sid] = {"name": r["scout"], "count": 0}
            agg[sid]["count"] += 1
        ranked = sorted(agg.values(), key=lambda x: x["count"], reverse=True)
        lines = [f"**{i+1}.** {r['name']} — {r['count']} reports" for i, r in enumerate(ranked[:10])]
        embed = discord.Embed(
            title="🔍 Top Scouts", description="\n".join(lines), color=discord.Color.teal()
        )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /migration --- NEW
    @commands.hybrid_command(name="migration")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
    @app_commands.describe(
        destination="Target kingdom/zone", reason="Why we're migrating", eta="Expected time"
    )
    async def migration(self, ctx: commands.Context, destination: str, *, reason: str = "", eta: str = "TBD"):
        """Announce a migration plan."""
        embed = discord.Embed(
            title="🗺️ Migration Announcement", color=discord.Color.dark_gold(),
            description=f"**Destination:** {destination}\n**Reason:** {reason or 'Strategic relocation'}\n**ETA:** {eta}",
            timestamp=utc_now(),
        )
        embed.set_footer(text=f"Announced by {ctx.author.display_name}")
        announce_ch = get_channel(ctx.guild, "announcements")
        target_ch = announce_ch or ctx.channel
        await target_ch.send("@everyone", embed=embed)
        if target_ch != ctx.channel:
            await ctx.send(f"✅ Migration announced in #{target_ch.name}")

    # --- /territory --- NEW
    @commands.hybrid_command(name="territory")
    @app_commands.describe(
        zone="Territory zone name/number", status="captured/lost/contested",
        notes="Additional details"
    )
    @cooldown(10)
    async def territory(self, ctx: commands.Context, zone: str, status: str = "captured", *, notes: str = ""):
        """Log territory status change."""
        # Input validation
        if len(zone) > 50:
            await ctx.send("❌ Zone name too long (max 50 characters).", ephemeral=True); return
        if not re.match(r'^[\w\s\-\.#]+$', zone):
            await ctx.send("❌ Zone name contains invalid characters.", ephemeral=True); return
        if status.lower() not in ("captured", "lost", "contested"):
            await ctx.send("❌ Status must be `captured`, `lost`, or `contested`.", ephemeral=True); return
        entry = {
            "reporter_id": ctx.author.id, "reporter": ctx.author.display_name,
            "zone": zone, "status": status.lower(), "notes": notes,
            "timestamp": utc_now().isoformat(),
        }
        territory_log["entries"].append(entry)
        save_data("territory_log", territory_log)
        status_emoji = {"captured": "🟢", "lost": "🔴", "contested": "🟡"}.get(status.lower(), "⚪")
        embed = discord.Embed(
            title=f"{status_emoji} Territory Update: {zone}",
            description=f"**Status:** {status.title()}\n**Notes:** {notes or 'None'}",
            color=discord.Color.green() if status.lower() == "captured" else discord.Color.red(),
        )
        embed.set_footer(text=f"Logged by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # --- /territory_report --- NEW
    @commands.hybrid_command(name="territory_report")
    @cooldown(30)
    async def territory_report(self, ctx: commands.Context):
        """View territory status summary."""
        entries = territory_log.get("entries", [])
        if not entries:
            await ctx.send("🗺️ No territory data. Use `/territory` to log changes!", ephemeral=True)
            return
        # Get latest status per zone
        latest: dict[str, dict] = {}
        for e in entries:
            latest[e["zone"]] = e
        embed = discord.Embed(title="🗺️ Territory Report", color=discord.Color.dark_gold())
        for zone, e in sorted(latest.items()):
            status_emoji = {"captured": "🟢", "lost": "🔴", "contested": "🟡"}.get(e["status"], "⚪")
            embed.add_field(
                name=f"{status_emoji} {zone}",
                value=f"Status: {e['status'].title()} | Last update: {e['timestamp'][:10]}",
                inline=True,
            )
        await ctx.send(embed=embed, ephemeral=True)


    # --- /warhistory --- NEW (Phase 2: War history)
    @commands.hybrid_command(name="warhistory")
    @app_commands.describe(limit="Number of recent events to show (default 10)")
    @cooldown(30)
    async def war_history(self, ctx: commands.Context, limit: int = 10):
        """View history of war signups, rallies, and territory changes."""
        limit = min(max(limit, 1), 25)
        embeds = []

        # Page 1: Recent war signups
        signups = war_signups.get("signups", [])
        e1 = discord.Embed(title="📜 War History — Signups", color=discord.Color.dark_red())
        if signups:
            for s in reversed(signups[-limit:]):
                confirmed = len(s.get("confirmed", []))
                declined = len(s.get("declined", []))
                maybe = len(s.get("maybe", []))
                total = confirmed + declined + maybe
                ts = s.get("created_at", "?")[:10]
                e1.add_field(
                    name=f"⚔️ {s['event_name']} ({ts})",
                    value=f"✅ {confirmed} | ❌ {declined} | ❓ {maybe} | Total: {total}",
                    inline=False,
                )
        else:
            e1.description = "No war signups recorded yet."
        embeds.append(e1)

        # Page 2: Recent rallies
        rallies = rally_sessions.get("rallies", [])
        e2 = discord.Embed(title="📜 War History — Rallies", color=discord.Color.red())
        if rallies:
            for r in reversed(rallies[-limit:]):
                ts = r.get("created_at", "?")[:16]
                e2.add_field(
                    name=f"🚨 {r['target']} ({ts})",
                    value=f"Called by {r['caller']}",
                    inline=False,
                )
        else:
            e2.description = "No rallies recorded yet."
        embeds.append(e2)

        # Page 3: Recent territory changes
        entries = territory_log.get("entries", [])
        e3 = discord.Embed(title="📜 War History — Territory", color=discord.Color.dark_gold())
        if entries:
            for e in reversed(entries[-limit:]):
                emoji = {"captured": "🟢", "lost": "🔴", "contested": "🟡"}.get(e["status"], "⚪")
                ts = e.get("timestamp", "?")[:10]
                e3.add_field(
                    name=f"{emoji} {e['zone']} — {e['status'].title()} ({ts})",
                    value=f"By {e['reporter']} | {e.get('notes', '') or 'No notes'}",
                    inline=False,
                )
        else:
            e3.description = "No territory changes recorded yet."
        embeds.append(e3)

        view = PaginatorView(embeds)
        await ctx.send(embed=embeds[0], view=view, ephemeral=True)

    # --- /warlog --- NEW (Phase 2: Enhanced error context for war operations)
    @commands.hybrid_command(name="warlog")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role(*LEADER_ROLES)
    @cooldown(30)
    async def war_log(self, ctx: commands.Context):
        """Show a combined war activity log for leadership review."""
        embed = discord.Embed(title="📋 War Activity Summary", color=discord.Color.dark_red(), timestamp=utc_now())

        # Active timers count
        active_timers = [t for t in war_timers.get("timers", []) if utc_from_iso(t["time"]) > utc_now()]
        embed.add_field(name="⏰ Active Timers", value=str(len(active_timers)), inline=True)

        # Recent signups
        signups = war_signups.get("signups", [])
        embed.add_field(name="📝 Total Signups", value=str(len(signups)), inline=True)

        # Rally count
        rallies = rally_sessions.get("rallies", [])
        embed.add_field(name="🚨 Total Rallies", value=str(len(rallies)), inline=True)

        # Kill stats
        kills = kill_log.get("kills", [])
        total_killed = sum(k.get("troops_killed", 0) for k in kills)
        embed.add_field(name="💀 Total Kills Logged", value=f"{len(kills)} reports ({total_killed:,} troops)", inline=False)

        # Scout stats
        scouts = scout_reports.get("reports", [])
        embed.add_field(name="🔍 Scout Reports", value=str(len(scouts)), inline=True)

        # Territory
        territories = territory_log.get("entries", [])
        captured = sum(1 for e in territories if e["status"] == "captured")
        lost = sum(1 for e in territories if e["status"] == "lost")
        embed.add_field(name="🗺️ Territory", value=f"🟢 {captured} captured | 🔴 {lost} lost", inline=True)

        embed.set_footer(text="Use /warhistory for detailed event-by-event history")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(War(bot))
