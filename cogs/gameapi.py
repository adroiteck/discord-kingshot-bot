"""Game API cog — Kingshot gift code redemption, player lookup, auto-redeem."""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import hashlib
import time
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

from utils import (
    load_data, save_data, utc_now, cooldown, PaginatorView, LEADER_ROLES
)

log = logging.getLogger("kingshot-bot")

# ---------------------------------------------------------------------------
# Kingshot Game API config
# ---------------------------------------------------------------------------
API_BASE = "https://kingshot-giftcode.centurygame.com/api"
API_SALT = "mN4!pQs6JrYwV9"
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}

# Error code mapping
ERR_CODES = {
    20000: "Success",
    40004: "Invalid request / timeout",
    40007: "Code expired",
    40008: "Already claimed",
    40011: "Already claimed",
    40014: "Invalid code",
}

# Rate limit: ~30 requests/minute to be safe
RATE_LIMIT_DELAY = 2.5  # seconds between requests

# In-memory stores
registered_players = load_data("registered_players", {"players": {}})
code_history = load_data("code_history", {"codes": []})
auto_redeem_codes = load_data("auto_redeem_codes", {"pending": [], "completed": []})


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def _sign(params_str: str) -> str:
    """MD5-sign a parameter string with the Kingshot salt."""
    raw = params_str + API_SALT
    return hashlib.md5(raw.encode()).hexdigest()


def _make_login_payload(fid: str) -> dict:
    """Build signed payload for /api/player."""
    ts = int(time.time() * 1000)
    params = f"fid={fid}&time={ts}"
    return {"fid": fid, "time": ts, "sign": _sign(params)}


def _make_redeem_payload(fid: str, code: str) -> dict:
    """Build signed payload for /api/gift_code."""
    ts = int(time.time() * 1000)
    params = f"cdk={code}&fid={fid}&time={ts}"
    return {"fid": fid, "time": ts, "cdk": code, "sign": _sign(params)}


