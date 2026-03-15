"""Moderation cog — kick, mute, clear, promote, demote, setup, announcements."""
import discord
from discord.ext import commands
from discord import app_commands
from typing import List
from datetime import timedelta
import asyncio

from utils import (
    load_config, save_config, PaginatorView, ROLE_COLORS, LEADER_ROLES,
    announcement_name_autocomplete, cooldown, utc_now, RolePanelView
)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- /setup ---
    @commands.hybrid_command(name="setup")
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def setup_server(self, ctx: commands.Context):
        """Create all channels, roles, and permissions for the Kingshot guild server."""
        await ctx.send("🔧 **Starting server setup...** This may take a moment.")
        guild = ctx.guild
        existing_roles = {r.name for r in guild.roles}
        for role_name, color in ROLE_COLORS.items():
            if role_name not in existing_roles:
                await guild.create_role(name=role_name, color=color, mentionable=True, reason="Kingshot bot setup")
        await ctx.send(f"✅ Roles configured ({len(ROLE_COLORS)} roles)")
        await ctx.send("🎉 **Setup complete!** Use `/help` to see all commands.")

    # --- /announce ---
    @commands.hybrid_command(name="announce")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
    @app_commands.describe(channel_name="Channel to post in", message="Announcement text")
    async def announce(self, ctx: commands.Context, channel_name: str, *, message: str):
        """Send an announcement."""
        channel = discord.utils.get(ctx.guild.text_channels, name=channel_name)
        if not channel:
            await ctx.send(f"❌ Channel `#{channel_name}` not found."); return
        embed = discord.Embed(
            title="📢 Announcement", description=message,
            color=discord.Color.gold(), timestamp=utc_now(),
        )
        embed.set_footer(text=f"Posted by {ctx.author.display_name}")
        await channel.send(embed=embed)
        await ctx.send(f"✅ Announcement sent to #{channel_name}")

    # --- /listannouncements ---
    @commands.hybrid_command(name="listannouncements")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
    @cooldown(15)
    async def list_announcements(self, ctx: commands.Context):
        """List scheduled announcements."""
        cfg = load_config()
        announcements = cfg.get("scheduled_announcements", [])
        if not announcements:
            await ctx.send("No scheduled announcements configured."); return
        embeds = []
        per_page = 5
        for i in range(0, len(announcements), per_page):
            page = announcements[i:i+per_page]
            embed = discord.Embed(title="📋 Scheduled Announcements", color=discord.Color.blue())
            for ann in page:
                status = "✅ ON" if ann.get("enabled") else "❌ OFF"
                embed.add_field(
                    name=f"{ann['name']} — {status}",
                    value=f"**Channel:** #{ann['channel']}\n**Schedule:** `{ann['cron']}`\n**Message:** {ann['message'][:100]}...",
                    inline=False,
                )
            embed.set_footer(text=f"Page {i//per_page + 1}/{(len(announcements)-1)//per_page + 1}")
            embeds.append(embed)
        if len(embeds) == 1:
            await ctx.send(embed=embeds[0])
        else:
            await ctx.send(embed=embeds[0], view=PaginatorView(embeds))

    # --- /toggleannouncement ---
    @commands.hybrid_command(name="toggleannouncement")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
    @app_commands.autocomplete(name=announcement_name_autocomplete)
    @app_commands.describe(name="The announcement to toggle")
    async def toggle_announcement(self, ctx: commands.Context, name: str):
        """Toggle a scheduled announcement on/off."""
        cfg = load_config()
        for ann in cfg.get("scheduled_announcements", []):
            if ann["name"] == name:
                ann["enabled"] = not ann["enabled"]
                save_config(cfg)
                status = "enabled" if ann["enabled"] else "disabled"
                await ctx.send(f"✅ `{name}` is now **{status}**."); return
        await ctx.send(f"❌ Announcement `{name}` not found.")

    # --- /promote ---
    @commands.hybrid_command(name="promote")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
    @app_commands.describe(member="Member to promote", role_name="Role to assign")
    async def promote(self, ctx: commands.Context, member: discord.Member, *, role_name: str):
        """Promote a member."""
        role = discord.utils.get(ctx.guild.roles, name=role_name)
        if not role:
            await ctx.send(f"❌ Role `{role_name}` not found."); return
        await member.add_roles(role)
        await ctx.send(f"✅ {member.display_name} promoted to **{role_name}**!")

    # --- /demote ---
    @commands.hybrid_command(name="demote")
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_any_role("R5 | Alliance Leader", "R4 | Leadership")
    @app_commands.describe(member="Member to demote", role_name="Role to remove")
    async def demote(self, ctx: commands.Context, member: discord.Member, *, role_name: str):
        """Remove a role from a member."""
        role = discord.utils.get(ctx.guild.roles, name=role_name)
        if not role:
            await ctx.send(f"❌ Role `{role_name}` not found."); return
        await member.remove_roles(role)
        await ctx.send(f"✅ {member.display_name} removed from **{role_name}**.")

    # --- /kick ---
    @commands.hybrid_command(name="kick")
    @app_commands.default_permissions(kick_members=True)
    @commands.has_permissions(kick_members=True)
    @app_commands.describe(member="Member to kick", reason="Reason for kicking")
    async def kick_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        """Kick a member."""
        await member.kick(reason=reason)
        await ctx.send(f"👢 {member.display_name} kicked. Reason: {reason}")

    # --- /mute ---
    @commands.hybrid_command(name="mute")
    @app_commands.default_permissions(moderate_members=True)
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(member="Member to mute", minutes="Duration in minutes")
    async def mute_member(self, ctx: commands.Context, member: discord.Member, minutes: int = 10):
        """Timeout a member."""
        await member.timeout(timedelta(minutes=minutes), reason=f"Muted by {ctx.author.display_name}")
        await ctx.send(f"🔇 {member.display_name} muted for {minutes} minutes.")

    # --- /unmute ---
    @commands.hybrid_command(name="unmute")
    @app_commands.default_permissions(moderate_members=True)
    @commands.has_permissions(manage_roles=True)
    @app_commands.describe(member="Member to unmute")
    async def unmute_member(self, ctx: commands.Context, member: discord.Member):
        """Remove timeout."""
        await member.timeout(None, reason=f"Unmuted by {ctx.author.display_name}")
        await ctx.send(f"🔊 {member.display_name} unmuted.")

    # --- /clear ---
    @commands.hybrid_command(name="clear")
    @app_commands.default_permissions(manage_messages=True)
    @commands.has_permissions(manage_messages=True)
    @app_commands.describe(amount="Number of messages to delete (max 100)")
    async def clear_messages(self, ctx: commands.Context, amount: int = 10):
        """Delete messages."""
        if amount > 100:
            await ctx.send("❌ Max 100 messages at a time."); return
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(f"🗑️ Deleted {len(deleted) - 1} messages.")
        await asyncio.sleep(3)
        await msg.delete()

    # --- /rolepanel ---
    @commands.hybrid_command(name="rolepanel")
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def role_panel(self, ctx: commands.Context):
        """Post role selection panel."""
        embed = discord.Embed(
            title="🏷️ Select Your Alliance Role",
            description="Click buttons to add/remove roles.",
            color=discord.Color.purple(),
        )
        for role_name in ROLE_COLORS:
            embed.add_field(name=role_name, value="\u200b", inline=False)
        await ctx.send(embed=embed, view=RolePanelView())
        await ctx.send("✅ Role panel posted!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
