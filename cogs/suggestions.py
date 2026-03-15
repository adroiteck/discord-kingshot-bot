"""Suggestions cog — community crowdsourcing, troop reports, strategy sharing."""
import discord
from discord.ext import commands
from discord import app_commands

from utils import (
    load_data, save_data, utc_now, cooldown, PaginatorView
)

# In-memory stores
user_suggestions = load_data("user_suggestions", {"suggestions": []})


class Suggestions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- /suggest ---
    @commands.hybrid_command(name="suggest")
    @app_commands.describe(event="Event name (e.g., bear, kvk)", suggestion="Your strategy tip")
    @cooldown(30)
    async def suggest(self, ctx: commands.Context, event: str, *, suggestion: str):
        """Submit a strategy tip or troop composition."""
        entry = {
            "user_id": ctx.author.id, "user_name": str(ctx.author),
            "event": event.lower(), "suggestion": suggestion,
            "timestamp": utc_now().isoformat(), "votes": 0, "status": "pending",
        }
        user_suggestions["suggestions"].append(entry)
        save_data("user_suggestions", user_suggestions)
        embed = discord.Embed(
            title="✅ Suggestion Submitted!",
            description=f"**Event:** {event}\n**Tip:** {suggestion}\n\nLeadership will review your suggestion.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /viewsuggestions ---
    @commands.hybrid_command(name="viewsuggestions")
    @app_commands.describe(event="Event name to view suggestions for")
    @cooldown(15)
    async def viewsuggestions(self, ctx: commands.Context, event: str):
        """View community suggestions for an event."""
        filtered = [s for s in user_suggestions["suggestions"]
                     if s["event"] == event.lower() and s.get("status") != "rejected"]
        if not filtered:
            await ctx.send(f"No suggestions yet for **{event}**. Use `/suggest` to add one!", ephemeral=True)
            return
        pages = []
        per_page = 5
        for i in range(0, len(filtered), per_page):
            chunk = filtered[i:i+per_page]
            desc = ""
            for j, s in enumerate(chunk, start=i+1):
                icon = "✅" if s.get("status") == "approved" else "⏳"
                desc += f"{icon} **#{j}** by <@{s['user_id']}>\n{s['suggestion']}\n👍 {s.get('votes', 0)} votes\n\n"
            embed = discord.Embed(
                title=f"📋 Community Strategies — {event.title()}", description=desc, color=discord.Color.blue(),
            )
            embed.set_footer(text=f"Page {i//per_page+1}/{(len(filtered)-1)//per_page+1}")
            pages.append(embed)
        if len(pages) == 1:
            await ctx.send(embed=pages[0], ephemeral=True)
        else:
            await ctx.send(embed=pages[0], view=PaginatorView(pages), ephemeral=True)

    # --- /reportcomp ---
    @commands.hybrid_command(name="reportcomp")
    @app_commands.describe(
        event="Event name", infantry="Infantry %", cavalry="Cavalry %",
        archers="Archer %", result="How did it go?"
    )
    @cooldown(30)
    async def reportcomp(self, ctx: commands.Context, event: str, infantry: int, cavalry: int, archers: int, *, result: str):
        """Report your troop composition and results for an event."""
        if infantry + cavalry + archers != 100:
            await ctx.send("❌ Percentages must add up to 100!", ephemeral=True); return
        entry = {
            "user_id": ctx.author.id, "user_name": str(ctx.author),
            "event": event.lower(), "infantry": infantry, "cavalry": cavalry,
            "archers": archers, "result": result, "timestamp": utc_now().isoformat(),
        }
        comps = load_data("troop_reports", {"reports": []})
        comps["reports"].append(entry)
        save_data("troop_reports", comps)
        embed = discord.Embed(
            title="🪖 Troop Report Submitted!",
            description=(
                f"**Event:** {event}\n"
                f"**Composition:** {infantry}% Inf / {cavalry}% Cav / {archers}% Arch\n"
                f"**Result:** {result}"
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed, ephemeral=True)

    # --- /troopstats ---
    @commands.hybrid_command(name="troopstats")
    @app_commands.describe(event="Event name to view troop stats for")
    @cooldown(15)
    async def troopstats(self, ctx: commands.Context, event: str):
        """View aggregated troop composition data."""
        comps = load_data("troop_reports", {"reports": []})
        filtered = [r for r in comps["reports"] if r["event"] == event.lower()]
        if not filtered:
            await ctx.send(f"No troop reports for **{event}** yet. Use `/reportcomp`!", ephemeral=True)
            return
        avg_inf = sum(r["infantry"] for r in filtered) / len(filtered)
        avg_cav = sum(r["cavalry"] for r in filtered) / len(filtered)
        avg_arch = sum(r["archers"] for r in filtered) / len(filtered)
        recent = filtered[-5:]
        recent_text = "\n".join(
            f"<@{r['user_id']}>: {r['infantry']}%/{r['cavalry']}%/{r['archers']}% — {r['result']}"
            for r in reversed(recent)
        )
        embed = discord.Embed(
            title=f"📊 Troop Stats — {event.title()}",
            description=f"Based on **{len(filtered)}** reports.", color=discord.Color.blue(),
        )
        embed.add_field(
            name="📈 Average Composition",
            value=f"Infantry: {avg_inf:.0f}%\nCavalry: {avg_cav:.0f}%\nArchers: {avg_arch:.0f}%",
            inline=True,
        )
        embed.add_field(name="🕐 Recent Reports", value=recent_text or "None", inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    # --- /votesuggestion --- NEW (Phase 3: Suggestion voting)
    @commands.hybrid_command(name="votesuggestion")
    @app_commands.describe(index="Suggestion number to vote for")
    @cooldown(10)
    async def votesuggestion(self, ctx: commands.Context, index: int):
        """Vote for a community suggestion."""
        if index < 1 or index > len(user_suggestions["suggestions"]):
            await ctx.send("❌ Invalid suggestion number.", ephemeral=True)
            return
        s = user_suggestions["suggestions"][index - 1]
        uid = ctx.author.id
        # Track voters to prevent double-voting
        voters = s.setdefault("voters", [])
        if uid in voters:
            await ctx.send(f"⚠️ You already voted for suggestion #{index}.", ephemeral=True)
            return
        voters.append(uid)
        s["votes"] = s.get("votes", 0) + 1
        save_data("user_suggestions", user_suggestions)
        await ctx.send(
            f"👍 Voted for suggestion #{index}! ({s['votes']} total votes)\n"
            f"**{s['event'].title()}:** {s['suggestion'][:80]}",
            ephemeral=True,
        )

    # --- /topsuggestions --- NEW (Phase 3: View top-voted suggestions)
    @commands.hybrid_command(name="topsuggestions")
    @app_commands.describe(event="Event to view top suggestions for (leave empty for all)")
    @cooldown(15)
    async def topsuggestions(self, ctx: commands.Context, event: str = None):
        """View the most popular community suggestions."""
        suggestions = user_suggestions["suggestions"]
        if event:
            suggestions = [s for s in suggestions if s["event"] == event.lower()]
        active = [s for s in suggestions if s.get("status") != "rejected"]
        if not active:
            await ctx.send("No suggestions found. Use `/suggest` to add one!", ephemeral=True)
            return
        # Sort by votes
        ranked = sorted(active, key=lambda s: s.get("votes", 0), reverse=True)[:10]
        lines = []
        for i, s in enumerate(ranked, 1):
            status = "✅" if s.get("status") == "approved" else "⏳"
            lines.append(
                f"{status} **#{i}** [{s['event'].title()}] — 👍 {s.get('votes', 0)} votes\n"
                f"  {s['suggestion'][:80]} — *{s['user_name'].split('#')[0]}*"
            )
        embed = discord.Embed(
            title=f"🏆 Top Suggestions{f' — {event.title()}' if event else ''}",
            description="\n\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Use /votesuggestion <#> to vote")
        await ctx.send(embed=embed, ephemeral=True)

    # --- /approvesuggestion ---
    @commands.hybrid_command(name="approvesuggestion")
    @app_commands.describe(index="Suggestion number", action="approve or reject")
    @app_commands.default_permissions(manage_guild=True)
    async def approvesuggestion(self, ctx: commands.Context, index: int, action: str):
        """Approve or reject a community suggestion."""
        if action.lower() not in ("approve", "reject"):
            await ctx.send("❌ Action must be 'approve' or 'reject'", ephemeral=True); return
        if index < 1 or index > len(user_suggestions["suggestions"]):
            await ctx.send("❌ Invalid index.", ephemeral=True); return
        s = user_suggestions["suggestions"][index - 1]
        s["status"] = "approved" if action.lower() == "approve" else "rejected"
        s["reviewed_by"] = str(ctx.author)
        save_data("user_suggestions", user_suggestions)
        status_text = "✅ Approved" if action.lower() == "approve" else "❌ Rejected"
        embed = discord.Embed(
            title=f"{status_text} Suggestion #{index}",
            description=f"**Event:** {s['event']}\n**Tip:** {s['suggestion']}\n**By:** {s['user_name']}",
            color=discord.Color.green() if action.lower() == "approve" else discord.Color.red(),
        )
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Suggestions(bot))