async def api_get_player(session: aiohttp.ClientSession, fid: str) -> dict:
    """Fetch player info from Kingshot API. Returns dict with nickname, furnace level, etc."""
    payload = _make_login_payload(fid)
    try:
        async with session.post(f"{API_BASE}/player", data=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
            return data
    except Exception as e:
        log.error(f"API player lookup failed for {fid}: {e}")
        return {"err_code": -1, "msg": str(e)}


async def api_redeem_code(session: aiohttp.ClientSession, fid: str, code: str) -> dict:
    """Redeem a gift code for a player. Returns API response dict."""
    # First login/authenticate
    login_result = await api_get_player(session, fid)
    if login_result.get("err_code", -1) != 0 and login_result.get("code", -1) != 0:
        # Try anyway — some API versions don't require pre-login
        pass

    await asyncio.sleep(1)  # Small delay between login and redeem

    payload = _make_redeem_payload(fid, code)
    try:
        async with session.post(f"{API_BASE}/gift_code", data=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
            return data
    except Exception as e:
        log.error(f"API redeem failed for {fid}/{code}: {e}")
        return {"err_code": -1, "msg": str(e)}


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class GameAPI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def cog_unload(self):
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())
        self.auto_redeem_check.cancel()

    def start_tasks(self):
        """Start background tasks (called from on_ready)."""
        if not self.auto_redeem_check.is_running():
            self.auto_redeem_check.start()

    # -----------------------------------------------------------------------
    # /register — Link Discord user to in-game player ID
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="register")
    @app_commands.describe(
        player_id="Your Kingshot player ID (FID) — tap your avatar in-game to find it"
    )
    @cooldown(30)
    async def register(self, ctx: commands.Context, player_id: str):
        """Link your Discord account to your Kingshot player ID for auto gift code redemption."""
        await ctx.defer(ephemeral=True)

        # Validate the player ID with the game API
        session = await self._get_session()
        result = await api_get_player(session, player_id)

        # Check if API returned valid player data
        player_data = result.get("data", {})
        nickname = player_data.get("nickname", None)
        furnace_lv = player_data.get("stove_lv", player_data.get("furnace_lv", None))

        uid = str(ctx.author.id)
        registered_players["players"][uid] = {
            "fid": player_id,
            "discord_name": ctx.author.display_name,
            "registered_at": utc_now().isoformat(),
            "nickname": nickname,
            "furnace_lv": furnace_lv,
            "last_checked": utc_now().isoformat(),
        }
        save_data("registered_players", registered_players)

        embed = discord.Embed(title="✅ Registered!", color=discord.Color.green())
        embed.add_field(name="Player ID (FID)", value=player_id, inline=True)
        if nickname:
            embed.add_field(name="In-Game Name", value=nickname, inline=True)
        if furnace_lv:
            embed.add_field(name="Furnace Level", value=str(furnace_lv), inline=True)
        embed.add_field(
            name="What's Next?",
            value="You'll now automatically receive gift code redemptions when leaders submit codes with `/submitcode`!",
            inline=False,
        )
        embed.set_footer(text="Use /unregister to remove your link")
        await ctx.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /unregister — Remove player ID link
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="unregister")
    @cooldown(30)
    async def unregister(self, ctx: commands.Context):
        """Remove your Kingshot player ID link."""
        uid = str(ctx.author.id)
        if uid in registered_players["players"]:
            del registered_players["players"][uid]
            save_data("registered_players", registered_players)
            await ctx.send("✅ Your player ID has been unregistered. You'll no longer receive auto-redeems.", ephemeral=True)
        else:
            await ctx.send("❌ You don't have a registered player ID.", ephemeral=True)

    # -----------------------------------------------------------------------
    # /whoami — Show your linked game profile
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="whoami")
    @cooldown(15)
    async def whoami(self, ctx: commands.Context):
        """Show your linked Kingshot player info."""
        uid = str(ctx.author.id)
        player = registered_players["players"].get(uid)
        if not player:
            await ctx.send("❌ You haven't registered yet. Use `/register <your_FID>` to link your account!", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)

        # Refresh from API
        session = await self._get_session()
        result = await api_get_player(session, player["fid"])
        player_data = result.get("data", {})

        if player_data:
            player["nickname"] = player_data.get("nickname", player.get("nickname"))
            player["furnace_lv"] = player_data.get("stove_lv", player_data.get("furnace_lv", player.get("furnace_lv")))
            player["last_checked"] = utc_now().isoformat()
            save_data("registered_players", registered_players)

        embed = discord.Embed(title=f"🎮 {player.get('nickname', 'Unknown')}", color=discord.Color.blue())
        embed.add_field(name="FID", value=player["fid"], inline=True)
        embed.add_field(name="Furnace Lv", value=str(player.get("furnace_lv", "?")), inline=True)
        embed.add_field(name="Registered", value=f"<t:{int(datetime.fromisoformat(player['registered_at']).timestamp())}:R>", inline=True)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /submitcode — Redeem a gift code for all registered players (Leader only)
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="submitcode")
    @app_commands.describe(code="The gift code to redeem for all registered players")
    @commands.has_any_role(*LEADER_ROLES)
    @cooldown(60)
    async def submit_code(self, ctx: commands.Context, code: str):
        """Redeem a gift code for ALL registered alliance members."""
        code = code.strip().upper()

        # Check if already processed
        if code in [c["code"] for c in code_history["codes"]]:
            await ctx.send(f"⚠️ Code `{code}` has already been submitted before.", ephemeral=True)
            return

        players = registered_players["players"]
        if not players:
            await ctx.send("❌ No players registered yet. Members need to use `/register` first!", ephemeral=True)
            return

        await ctx.defer()

        embed = discord.Embed(
            title=f"🎁 Redeeming Code: `{code}`",
            description=f"Processing for **{len(players)}** registered players...\nThis may take a few minutes.",
            color=discord.Color.gold(),
        )
        status_msg = await ctx.send(embed=embed)

        # Process redemptions
        session = await self._get_session()
        results = {"success": 0, "already_claimed": 0, "failed": 0, "errors": []}
        processed = 0

        for uid, player in players.items():
            fid = player["fid"]
            try:
                resp = await api_redeem_code(session, fid, code)
                err_code = resp.get("err_code", resp.get("code", -1))

                if err_code == 20000 or err_code == 0:
                    results["success"] += 1
                elif err_code in (40008, 40011):
                    results["already_claimed"] += 1
                elif err_code == 40014:
                    # Invalid code — stop processing
                    results["errors"].append(f"Code `{code}` is invalid")
                    break
                elif err_code == 40007:
                    results["errors"].append(f"Code `{code}` has expired")
                    break
                else:
                    results["failed"] += 1
                    msg = ERR_CODES.get(err_code, resp.get("msg", f"Error {err_code}"))
                    results["errors"].append(f"{player.get('nickname', fid)}: {msg}")

            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"{player.get('nickname', fid)}: {str(e)[:50]}")

            processed += 1
            # Rate limiting
            await asyncio.sleep(RATE_LIMIT_DELAY)

            # Update progress every 10 players
            if processed % 10 == 0:
                embed.description = f"Processing... **{processed}/{len(players)}** done"
                try:
                    await status_msg.edit(embed=embed)
                except Exception:
                    pass

        # Final results
        embed = discord.Embed(title=f"🎁 Code `{code}` — Results", color=discord.Color.green() if results["success"] > 0 else discord.Color.red())
        embed.add_field(name="✅ Redeemed", value=str(results["success"]), inline=True)
        embed.add_field(name="📦 Already Claimed", value=str(results["already_claimed"]), inline=True)
        embed.add_field(name="❌ Failed", value=str(results["failed"]), inline=True)
        if results["errors"]:
            error_text = "\n".join(results["errors"][:10])
            embed.add_field(name="⚠️ Notes", value=error_text, inline=False)
        embed.set_footer(text=f"Submitted by {ctx.author.display_name}")

        await status_msg.edit(embed=embed)

        # Record in history
        code_history["codes"].append({
            "code": code,
            "submitted_by": str(ctx.author.id),
            "submitted_at": utc_now().isoformat(),
            "results": {k: v for k, v in results.items() if k != "errors"},
        })
        save_data("code_history", code_history)

    # -----------------------------------------------------------------------
    # /redeemcode — Redeem a code for yourself only
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="redeemcode")
    @app_commands.describe(code="The gift code to redeem")
    @cooldown(15)
    async def redeem_code_self(self, ctx: commands.Context, code: str):
        """Redeem a gift code for your own account."""
        uid = str(ctx.author.id)
        player = registered_players["players"].get(uid)
        if not player:
            await ctx.send("❌ You need to `/register` your player ID first!", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        code = code.strip().upper()
        session = await self._get_session()
        resp = await api_redeem_code(session, player["fid"], code)
        err_code = resp.get("err_code", resp.get("code", -1))

        if err_code == 20000 or err_code == 0:
            await ctx.send(f"✅ Code `{code}` redeemed successfully! Check your in-game mail.", ephemeral=True)
        elif err_code in (40008, 40011):
            await ctx.send(f"📦 Code `{code}` was already claimed on your account.", ephemeral=True)
        elif err_code == 40014:
            await ctx.send(f"❌ Code `{code}` is invalid.", ephemeral=True)
        elif err_code == 40007:
            await ctx.send(f"⏰ Code `{code}` has expired.", ephemeral=True)
        else:
            msg = ERR_CODES.get(err_code, resp.get("msg", f"Unknown error ({err_code})"))
            await ctx.send(f"⚠️ Failed to redeem `{code}`: {msg}", ephemeral=True)

    # -----------------------------------------------------------------------
    # /codehistory — View past redeemed codes
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="codehistory")
    @cooldown(15)
    async def code_history_cmd(self, ctx: commands.Context):
        """View previously submitted gift codes."""
        codes = code_history.get("codes", [])
        if not codes:
            await ctx.send("📭 No gift codes have been submitted yet.", ephemeral=True)
            return

        lines = []
        for c in reversed(codes[-20:]):
            ts = datetime.fromisoformat(c["submitted_at"])
            res = c.get("results", {})
            lines.append(
                f"`{c['code']}` — ✅{res.get('success', 0)} 📦{res.get('already_claimed', 0)} ❌{res.get('failed', 0)} — <t:{int(ts.timestamp())}:R>"
            )

        embed = discord.Embed(title="📋 Gift Code History", description="\n".join(lines), color=discord.Color.blue())
        embed.set_footer(text=f"{len(codes)} codes submitted total")
        await ctx.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /registered — View all registered players (Leader only)
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="registered")
    @commands.has_any_role(*LEADER_ROLES)
    @cooldown(30)
    async def registered_list(self, ctx: commands.Context):
        """View all registered players and their game IDs."""
        players = registered_players["players"]
        if not players:
            await ctx.send("📭 No players registered yet.", ephemeral=True)
            return

        lines = []
        for uid, p in players.items():
            member = ctx.guild.get_member(int(uid))
            name = member.display_name if member else p.get("discord_name", "Unknown")
            nickname = p.get("nickname", "?")
            furnace = p.get("furnace_lv", "?")
            lines.append(f"**{name}** → `{p['fid']}` | {nickname} | Furnace Lv.{furnace}")

        # Paginate if many players
        if len(lines) <= 15:
            embed = discord.Embed(
                title=f"📋 Registered Players ({len(players)})",
                description="\n".join(lines),
                color=discord.Color.blue(),
            )
            await ctx.send(embed=embed, ephemeral=True)
        else:
            embeds = []
            for i in range(0, len(lines), 15):
                chunk = lines[i : i + 15]
                embed = discord.Embed(
                    title=f"📋 Registered Players ({len(players)})",
                    description="\n".join(chunk),
                    color=discord.Color.blue(),
                )
                embed.set_footer(text=f"Page {i // 15 + 1}")
                embeds.append(embed)
            await ctx.send(embed=embeds[0], view=PaginatorView(embeds), ephemeral=True)

    # -----------------------------------------------------------------------
    # /refreshplayers — Bulk refresh all player data from API (Leader only)
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="refreshplayers")
    @commands.has_any_role(*LEADER_ROLES)
    @cooldown(300)
    async def refresh_players(self, ctx: commands.Context):
        """Refresh all registered players' game data from the API."""
        players = registered_players["players"]
        if not players:
            await ctx.send("📭 No players registered.", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        session = await self._get_session()
        updated = 0

        for uid, player in players.items():
            try:
                result = await api_get_player(session, player["fid"])
                pdata = result.get("data", {})
                if pdata:
                    player["nickname"] = pdata.get("nickname", player.get("nickname"))
                    player["furnace_lv"] = pdata.get("stove_lv", pdata.get("furnace_lv", player.get("furnace_lv")))
                    player["last_checked"] = utc_now().isoformat()
                    updated += 1
            except Exception as e:
                log.warning(f"Failed to refresh player {player['fid']}: {e}")
            await asyncio.sleep(RATE_LIMIT_DELAY)

        save_data("registered_players", registered_players)
        await ctx.send(f"✅ Refreshed **{updated}/{len(players)}** players' game data.", ephemeral=True)

    # -----------------------------------------------------------------------
    # /lookup — Look up any player by FID
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="lookup")
    @app_commands.describe(player_id="The player's FID to look up")
    @cooldown(15)
    async def lookup_player(self, ctx: commands.Context, player_id: str):
        """Look up any Kingshot player by their FID."""
        await ctx.defer(ephemeral=True)
        session = await self._get_session()
        result = await api_get_player(session, player_id)
        pdata = result.get("data", {})

        if not pdata or not pdata.get("nickname"):
            await ctx.send(f"❌ Could not find player with FID `{player_id}`. Check the ID and try again.", ephemeral=True)
            return

        embed = discord.Embed(title=f"🔍 Player Lookup", color=discord.Color.blue())
        embed.add_field(name="FID", value=player_id, inline=True)
        embed.add_field(name="Nickname", value=pdata.get("nickname", "?"), inline=True)
        furnace = pdata.get("stove_lv", pdata.get("furnace_lv", "?"))
        embed.add_field(name="Furnace Level", value=str(furnace), inline=True)
        # Include any other fields the API returns
        if pdata.get("kid"):
            embed.add_field(name="Kingdom", value=str(pdata["kid"]), inline=True)
        if pdata.get("avatar_image"):
            embed.set_thumbnail(url=pdata["avatar_image"])
        await ctx.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # Background task: auto-redeem pending codes
    # -----------------------------------------------------------------------
    @tasks.loop(minutes=5)
    async def auto_redeem_check(self):
        """Check for pending auto-redeem codes and process them."""
        pending = auto_redeem_codes.get("pending", [])
        if not pending:
            return

        session = await self._get_session()
        code_entry = pending[0]
        code = code_entry["code"]
        remaining_fids = code_entry.get("remaining_fids", [])

        if not remaining_fids:
            # Move to completed
            auto_redeem_codes["completed"].append(code_entry)
            auto_redeem_codes["pending"].pop(0)
            save_data("auto_redeem_codes", auto_redeem_codes)
            return

        # Process up to 10 players per cycle
        batch = remaining_fids[:10]
        for fid in batch:
            try:
                await api_redeem_code(session, fid, code)
            except Exception as e:
                log.warning(f"Auto-redeem failed for {fid}/{code}: {e}")
            await asyncio.sleep(RATE_LIMIT_DELAY)

        code_entry["remaining_fids"] = remaining_fids[10:]
        save_data("auto_redeem_codes", auto_redeem_codes)

    @auto_redeem_check.before_loop
    async def before_auto_redeem(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(GameAPI(bot))
