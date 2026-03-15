"""Tasks cog — background scheduled tasks, cleanup, bot health monitoring."""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import logging
import time
from datetime import datetime, timedelta, timezone

from utils import (
    load_data, save_data, load_config, utc_now, utc_from_iso, format_delta,
    get_active_events, get_upcoming_events, cron_matches, get_bot_health,
    cooldown, DATA_PATH, BACKUP_PATH, create_backup, get_channel
)

log = logging.getLogger("kingshot-bot")

# Load last fire times
last_announcement_fires: dict[str, datetime] = {}

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
    "💡 View community troop stats with /troopstats — learn what works for others.",
    "💡 Check /viewsuggestions to see community-tested strategies for your event.",
]


class Tasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def cog_unload(self):
        self.scheduled_announcements.cancel()
        self.daily_tip_task.cancel()
        self.timer_check.cancel()
        self.event_cycle_reminder.cancel()
        self.data_cleanup.cancel()
        self.daily_backup.cancel()

    def start_tasks(self):
        """Start all background tasks (call from on_ready)."""
        for task in (self.scheduled_announcements, self.daily_tip_task,
                     self.timer_check, self.event_cycle_reminder,
                     self.data_cleanup, self.daily_backup):
            if not task.is_running():
                task.start()

    # --- Scheduled Announcements ---
    @tasks.loop(minutes=1)
    async def scheduled_announcements(self):
        now = utc_now()
        cfg = load_config()
        guild_id = cfg.get("guild_id")
        if not guild_id:
            return
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return
        for ann in cfg.get("scheduled_announcements", []):
            if not ann.get("enabled"):
                continue
            if cron_matches(ann["cron"], now):
                last = last_announcement_fires.get(ann["name"])
                if last and (now - last).total_seconds() < 120:
                    continue
                channel = get_channel(guild, ann["channel"])
                if channel:
                    # Replace placeholders
                    msg = ann["message"]
                    active = get_active_events()
                    if active:
                        msg = msg.replace("{active_events}", ", ".join(e["name"] for e in active))
                    else:
                        msg = msg.replace("{active_events}", "No active events")
                    upcoming = get_upcoming_events(days_ahead=3)
                    if upcoming:
                        up_text = ", ".join(f"{e['name']} (in {d}d)" for d, _, e in upcoming[:3])
                        msg = msg.replace("{upcoming_events}", up_text)
                    else:
                        msg = msg.replace("{upcoming_events}", "None in the next 3 days")
                    embed = discord.Embed(
                        title=f"📢 {ann['name']}", description=msg,
                        color=discord.Color.gold(), timestamp=now,
                    )
                    try:
                        await channel.send(embed=embed)
                        last_announcement_fires[ann["name"]] = now
                    except Exception as e:
                        log.error(f"Failed to send announcement {ann['name']}: {e}")

    @scheduled_announcements.before_loop
    async def before_announcements(self):
        await self.bot.wait_until_ready()

    # --- Daily Tip ---
    @tasks.loop(hours=24)
    async def daily_tip_task(self):
        cfg = load_config()
        guild_id = cfg.get("guild_id")
        tip_channel = cfg.get("tip_channel", "bot-tips")
        if not guild_id:
            return
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return
        channel = get_channel(guild, "bot-tips")
        if channel:
            tip = random.choice(DEFAULT_TIPS)
            embed = discord.Embed(description=tip, color=discord.Color.green())
            embed.set_footer(text="Daily Tip | Use /events for full guides")
            try:
                await channel.send(embed=embed)
            except Exception as e:
                log.error(f"Failed to send daily tip: {e}")

    @daily_tip_task.before_loop
    async def before_daily_tip(self):
        await self.bot.wait_until_ready()

    # --- Timer Check ---
    @tasks.loop(minutes=5)
    async def timer_check(self):
        war_timers = load_data("war_timers", {"timers": []})
        now = utc_now()
        expired = []
        for t in war_timers.get("timers", []):
            target = utc_from_iso(t["time"])
            if target <= now:
                expired.append(t)
            elif target - now <= timedelta(minutes=5):
                # 5 minute warning
                cfg = load_config()
                guild_id = cfg.get("guild_id")
                if guild_id:
                    guild = self.bot.get_guild(int(guild_id))
                    if guild:
                        ch = get_channel(guild, "announcements")
                        if ch:
                            try:
                                await ch.send(f"⏰ **{t['name']}** starts in less than 5 minutes!")
                            except Exception as e:
                                log.error(f"Failed to send timer warning for {t['name']}: {e}")
        if expired:
            war_timers["timers"] = [t for t in war_timers["timers"] if t not in expired]
            save_data("war_timers", war_timers)

    @timer_check.before_loop
    async def before_timer_check(self):
        await self.bot.wait_until_ready()

    # --- Event Cycle Reminder ---
    @tasks.loop(hours=6)
    async def event_cycle_reminder(self):
        cfg = load_config()
        guild_id = cfg.get("guild_id")
        if not guild_id:
            return
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return
        active = get_active_events()
        upcoming = get_upcoming_events(days_ahead=1)
        if upcoming:
            ch = get_channel(guild, "announcements")
            if ch:
                for days_until, start_date, ev in upcoming:
                    if days_until == 1:
                        embed = discord.Embed(
                            title=f"{ev['emoji']} {ev['name']} — Starting Tomorrow!",
                            description=f"Duration: {ev.get('duration_days', 1)} day(s)\nType: {ev.get('type', 'event').title()}",
                            color=discord.Color.orange(),
                        )
                        try:
                            await ch.send(embed=embed)
                        except Exception as e:
                            log.error(f"Failed to send event reminder for {ev['name']}: {e}")

    @event_cycle_reminder.before_loop
    async def before_event_cycle(self):
        await self.bot.wait_until_ready()

    # --- Data Cleanup --- NEW
    @tasks.loop(hours=24)
    async def data_cleanup(self):
        """Clean up expired data: old timers, old kill logs, old scout reports."""
        now = utc_now()
        cutoff = now - timedelta(days=90)

        # Clean expired timers
        war_timers = load_data("war_timers", {"timers": []})
        original_count = len(war_timers.get("timers", []))
        war_timers["timers"] = [
            t for t in war_timers.get("timers", [])
            if utc_from_iso(t["time"]) > now - timedelta(days=1)
        ]
        if len(war_timers["timers"]) < original_count:
            save_data("war_timers", war_timers)
            log.info(f"Cleaned {original_count - len(war_timers['timers'])} expired timers")

        # Clean old kill logs (keep last 90 days)
        kill_log = load_data("kill_log", {"kills": []})
        original_kills = len(kill_log.get("kills", []))
        kill_log["kills"] = [
            k for k in kill_log.get("kills", [])
            if utc_from_iso(k.get("timestamp", now.isoformat())) > cutoff
        ]
        if len(kill_log["kills"]) < original_kills:
            save_data("kill_log", kill_log)
            log.info(f"Cleaned {original_kills - len(kill_log['kills'])} old kill logs")

        # Clean old scout reports
        scouts = load_data("scout_reports", {"reports": []})
        original_scouts = len(scouts.get("reports", []))
        scouts["reports"] = [
            r for r in scouts.get("reports", [])
            if utc_from_iso(r.get("timestamp", now.isoformat())) > cutoff
        ]
        if len(scouts["reports"]) < original_scouts:
            save_data("scout_reports", scouts)
            log.info(f"Cleaned {original_scouts - len(scouts['reports'])} old scout reports")

        # Clean old power history (keep last 200 per user)
        power_history = load_data("power_history", {})
        trimmed_ph = False
        for uid, entries in power_history.items():
            if isinstance(entries, list) and len(entries) > 200:
                power_history[uid] = entries[-200:]
                trimmed_ph = True
        if trimmed_ph:
            save_data("power_history", power_history)
            log.info("Trimmed power history entries (>200 per user)")

        # Clean old territory logs (keep last 500)
        territory = load_data("territory_log", {"entries": []})
        if len(territory.get("entries", [])) > 500:
            territory["entries"] = territory["entries"][-500:]
            save_data("territory_log", territory)
            log.info("Trimmed territory log to 500 entries")

        # Clean old rally sessions (keep last 100)
        rallies = load_data("rally_sessions", {"rallies": []})
        if len(rallies.get("rallies", [])) > 100:
            rallies["rallies"] = rallies["rallies"][-100:]
            save_data("rally_sessions", rallies)
            log.info("Trimmed rally sessions to 100 entries")

        # Clean old war signups (keep last 50)
        signups = load_data("war_signups", {"signups": []})
        if len(signups.get("signups", [])) > 50:
            signups["signups"] = signups["signups"][-50:]
            save_data("war_signups", signups)
            log.info("Trimmed war signups to 50 entries")

        # Clean old code history (keep last 200)
        code_hist = load_data("code_history", {"codes": []})
        if len(code_hist.get("codes", [])) > 200:
            code_hist["codes"] = code_hist["codes"][-200:]
            save_data("code_history", code_hist)
            log.info("Trimmed code history to 200 entries")

    @data_cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # --- Daily Backup --- NEW
    @tasks.loop(hours=24)
    async def daily_backup(self):
        """Create automated daily backup of all data files."""
        try:
            stats = create_backup("auto")
            log.info(f"Daily backup complete: {stats['files']} files, {stats['size_kb']}KB")
        except Exception as e:
            log.error(f"Daily backup failed: {e}")

    @daily_backup.before_loop
    async def before_backup(self):
        await self.bot.wait_until_ready()

    # --- /backup --- NEW
    @commands.hybrid_command(name="backup")
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    @cooldown(300)
    async def manual_backup(self, ctx: commands.Context):
        """Create a manual backup of all bot data."""
        await ctx.defer(ephemeral=True)
        try:
            stats = create_backup("manual")
            embed = discord.Embed(title="💾 Backup Created", color=discord.Color.green())
            embed.add_field(name="Files", value=str(stats["files"]), inline=True)
            embed.add_field(name="Size", value=f"{stats['size_kb']}KB", inline=True)
            # Count existing backups
            import os
            backup_count = sum(1 for d in BACKUP_PATH.iterdir() if d.is_dir())
            embed.add_field(name="Total Backups", value=str(backup_count), inline=True)
            embed.set_footer(text="Backups are automatically cleaned after 7 days")
            await ctx.send(embed=embed, ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ Backup failed: {e}", ephemeral=True)

    # --- /bot_health --- NEW
    @commands.hybrid_command(name="bot_health")
    @app_commands.default_permissions(manage_guild=True)
    @cooldown(30)
    async def health_check(self, ctx: commands.Context):
        """Show bot health and diagnostics."""
        health = get_bot_health()
        uptime = time.time() - health.get("start_time", time.time())
        days = int(uptime // 86400)
        hours = int((uptime % 86400) // 3600)
        mins = int((uptime % 3600) // 60)

        embed = discord.Embed(title="🤖 Bot Health", color=discord.Color.green())
        embed.add_field(name="⏱️ Uptime", value=f"{days}d {hours}h {mins}m", inline=True)
        embed.add_field(name="🔗 Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="👥 Guilds", value=str(len(self.bot.guilds)), inline=True)

        last_ok = health.get("last_save_ok")
        last_fail = health.get("last_save_fail")
        errors = health.get("error_count", 0)
        embed.add_field(name="💾 Last Save OK", value=f"<t:{int(last_ok)}:R>" if last_ok else "Never", inline=True)
        embed.add_field(name="❌ Save Errors", value=str(errors), inline=True)
        if last_fail:
            embed.add_field(name="⚠️ Last Fail", value=f"<t:{int(last_fail)}:R>", inline=True)

        # Task status
        task_status = []
        for name, task in [
            ("Announcements", self.scheduled_announcements),
            ("Daily Tip", self.daily_tip_task),
            ("Timer Check", self.timer_check),
            ("Event Reminders", self.event_cycle_reminder),
            ("Data Cleanup", self.data_cleanup),
            ("Daily Backup", self.daily_backup),
        ]:
            status = "✅ Running" if task.is_running() else "❌ Stopped"
            task_status.append(f"{status} {name}")
        embed.add_field(name="📋 Tasks", value="\n".join(task_status), inline=False)

        await ctx.send(embed=embed, ephemeral=True)

    # --- /data_stats --- NEW
    @commands.hybrid_command(name="data_stats")
    @app_commands.default_permissions(manage_guild=True)
    @cooldown(30)
    async def data_stats(self, ctx: commands.Context):
        """Show data file sizes and record counts."""
        import os
        embed = discord.Embed(title="📊 Data Stats", color=discord.Color.blue())
        data_files = [
            ("profiles", "Profiles"), ("member_stats", "Member Stats"),
            ("gift_codes", "Gift Codes"), ("war_timers", "War Timers"),
            ("war_signups", "War Signups"), ("kill_log", "Kill Log"),
            ("scout_reports", "Scout Reports"), ("territory_log", "Territory"),
            ("user_suggestions", "Suggestions"), ("troop_reports", "Troop Reports"),
            ("power_history", "Power History"), ("user_timezones", "Timezones"),
            ("reminder_optins", "Reminder Opt-ins"), ("rally_sessions", "Rally Sessions"),
        ]
        for fname, label in data_files:
            fpath = DATA_PATH / f"{fname}.json"
            if fpath.exists():
                size = os.path.getsize(fpath)
                data = load_data(fname, {})
                # Estimate record count
                count = 0
                if isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, list):
                            count = max(count, len(v))
                        else:
                            count = len(data)
                            break
                size_str = f"{size/1024:.1f}KB" if size > 1024 else f"{size}B"
                embed.add_field(name=label, value=f"{size_str} | {count} records", inline=True)
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tasks(bot))
