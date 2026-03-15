"""Stats cog — member stats, optimization engine, achievements, readiness prediction."""
import discord
from discord.ext import commands
from discord import app_commands
from typing import List, Optional
from datetime import datetime, timedelta, timezone

from utils import (
    load_data, save_data, utc_now, utc_from_iso, parse_power,
    cooldown, PaginatorView, load_hero_db, load_formations, event_name_autocomplete
)

# In-memory stores
member_stats = load_data("member_stats", {})
user_profiles = load_data("profiles", {})

TROOP_TIERS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11"]


class StatInputModal(discord.ui.Modal, title="Enter Your Stats"):
    tc_level = discord.ui.TextInput(label="Town Center Level", placeholder="e.g. 25", max_length=3, required=True)
    total_power = discord.ui.TextInput(label="Total Power (use k/m/b)", placeholder="e.g. 85m", max_length=15, required=True)
    highest_troop_tier = discord.ui.TextInput(label="Highest Troop Tier Unlocked", placeholder="e.g. T9", max_length=4, required=True)
    generation = discord.ui.TextInput(label="Server Age (1=newest … 5=oldest)", placeholder="Profile → Server Info → Generation (e.g. 4)", max_length=1, required=True)
    top_heroes = discord.ui.TextInput(label="Top 3 Heroes (name, star level)", placeholder="e.g. Amadeus 5*, Hilde 4*", style=discord.TextStyle.short, max_length=100, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        pwr = parse_power(self.total_power.value)
        if pwr is None:
            await interaction.response.send_message("❌ Invalid power value.", ephemeral=True); return
        try:
            tc = int(self.tc_level.value.strip())
            if tc < 1 or tc > 35: raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ TC level must be 1-35.", ephemeral=True); return
        try:
            gen = int(self.generation.value.strip())
            if gen < 1 or gen > 5: raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Generation must be 1-5.", ephemeral=True); return
        tier_raw = self.highest_troop_tier.value.upper().strip().replace(" ", "")
        if not tier_raw.startswith("T"): tier_raw = "T" + tier_raw
        if tier_raw not in TROOP_TIERS:
            await interaction.response.send_message(f"❌ Invalid tier. Use: {', '.join(TROOP_TIERS)}", ephemeral=True); return

        if uid not in member_stats: member_stats[uid] = {}
        member_stats[uid].update({
            "user_name": str(interaction.user), "tc_level": tc, "power": pwr,
            "highest_tier": tier_raw, "generation": gen,
            "top_heroes": self.top_heroes.value.strip() if self.top_heroes.value else "",
            "updated_at": utc_now().isoformat(),
        })
        save_data("member_stats", member_stats)
        if uid not in user_profiles: user_profiles[uid] = {}
        user_profiles[uid]["power"] = pwr
        save_data("profiles", user_profiles)

        embed = discord.Embed(title="✅ Stats Updated!", color=discord.Color.green())
        embed.add_field(name="🏰 TC", value=f"Level {tc}", inline=True)
        embed.add_field(name="⚡ Power", value=f"{pwr:,}", inline=True)
        embed.add_field(name="🗡️ Tier", value=tier_raw, inline=True)
        embed.add_field(name="🌍 Gen", value=f"Gen {gen}", inline=True)
        embed.add_field(name="🦸 Heroes", value=self.top_heroes.value or "Not set", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TroopInputModal(discord.ui.Modal, title="Enter Your Troop Counts"):
    infantry = discord.ui.TextInput(label="Infantry Count (use k/m)", placeholder="e.g. 500k", max_length=15, required=True)
    cavalry = discord.ui.TextInput(label="Cavalry Count (use k/m)", placeholder="e.g. 200k", max_length=15, required=True)
    archers = discord.ui.TextInput(label="Archer Count (use k/m)", placeholder="e.g. 300k", max_length=15, required=True)
    march_capacity = discord.ui.TextInput(label="Max March Capacity", placeholder="e.g. 250k", max_length=15, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        inf = parse_power(self.infantry.value)
        cav = parse_power(self.cavalry.value)
        arch = parse_power(self.archers.value)
        if None in (inf, cav, arch):
            await interaction.response.send_message("❌ Invalid count. Use numbers like `500k`.", ephemeral=True); return
        march_cap = parse_power(self.march_capacity.value) if self.march_capacity.value and self.march_capacity.value.strip() else None
        if uid not in member_stats:
            member_stats[uid] = {"user_name": str(interaction.user)}
        total = inf + cav + arch
        member_stats[uid].update({
            "infantry": inf, "cavalry": cav, "archers": arch, "total_troops": total,
            "troop_pct_inf": round(inf / total * 100) if total > 0 else 0,
            "troop_pct_cav": round(cav / total * 100) if total > 0 else 0,
            "troop_pct_arch": round(arch / total * 100) if total > 0 else 0,
            "troops_updated_at": utc_now().isoformat(),
        })
        if march_cap: member_stats[uid]["march_capacity"] = march_cap
        save_data("member_stats", member_stats)
        embed = discord.Embed(title="🪖 Troop Counts Updated!", color=discord.Color.green())
        embed.add_field(name="🛡️ Infantry", value=f"{inf:,} ({member_stats[uid]['troop_pct_inf']}%)", inline=True)
        embed.add_field(name="🐴 Cavalry", value=f"{cav:,} ({member_stats[uid]['troop_pct_cav']}%)", inline=True)
        embed.add_field(name="🏹 Archers", value=f"{arch:,} ({member_stats[uid]['troop_pct_arch']}%)", inline=True)
        embed.add_field(name="📊 Total", value=f"{total:,}", inline=True)
        if march_cap: embed.add_field(name="🚶 March Cap", value=f"{march_cap:,}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- /mystats ---
    @commands.hybrid_command(name="mystats")
    @cooldown(10)
    async def mystats(self, ctx: commands.Context):
        """Enter or view your game stats."""
        uid = str(ctx.author.id)
        data = member_stats.get(uid)
        if data and data.get("tc_level"):
            embed = discord.Embed(
                title=f"📊 {ctx.author.display_name}'s Stats", color=discord.Color.blue(), timestamp=utc_now(),
            )
            embed.set_thumbnail(url=ctx.author.display_avatar.url)
            embed.add_field(name="🏰 TC", value=f"Level {data.get('tc_level', '?')}", inline=True)
            embed.add_field(name="⚡ Power", value=f"{data.get('power', 0):,}", inline=True)
            embed.add_field(name="🗡️ Tier", value=data.get("highest_tier", "?"), inline=True)
            embed.add_field(name="🌍 Gen", value=f"Gen {data.get('generation', '?')}", inline=True)
            embed.add_field(name="🦸 Heroes", value=data.get("top_heroes", "Not set") or "Not set", inline=False)
            if data.get("infantry") is not None:
                troop_text = (
                    f"🛡️ Inf: {data['infantry']:,} ({data.get('troop_pct_inf', 0)}%)\n"
                    f"🐴 Cav: {data['cavalry']:,} ({data.get('troop_pct_cav', 0)}%)\n"
                    f"🏹 Arch: {data['archers']:,} ({data.get('troop_pct_arch', 0)}%)\n"
                    f"📊 Total: {data.get('total_troops', 0):,}"
                )
                if data.get("march_capacity"): troop_text += f"\n🚶 March Cap: {data['march_capacity']:,}"
                embed.add_field(name="🪖 Troops", value=troop_text, inline=False)
            view = discord.ui.View()
            update_btn = discord.ui.Button(label="Update Stats", style=discord.ButtonStyle.primary, emoji="📝")
            troops_btn = discord.ui.Button(label="Update Troops", style=discord.ButtonStyle.secondary, emoji="🪖")
            async def _update_stats(interaction: discord.Interaction):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message("You can only update your own stats.", ephemeral=True); return
                await interaction.response.send_modal(StatInputModal())
            async def _update_troops(interaction: discord.Interaction):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message("You can only update your own troops.", ephemeral=True); return
                await interaction.response.send_modal(TroopInputModal())
            update_btn.callback = _update_stats
            troops_btn.callback = _update_troops
            view.add_item(update_btn); view.add_item(troops_btn)
            await ctx.send(embed=embed, view=view, ephemeral=True)
        else:
            if ctx.interaction:
                await ctx.interaction.response.send_modal(StatInputModal())
            else:
                await ctx.send("Use `/mystats` as a slash command to open the stats form!")

    # --- /updatetroops ---
    @commands.hybrid_command(name="updatetroops")
    async def updatetroops(self, ctx: commands.Context):
        """Open the troop count input form."""
        if ctx.interaction:
            await ctx.interaction.response.send_modal(TroopInputModal())
        else:
            await ctx.send("Use `/updatetroops` as a slash command to open the form!")

    # --- /alliancestats ---
    @commands.hybrid_command(name="alliancestats")
    @app_commands.default_permissions(manage_guild=True)
    @cooldown(30)
    async def alliancestats(self, ctx: commands.Context):
        """View aggregated alliance stats for event planning."""
        if not member_stats:
            await ctx.send("No member stats yet. Ask members to use `/mystats`!", ephemeral=True); return

        total_members = len(member_stats)
        powers = [d.get("power", 0) for d in member_stats.values() if d.get("power", 0) > 0]
        tc_levels = [d.get("tc_level", 0) for d in member_stats.values() if d.get("tc_level", 0) > 0]
        total_inf = sum(d.get("infantry", 0) for d in member_stats.values())
        total_cav = sum(d.get("cavalry", 0) for d in member_stats.values())
        total_arch = sum(d.get("archers", 0) for d in member_stats.values())
        total_troops = total_inf + total_cav + total_arch

        embed1 = discord.Embed(title="📊 Alliance Stats — Overview", color=discord.Color.gold())
        embed1.add_field(name="👥 Members", value=str(total_members), inline=True)
        if powers:
            embed1.add_field(name="⚡ Avg Power", value=f"{sum(powers)//len(powers):,}", inline=True)
            embed1.add_field(name="⚡ Total", value=f"{sum(powers):,}", inline=True)
        if tc_levels:
            tc25 = sum(1 for tc in tc_levels if tc >= 25)
            tc20 = sum(1 for tc in tc_levels if 20 <= tc < 25)
            embed1.add_field(name="🏰 TC Distribution", value=f"TC25+: **{tc25}** | TC20-24: **{tc20}** | <TC20: **{len(tc_levels)-tc25-tc20}**", inline=False)

        embed2 = discord.Embed(title="📊 Alliance Stats — Troops", color=discord.Color.gold())
        if total_troops > 0:
            pi, pc, pa = round(total_inf/total_troops*100), round(total_cav/total_troops*100), round(total_arch/total_troops*100)
            embed2.add_field(name="📊 Total", value=f"{total_troops:,}", inline=True)
            embed2.add_field(name="🛡️ Inf", value=f"{total_inf:,} ({pi}%)", inline=True)
            embed2.add_field(name="🐴 Cav", value=f"{total_cav:,} ({pc}%)", inline=True)
            embed2.add_field(name="🏹 Arch", value=f"{total_arch:,} ({pa}%)", inline=True)

        embed3 = discord.Embed(title="📊 Alliance Stats — Top 15", color=discord.Color.gold())
        sorted_by_power = sorted(
            [(uid, d) for uid, d in member_stats.items() if d.get("power", 0) > 0],
            key=lambda x: x[1]["power"], reverse=True
        )
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, d) in enumerate(sorted_by_power[:15]):
            m = medals[i] if i < 3 else f"#{i+1}"
            name = d.get("user_name", "Unknown").split("#")[0]
            lines.append(f"{m} **{name}** — {d['power']:,} | TC{d.get('tc_level','?')} | {d.get('highest_tier','?')}")
        embed3.add_field(name="🏆 By Power", value="\n".join(lines) or "No data", inline=False)

        view = PaginatorView([embed1, embed2, embed3])
        await ctx.send(embed=embed1, view=view, ephemeral=True)

    # --- /eventready ---
    @commands.hybrid_command(name="eventready")
    @app_commands.describe(event="Event to check readiness for")
    @app_commands.default_permissions(manage_guild=True)
    @cooldown(30)
    async def eventready(self, ctx: commands.Context, event: str):
        """Check alliance readiness for a specific event."""
        if not member_stats:
            await ctx.send("No member stats yet.", ephemeral=True); return
        event_reqs = {
            "bear": {"name": "Bear Hunt", "emoji": "🐻", "ideal_arch": 89, "min_tc": 15},
            "kvk": {"name": "KvK", "emoji": "👑", "ideal_inf": 50, "min_tc": 20},
            "swordland": {"name": "Swordland", "emoji": "⚔️", "ideal_inf": 50, "min_tc": 15},
            "brawl": {"name": "Brawl", "emoji": "💥", "ideal_inf": 50, "min_tc": 20},
        }
        req = event_reqs.get(event.lower())
        if not req:
            await ctx.send(f"Choose from: {', '.join(event_reqs.keys())}", ephemeral=True); return

        eligible, not_ready = [], []
        for uid, d in member_stats.items():
            tc = d.get("tc_level", 0)
            if tc >= req.get("min_tc", 1):
                eligible.append((uid, d))
            elif tc > 0:
                not_ready.append((uid, d))

        embed = discord.Embed(
            title=f"{req['emoji']} {req['name']} — Alliance Readiness", color=discord.Color.blue(),
        )
        embed.add_field(name="✅ Eligible", value=str(len(eligible)), inline=True)
        embed.add_field(name="⚠️ Not Ready", value=str(len(not_ready)), inline=True)
        embed.add_field(name="Min TC Required", value=str(req.get("min_tc", 1)), inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    # --- /optimize --- Enhanced scoring
    @commands.hybrid_command(name="optimize")
    @app_commands.describe(event="Event to optimize for (bear, kvk, swordland, mystic)")
    @cooldown(15)
    async def optimize(self, ctx: commands.Context, event: str):
        """Get optimized lineup recommendations for an event."""
        heroes = load_hero_db()
        formations = load_formations()
        event_lower = event.lower()

        embed = discord.Embed(
            title=f"🎯 Optimized Lineup: {event.title()}", color=discord.Color.green(),
        )
        # Score heroes for event
        scored = []
        for name, h in heroes.items():
            score = 0
            if event_lower in h.get("captain_for", []):
                score += 100
            if event_lower in h.get("joiner_for", []):
                score += 50
            score += h.get("lethality", 0)
            score += h.get("atk_buff", 0)
            if h.get("tier", 0) >= 3:
                score += 20
            scored.append((name, h, score))
        scored.sort(key=lambda x: x[2], reverse=True)

        if scored:
            top = scored[:5]
            hero_text = "\n".join(
                f"{'⭐' * min(h.get('tier', 1), 5)} **{n.title()}** — {h['role']} ({h['type']}) | Score: {s}"
                for n, h, s in top
            )
            embed.add_field(name="🦸 Top Heroes", value=hero_text, inline=False)

        # Formation recommendation
        form = formations.get(event_lower)
        if form:
            embed.add_field(
                name="🪖 Recommended Formation",
                value=f"Infantry: {form.get('infantry', '?')}% | Cavalry: {form.get('cavalry', '?')}% | Archers: {form.get('archers', '?')}%",
                inline=False,
            )

        embed.set_footer(text="Scores based on hero abilities, event synergy, and tier")
        await ctx.send(embed=embed, ephemeral=True)

    # --- /myfit ---
    @commands.hybrid_command(name="myfit")
    @app_commands.describe(event="Event to check your fit for")
    @cooldown(15)
    async def myfit(self, ctx: commands.Context, event: str):
        """Check how well your stats match an event's requirements."""
        uid = str(ctx.author.id)
        data = member_stats.get(uid)
        if not data or not data.get("tc_level"):
            await ctx.send("Set your stats first with `/mystats`!", ephemeral=True); return

        event_reqs = {
            "bear": {"name": "Bear Hunt", "ideal_arch": 80, "min_tc": 15, "min_power": 20_000_000},
            "kvk": {"name": "KvK", "ideal_inf": 50, "min_tc": 20, "min_power": 50_000_000},
            "swordland": {"name": "Swordland", "ideal_inf": 50, "min_tc": 15, "min_power": 30_000_000},
        }
        req = event_reqs.get(event.lower())
        if not req:
            await ctx.send(f"Choose from: {', '.join(event_reqs.keys())}", ephemeral=True); return

        score = 0
        checks = []
        tc = data.get("tc_level", 0)
        power = data.get("power", 0)
        if tc >= req.get("min_tc", 1):
            score += 30; checks.append(f"✅ TC Level {tc} (req: {req.get('min_tc')})")
        else:
            checks.append(f"❌ TC Level {tc} (need: {req.get('min_tc')})")
        if power >= req.get("min_power", 0):
            score += 30; checks.append(f"✅ Power {power:,}")
        else:
            checks.append(f"⚠️ Power {power:,} (aim: {req.get('min_power', 0):,})")
        tier_num = int(data.get("highest_tier", "T1").replace("T", "") or 1)
        if tier_num >= 9:
            score += 20; checks.append(f"✅ Tier {data.get('highest_tier')}")
        elif tier_num >= 7:
            score += 10; checks.append(f"⚠️ Tier {data.get('highest_tier')} (aim T9+)")
        else:
            checks.append(f"❌ Tier {data.get('highest_tier')} (need T7+)")
        if data.get("infantry") is not None:
            score += 20; checks.append("✅ Troop data submitted")
        else:
            checks.append("⚠️ No troop data (use /updatetroops)")

        grade = "S" if score >= 90 else "A" if score >= 70 else "B" if score >= 50 else "C" if score >= 30 else "D"
        embed = discord.Embed(
            title=f"🎯 Your Fit: {req['name']} — Grade {grade} ({score}/100)",
            description="\n".join(checks), color=discord.Color.green() if score >= 70 else discord.Color.orange(),
        )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /achievements --- NEW
    @commands.hybrid_command(name="achievements")
    @cooldown(30)
    async def achievements(self, ctx: commands.Context, member: discord.Member = None):
        """View achievements for a member."""
        member = member or ctx.author
        uid = str(member.id)
        data = member_stats.get(uid, {})
        achievements = []

        power = data.get("power", 0)
        if power >= 100_000_000: achievements.append("💎 **Centurion** — 100M+ Power")
        elif power >= 50_000_000: achievements.append("🏆 **Powerhouse** — 50M+ Power")
        elif power >= 20_000_000: achievements.append("⭐ **Rising Star** — 20M+ Power")

        tc = data.get("tc_level", 0)
        if tc >= 30: achievements.append("🏰 **Fortress Master** — TC 30+")
        elif tc >= 25: achievements.append("🏰 **Commander** — TC 25+")

        tier = data.get("highest_tier", "T1")
        tier_num = int(tier.replace("T", "") or 1)
        if tier_num >= 11: achievements.append("🗡️ **Elite Warrior** — T11 Troops")
        elif tier_num >= 9: achievements.append("🗡️ **Veteran** — T9+ Troops")

        gen = data.get("generation", 0)
        if gen >= 4: achievements.append("🌍 **Old Guard** — Gen 4+")

        # Kill-based achievements
        kill_log_data = load_data("kill_log", {"kills": []})
        user_kills = [k for k in kill_log_data.get("kills", []) if k.get("reporter_id") == member.id]
        total_killed = sum(k.get("troops_killed", 0) for k in user_kills)
        if total_killed >= 1_000_000: achievements.append("💀 **Warlord** — 1M+ Troops Killed")
        elif total_killed >= 100_000: achievements.append("💀 **Slayer** — 100K+ Troops Killed")

        if not achievements:
            achievements.append("🌱 **Newcomer** — Keep growing!")

        embed = discord.Embed(
            title=f"🏅 {member.display_name}'s Achievements",
            description="\n".join(achievements), color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed, ephemeral=True)

    # --- /mvp --- NEW
    @commands.hybrid_command(name="mvp")
    @cooldown(30)
    async def mvp(self, ctx: commands.Context):
        """Show the alliance MVP based on combined metrics."""
        if not member_stats:
            await ctx.send("No member stats yet.", ephemeral=True); return

        kill_log_data = load_data("kill_log", {"kills": []})
        scout_data = load_data("scout_reports", {"reports": []})

        scores: dict[str, dict] = {}
        for uid, d in member_stats.items():
            score = 0
            score += min(d.get("power", 0) / 1_000_000, 50)  # Up to 50 pts from power
            score += min(d.get("tc_level", 0) * 2, 30)  # Up to 30 pts from TC
            tier_num = int(d.get("highest_tier", "T1").replace("T", "") or 1)
            score += tier_num * 2  # Up to 22 pts from tier
            # Kills
            user_kills = sum(k.get("troops_killed", 0) for k in kill_log_data.get("kills", []) if str(k.get("reporter_id")) == uid)
            score += min(user_kills / 10_000, 20)
            # Scouts
            user_scouts = sum(1 for r in scout_data.get("reports", []) if str(r.get("scout_id")) == uid)
            score += min(user_scouts * 2, 10)
            scores[uid] = {"name": d.get("user_name", "Unknown").split("#")[0], "score": round(score, 1)}

        ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, s) in enumerate(ranked[:10]):
            m = medals[i] if i < 3 else f"**{i+1}.**"
            lines.append(f"{m} {s['name']} — **{s['score']}** pts")

        embed = discord.Embed(
            title="🏅 Alliance MVP Rankings",
            description="\n".join(lines), color=discord.Color.gold(),
        )
        embed.set_footer(text="Score = power + TC + tier + kills + scouts")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Stats(bot))
