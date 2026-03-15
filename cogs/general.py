"""General cog — profiles, codes, timezones, leaderboard, power tracking."""
import discord
from discord.ext import commands
from discord import app_commands
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from utils import (
    PaginatorView, RolePanelView, load_data, save_data, utc_now, utc_from_iso,
    parse_power, resolve_timezone, timezone_autocomplete, ROLE_COLORS,
    format_delta, event_name_autocomplete
)

# In-memory stores (loaded once, persisted on change)
gift_codes = load_data("gift_codes", {"codes": []})
user_profiles = load_data("profiles", {})
user_timezones = load_data("user_timezones", {})
power_history = load_data("power_history", {})
reminder_optins = load_data("reminder_optins", {"users": []})


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- /help ---
    @commands.hybrid_command(name="help")
    async def help_command(self, ctx: commands.Context):
        """Show all available bot commands organized by category."""
        pages = []
        # Page 1: Member commands
        e1 = discord.Embed(title="🤖 Kingshot Bot — Command Guide", description="All commands. 🔒 = requires special roles.", color=discord.Color.blurple())
        e1.add_field(name="📚 Event Guides", value="`/event` `/events` `/heroes` `/troops` `/tips` `/today` `/nextevent` `/schedule` `/countdown`", inline=False)
        e1.add_field(name="👤 Profile & Info", value="`/profile` `/setpower` `/setign` `/memberinfo` `/serverinfo` `/leaderboard` `/powerhistory`", inline=False)
        e1.add_field(name="🎁 Gift Codes", value="`/codes` `/addcode` `/expirecode` `/redeemcode` `/codehistory`", inline=False)
        e1.add_field(name="🎮 Game Link", value="`/register` `/whoami` `/lookup` `/unregister`", inline=False)
        e1.add_field(name="⏰ Timers & TZ", value="`/timers` `/settimer` `/deltimer` `/countdown` `/timezone` `/localtime` `/remindme`", inline=False)
        e1.add_field(name="📊 Stats & Optimize", value="`/mystats` `/updatetroops` `/alliancestats` `/eventready` `/optimize` `/myfit`", inline=False)
        e1.set_footer(text="Page 1/3 — Member Commands")
        pages.append(e1)

        # Page 2: War & Intel
        e2 = discord.Embed(title="🤖 Kingshot Bot — War & Intel", color=discord.Color.red())
        e2.add_field(name="⚔️ War Commands 🔒", value="`/rally` `/warsched` `/warsignup` `/attendance` `/rally_create` `/rally_status`", inline=False)
        e2.add_field(name="💀 Kill Tracking", value="`/reportkill` `/killers` `/mykills`", inline=False)
        e2.add_field(name="🔍 Scouting & Intel", value="`/scout` `/threat` `/scouts`", inline=False)
        e2.add_field(name="🗺️ Migration & Territory", value="`/migration` `/territory` `/territory_report`", inline=False)
        e2.add_field(name="💡 Community", value="`/suggest` `/viewsuggestions` `/reportcomp` `/troopstats` `/approvesuggestion`", inline=False)
        e2.set_footer(text="Page 2/3 — War & Intel")
        pages.append(e2)

        # Page 3: Admin & Moderation
        e3 = discord.Embed(title="🤖 Kingshot Bot — Admin & Moderation", color=discord.Color.dark_grey())
        e3.add_field(name="📢 Announcements 🔒", value="`/announce` `/listannouncements` `/toggleannouncement`", inline=False)
        e3.add_field(name="👥 Role Management 🔒", value="`/promote` `/demote` `/rolepanel`", inline=False)
        e3.add_field(name="🛡️ Moderation 🔒", value="`/kick` `/mute` `/unmute` `/clear`", inline=False)
        e3.add_field(name="🔧 Admin 🔒", value="`/setup` `/bot_health` `/data_stats`", inline=False)
        e3.add_field(name="🏆 Achievements", value="`/achievements` `/mvp`", inline=False)
        e3.set_footer(text="Page 3/3 — Admin & Moderation")
        pages.append(e3)

        if len(pages) == 1:
            await ctx.send(embed=pages[0], ephemeral=True)
        else:
            await ctx.send(embed=pages[0], view=PaginatorView(pages), ephemeral=True)

    # --- /profile ---
    @commands.hybrid_command(name="profile")
    @app_commands.describe(member="The member to view (leave empty for yourself)")
    async def profile(self, ctx: commands.Context, member: discord.Member = None):
        """View your profile or another member's profile."""
        viewing_self = member is None
        member = member or ctx.author
        uid = str(member.id)
        data = user_profiles.get(uid, {})
        embed = discord.Embed(title=f"👤 {member.display_name}'s Profile",
            color=member.top_role.color if member.top_role.color != discord.Color.default() else discord.Color.blue(), timestamp=utc_now())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="🎮 In-Game Name", value=data.get("ign", "*Not set* — use `/setign`"), inline=True)
        embed.add_field(name="⚡ Power", value=f"{data.get('power', 0):,}" if data.get("power") else "*Not set*", inline=True)
        embed.add_field(name="🏷️ Roles", value=", ".join(r.name for r in member.roles if r.name != "@everyone") or "None", inline=False)
        embed.add_field(name="📅 Joined", value=member.joined_at.strftime("%b %d, %Y") if member.joined_at else "Unknown", inline=True)
        await ctx.send(embed=embed, ephemeral=viewing_self)

    # --- /setpower ---
    @commands.hybrid_command(name="setpower")
    @app_commands.describe(power="Your power level (e.g. 25m, 5000000, 1.2b)")
    async def set_power(self, ctx: commands.Context, power: str):
        """Set your power level."""
        power_val = parse_power(power)
        if power_val is None:
            await ctx.send("❌ Invalid power value. Examples: `25m`, `5000000`", ephemeral=True); return
        uid = str(ctx.author.id)
        if uid not in user_profiles: user_profiles[uid] = {}
        old_power = user_profiles[uid].get("power", 0)
        if uid not in power_history: power_history[uid] = []
        power_history[uid].append({"timestamp": utc_now().isoformat(), "old_power": old_power, "new_power": power_val})
        # Keep only last 100 entries per user
        if len(power_history[uid]) > 100: power_history[uid] = power_history[uid][-100:]
        save_data("power_history", power_history)
        user_profiles[uid]["power"] = power_val
        save_data("profiles", user_profiles)
        await ctx.send(f"✅ Power set to **{power_val:,}**!", ephemeral=True)

    # --- /setign ---
    @commands.hybrid_command(name="setign")
    @app_commands.describe(ign="Your in-game name")
    async def set_ign(self, ctx: commands.Context, *, ign: str):
        """Set your in-game name."""
        uid = str(ctx.author.id)
        if uid not in user_profiles: user_profiles[uid] = {}
        user_profiles[uid]["ign"] = ign.strip()
        save_data("profiles", user_profiles)
        await ctx.send(f"✅ In-game name set to **{ign.strip()}**!", ephemeral=True)

    # --- /leaderboard ---
    @commands.hybrid_command(name="leaderboard", aliases=["lb", "top"])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def leaderboard(self, ctx: commands.Context):
        """Show the alliance power leaderboard (paginated)."""
        ranked = sorted([(uid, data) for uid, data in user_profiles.items() if data.get("power", 0) > 0],
            key=lambda x: x[1]["power"], reverse=True)
        if not ranked:
            await ctx.send("📊 No one has set their power yet! Use `/setpower`", ephemeral=True); return
        embeds = []
        medals = ["🥇", "🥈", "🥉"]
        per_page = 10
        for page_num in range(0, len(ranked), per_page):
            page_data = ranked[page_num:page_num + per_page]
            lines = []
            for i, (uid, data) in enumerate(page_data):
                rank = page_num + i
                medal = medals[rank] if rank < 3 else f"**{rank + 1}.**"
                name = data.get("ign", f"<@{uid}>")
                lines.append(f"{medal} {name} — ⚡ {data['power']:,}")
            embed = discord.Embed(title="🏆 Alliance Power Leaderboard", description="\n".join(lines), color=discord.Color.gold())
            embed.set_footer(text=f"Page {page_num // per_page + 1}/{(len(ranked) + per_page - 1) // per_page} | {len(ranked)} members")
            embeds.append(embed)
        await ctx.send(embed=embeds[0], view=PaginatorView(embeds) if len(embeds) > 1 else None, ephemeral=True)

    # --- /serverinfo ---
    @commands.hybrid_command(name="serverinfo", aliases=["server"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def server_info(self, ctx: commands.Context):
        """Show server statistics."""
        g = ctx.guild
        embed = discord.Embed(title=f"📊 {g.name}", color=discord.Color.blue(), timestamp=utc_now())
        if g.icon: embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="👥 Members", value=str(g.member_count), inline=True)
        embed.add_field(name="💬 Channels", value=str(len(g.text_channels)), inline=True)
        embed.add_field(name="🔊 Voice", value=str(len(g.voice_channels)), inline=True)
        embed.add_field(name="📅 Created", value=g.created_at.strftime("%b %d, %Y"), inline=True)
        powers = [d["power"] for d in user_profiles.values() if d.get("power", 0) > 0]
        if powers:
            embed.add_field(name="⚡ Alliance Power", value=f"Total: **{sum(powers):,}**\nAvg: **{sum(powers)//len(powers):,}**\nTracked: **{len(powers)}**", inline=False)
        await ctx.send(embed=embed)

    # --- /memberinfo ---
    @commands.hybrid_command(name="memberinfo")
    @app_commands.describe(member="The member to view")
    async def member_info(self, ctx: commands.Context, member: discord.Member = None):
        """Show info about a member."""
        member = member or ctx.author
        data = user_profiles.get(str(member.id), {})
        embed = discord.Embed(title=f"👤 {member.display_name}", color=member.top_role.color if member.top_role.color != discord.Color.default() else discord.Color.blue())
        embed.set_thumbnail(url=member.display_avatar.url)
        if data.get("ign"): embed.add_field(name="🎮 IGN", value=data["ign"], inline=True)
        if data.get("power"): embed.add_field(name="⚡ Power", value=f"{data['power']:,}", inline=True)
        embed.add_field(name="📅 Joined", value=member.joined_at.strftime("%b %d, %Y") if member.joined_at else "Unknown", inline=True)
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        embed.add_field(name="🏷️ Roles", value=", ".join(roles) if roles else "None", inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    # --- Gift Codes ---
    @commands.hybrid_command(name="codes")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def view_codes(self, ctx: commands.Context):
        """View all active gift codes."""
        active = [c for c in gift_codes.get("codes", []) if not c.get("expired")]
        if not active:
            await ctx.send("🎁 No active gift codes right now. Use `/addcode` when you find one!", ephemeral=True); return
        embeds = []
        for i in range(0, len(active), 5):
            embed = discord.Embed(title="🎁 Active Gift Codes", color=discord.Color.from_str("#FF69B4"))
            for code in active[i:i+5]:
                embed.add_field(name=f"📋 `{code['code']}`", value=f"📦 {code.get('rewards', 'Unknown')}\n👤 {code.get('added_by', 'Unknown')}", inline=False)
            embeds.append(embed)
        await ctx.send(embed=embeds[0], view=PaginatorView(embeds) if len(embeds) > 1 else None, ephemeral=True)

    @commands.hybrid_command(name="addcode")
    @app_commands.describe(args="Code and rewards: CODE123 | 500 gems, 2 speedups")
    async def add_code(self, ctx: commands.Context, *, args: str):
        """Submit a new gift code."""
        parts = args.split("|", 1)
        code = parts[0].strip().upper()
        rewards = parts[1].strip() if len(parts) > 1 else "Rewards unknown"
        existing = [c["code"] for c in gift_codes.get("codes", [])]
        if code in existing:
            await ctx.send(f"⚠️ Code `{code}` already submitted!", ephemeral=True); return
        gift_codes.setdefault("codes", []).append({"code": code, "rewards": rewards, "added_by": ctx.author.display_name, "added_at": utc_now().isoformat(), "expired": False})
        save_data("gift_codes", gift_codes)
        await ctx.send(f"✅ Gift code `{code}` added! Rewards: {rewards}")
        gift_ch = discord.utils.get(ctx.guild.text_channels, name="gift-codes")
        if gift_ch and gift_ch != ctx.channel:
            embed = discord.Embed(title="🎁 New Gift Code!", color=discord.Color.from_str("#FF69B4"))
            embed.add_field(name="Code", value=f"```{code}```", inline=False)
            embed.add_field(name="Rewards", value=rewards, inline=False)
            embed.set_footer(text=f"Submitted by {ctx.author.display_name}")
            await gift_ch.send(embed=embed)

    @commands.hybrid_command(name="expirecode")
    @app_commands.describe(code="The gift code to expire")
    async def expire_code(self, ctx: commands.Context, *, code: str):
        """Mark a gift code as expired."""
        code = code.strip().upper()
        for c in gift_codes.get("codes", []):
            if c["code"] == code:
                c["expired"] = True; save_data("gift_codes", gift_codes)
                await ctx.send(f"✅ Code `{code}` expired.", ephemeral=True); return
        await ctx.send(f"❌ Code `{code}` not found.", ephemeral=True)

    # --- Timezone ---
    @commands.hybrid_command(name="timezone", aliases=["tz", "settz"])
    @app_commands.autocomplete(tz_input=timezone_autocomplete)
    @app_commands.describe(tz_input="Your timezone (EST, America/New_York, UTC+5)")
    async def set_timezone(self, ctx: commands.Context, *, tz_input: str = None):
        """Set or view your timezone."""
        uid = str(ctx.author.id)
        if not tz_input:
            current = user_timezones.get(uid)
            if current:
                now_local = utc_now().astimezone(ZoneInfo(current))
                await ctx.send(f"🕐 Timezone: **{current}**\nLocal: **{now_local.strftime('%b %d, %I:%M %p %Z')}**", ephemeral=True)
            else:
                await ctx.send("🕐 No timezone set. Use `/timezone EST` (or your tz)", ephemeral=True)
            return
        resolved = resolve_timezone(tz_input)
        if not resolved:
            await ctx.send(f"❌ Could not find timezone `{tz_input}`. Try: EST, PST, America/New_York, UTC+5", ephemeral=True); return
        user_timezones[uid] = resolved; save_data("user_timezones", user_timezones)
        now_local = utc_now().astimezone(ZoneInfo(resolved))
        await ctx.send(f"✅ Timezone set to **{resolved}** — Local: **{now_local.strftime('%b %d, %I:%M %p %Z')}**", ephemeral=True)

    @commands.hybrid_command(name="localtime", aliases=["lt", "convert"])
    @app_commands.describe(utc_time_str="UTC time to convert (e.g. 2026-03-15 20:00)")
    async def local_time(self, ctx: commands.Context, *, utc_time_str: str = None):
        """Convert UTC time to your local timezone."""
        uid = str(ctx.author.id)
        user_tz = user_timezones.get(uid)
        if not user_tz:
            await ctx.send("❌ Set your timezone first: `/timezone EST`", ephemeral=True); return
        if not utc_time_str:
            now_local = utc_now().astimezone(ZoneInfo(user_tz))
            await ctx.send(f"🕐 UTC: **{utc_now().strftime('%b %d, %I:%M %p')}**\nLocal: **{now_local.strftime('%b %d, %I:%M %p %Z')}**", ephemeral=True)
        else:
            try:
                target = utc_from_iso(utc_time_str)
                local = target.astimezone(ZoneInfo(user_tz))
                await ctx.send(f"🕐 **{utc_time_str}** UTC = **{local.strftime('%b %d, %I:%M %p %Z')}**", ephemeral=True)
            except ValueError:
                await ctx.send("❌ Invalid format. Use: `YYYY-MM-DD HH:MM`", ephemeral=True)

    # --- /remindme ---
    @commands.hybrid_command(name="remindme")
    async def remind_me(self, ctx: commands.Context):
        """Toggle event reminders via DM."""
        uid = str(ctx.author.id)
        users = reminder_optins.get("users", [])
        if uid in users:
            users.remove(uid)
            save_data("reminder_optins", reminder_optins)
            await ctx.send("🔕 Event DM reminders **disabled**.", ephemeral=True)
        else:
            users.append(uid)
            save_data("reminder_optins", reminder_optins)
            await ctx.send("🔔 Event DM reminders **enabled**! You'll get DMs 1 hour before events.", ephemeral=True)

    # --- /powerhistory (enhanced with growth trends) ---
    @commands.hybrid_command(name="powerhistory")
    @commands.cooldown(1, 30, commands.BucketType.user)
    @app_commands.describe(member="Member to view (leave empty for yourself)")
    async def power_history_cmd(self, ctx: commands.Context, member: discord.Member = None):
        """View power growth history with trends and projections."""
        member = member or ctx.author
        uid = str(member.id)
        history = power_history.get(uid, [])
        if not history:
            await ctx.send(f"📊 No power history for {member.display_name}. Use `/setpower` to start tracking!", ephemeral=True); return

        embed = discord.Embed(title=f"📈 {member.display_name}'s Power History", color=discord.Color.green(), timestamp=utc_now())
        # Last 10 entries
        recent = history[-10:]
        lines = []
        for entry in reversed(recent):
            ts = utc_from_iso(entry["timestamp"])
            delta = entry["new_power"] - entry["old_power"]
            arrow = "📈" if delta > 0 else "📉" if delta < 0 else "➡️"
            lines.append(f"{arrow} **{entry['new_power']:,}** ({'+' if delta >= 0 else ''}{delta:,}) — {ts.strftime('%b %d')}")
        embed.add_field(name="Recent Changes", value="\n".join(lines), inline=False)

        # Growth analytics
        if len(history) >= 2:
            first = history[0]; last = history[-1]
            total_growth = last["new_power"] - first["new_power"]
            first_ts = utc_from_iso(first["timestamp"])
            last_ts = utc_from_iso(last["timestamp"])
            days = max((last_ts - first_ts).days, 1)
            daily_rate = total_growth / days

            embed.add_field(name="📊 Growth Analytics", value=(
                f"**Total Growth:** {total_growth:,} over {days} days\n"
                f"**Daily Rate:** {daily_rate:,.0f}/day\n"
                f"**Weekly Rate:** {daily_rate * 7:,.0f}/week\n"
                f"**Monthly Rate:** {daily_rate * 30:,.0f}/month"
            ), inline=False)

            # Projections
            current = last["new_power"]
            if daily_rate > 0:
                proj_30 = current + int(daily_rate * 30)
                proj_90 = current + int(daily_rate * 90)
                embed.add_field(name="🔮 Projections", value=(
                    f"**30 days:** {proj_30:,}\n"
                    f"**90 days:** {proj_90:,}"
                ), inline=True)

        embed.set_footer(text=f"{len(history)} data points tracked")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(General(bot))
