"""Game API cog — Kingshot gift code redemption, player lookup, auto-redeem, wiki scraper."""
import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import hashlib
import time
import logging
import asyncio
import re
import os
from datetime import datetime, timezone
from typing import Optional

from utils import (
    load_data, save_data, utc_now, cooldown, PaginatorView, LEADER_ROLES, load_config,
    load_event_cycle, EVENT_CYCLE_PATH, load_json_file, sanitize_html
)
import json

log = logging.getLogger("kingshot-bot")

# ---------------------------------------------------------------------------
# Kingshot Game API config
# ---------------------------------------------------------------------------
API_BASE = "https://kingshot-giftcode.centurygame.com/api"
API_SALT = os.environ.get("KINGSHOT_API_SALT", "mN4!pQs6JrYwV9")
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}

# Channel ID where official gift codes are forwarded/linked (from config.json)
def _get_giftcode_watch_channel():
    cfg = load_config()
    return int(cfg.get("giftcode_watch_channel", "1480215871359029351"))

# Gift code pattern — alphanumeric, 6-30 chars, often with mixed case
# Typical codes: KS2025SPRING, KINGSHOTGIFT, NEWYEAR2025, etc.
# --- Code extraction patterns (ordered by specificity) ---
# 1. Official format: Gift Code: `CODE` or Gift Code: CODE
P_BACKTICK_CODE = re.compile(r'Gift\s+Code\s*:\s*`([A-Za-z0-9]{4,30})`', re.IGNORECASE)
# 2. Triple-backtick code blocks (bot-submitted embeds)
P_CODEBLOCK = re.compile(r'```\s*([A-Za-z0-9]{4,30})\s*```')
# 3. Inline backtick
P_INLINE_TICK = re.compile(r'`([A-Za-z0-9]{4,30})`')
# 4. Fallback: bare alphanumeric token on its own line or surrounded by whitespace
P_BARE_CODE = re.compile(r'(?:^|\n)\s*([A-Za-z0-9]{6,30})\s*(?:\n|$)')

# Words / fragments to never treat as codes
CODE_EXCLUDE = {
    # common English
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her",
    "was", "one", "our", "out", "has", "his", "how", "its", "may", "new", "now",
    "old", "see", "way", "who", "did", "get", "let", "say", "she", "too", "use",
    "from", "have", "this", "that", "with", "they", "been", "said", "each",
    "which", "their", "will", "other", "about", "many", "then", "them", "would",
    "make", "like", "time", "very", "when", "come", "could", "more", "some",
    "what", "than", "first", "also", "into", "just", "your", "over", "such",
    "after", "year", "most", "only", "made", "find", "here", "thing", "give",
    # gift-code domain words
    "codes", "code", "gift", "free", "link", "click", "redeem", "reward",
    "rewards", "claim", "today", "check", "hello", "everyone", "update",
    "valid", "until", "expired", "active", "submitted", "android",
    "settings", "avatar", "interface", "website", "store", "center",
    "giftcode", "giftcodes", "official", "governors", "bookmark",
    "access", "concierge", "member", "expired",
    # brand / platform
    "kingshot", "whiteout", "survival", "discord", "server", "channel",
    "centurygame", "centurygames", "kingshotwiki",
    "https", "http", "www", "com", "org", "message", "posted",
    "copy", "wiki", "march", "february", "january", "april",
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

# Rate limiting with adaptive backoff
RATE_LIMIT_DELAY = 2.5  # base seconds between requests
_api_backoff = {"delay": 2.5, "consecutive_errors": 0, "last_success": 0}

def _get_api_delay() -> float:
    """Get current rate limit delay with adaptive backoff."""
    return min(_api_backoff["delay"], 30.0)  # Cap at 30s

def _api_success():
    """Record successful API call — reduce backoff."""
    _api_backoff["consecutive_errors"] = 0
    _api_backoff["delay"] = RATE_LIMIT_DELAY
    _api_backoff["last_success"] = time.time()

def _api_error():
    """Record API error — increase backoff exponentially."""
    _api_backoff["consecutive_errors"] += 1
    _api_backoff["delay"] = min(RATE_LIMIT_DELAY * (2 ** _api_backoff["consecutive_errors"]), 30.0)

# In-memory stores
registered_players = load_data("registered_players", {"players": {}})
code_history = load_data("code_history", {"codes": []})
auto_redeem_codes = load_data("auto_redeem_codes", {"pending": [], "completed": []})
gift_codes = load_data("gift_codes", {"codes": []})


# Wiki scraper config
WIKI_BASE_URL = "https://kingshotwiki.com"
WIKI_CODES_URL = f"{WIKI_BASE_URL}/sneak-peek/"
WIKI_CODE_PATTERN = re.compile(r'Gift\s+Code\s*:\s*[`"]?([A-Za-z0-9]{4,30})[`"]?', re.IGNORECASE)
WIKI_EXPIRY_PATTERN = re.compile(r'(?:Expir(?:es?|ation|y)|Valid\s+(?:until|through|till))\s*[:\-–]?\s*(\w+\s+\d{1,2},?\s*\d{4})', re.IGNORECASE)
# Event date patterns for scraping event schedule info
WIKI_EVENT_DATE_PATTERN = re.compile(
    r'(\w[\w\s\']+?)\s*[:\-–]\s*'
    r'(?:(\w+\s+\d{1,2})\s*[\-–]\s*(\w+\s+\d{1,2}),?\s*(\d{4})'  # "Mar 17 – Mar 23, 2026"
    r'|(\w+\s+\d{1,2},?\s*\d{4})\s*[\-–]\s*(\w+\s+\d{1,2},?\s*\d{4})'  # "March 17, 2026 – March 23, 2026"
    r')', re.IGNORECASE
)
WIKI_DURATION_PATTERN = re.compile(r'(?:Duration|Lasts?|Length)\s*[:\-–]\s*(\d+)\s*days?', re.IGNORECASE)


def _mark_code_expired(code: str):
    """Mark a gift code as expired in the gift_codes store."""
    code = code.upper()
    for c in gift_codes.get("codes", []):
        if c["code"].upper() == code:
            c["expired"] = True
            c["expired_at"] = utc_now().isoformat()
            save_data("gift_codes", gift_codes)
            log.info(f"Marked gift code {code} as expired")
            return
    # If code isn't in the list, add it as expired
    gift_codes.setdefault("codes", []).append({
        "code": code, "expired": True,
        "expired_at": utc_now().isoformat(), "source": "api_detection"
    })
    save_data("gift_codes", gift_codes)


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
    """Fetch player info from Kingshot API."""
    payload = _make_login_payload(fid)
    try:
        async with session.post(f"{API_BASE}/player", data=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
            _api_success()
            return data
    except Exception as e:
        _api_error()
        log.error(f"API player lookup failed for {fid}: {e}")
        return {"err_code": -1, "msg": str(e)}


async def api_redeem_code(session: aiohttp.ClientSession, fid: str, code: str) -> dict:
    """Redeem a gift code for a player. Returns API response dict."""
    # First login/authenticate
    await api_get_player(session, fid)
    await asyncio.sleep(_get_api_delay())

    payload = _make_redeem_payload(fid, code)
    try:
        async with session.post(f"{API_BASE}/gift_code", data=payload, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
            _api_success()
            return data
    except Exception as e:
        _api_error()
        log.error(f"API redeem failed for {fid}/{code}: {e}")
        return {"err_code": -1, "msg": str(e)}


def _is_valid_candidate(c: str) -> bool:
    """Check whether a candidate string looks like a real gift code."""
    if c.lower() in CODE_EXCLUDE:
        return False
    if c.islower():
        return False
    if c.isdigit():
        return False
    # URL fragments
    if "." in c or "/" in c:
        return False
    has_letter = any(ch.isalpha() for ch in c)
    has_digit = any(ch.isdigit() for ch in c)
    # Mixed alphanumeric is a strong signal; all-caps 6+ chars is also valid
    return (has_letter and has_digit) or (c.isupper() and len(c) >= 6)


def _extract_codes_from_message(content: str) -> list[str]:
    """Extract potential gift codes from a message using prioritized patterns.

    Priority order:
      1. "Gift Code: `CODE`" — official announcement format
      2. ```CODE``` — triple-backtick code blocks (bot embeds)
      3. `CODE` — inline backtick
      4. Bare all-caps/mixed token on its own line (user paste)
    """
    codes: list[str] = []

    # --- High-confidence structured patterns (skip exclusion for these) ---
    # 1. Official "Gift Code: `CODE`"
    for m in P_BACKTICK_CODE.finditer(content):
        c = m.group(1).upper()
        if not c.isdigit() and c.lower() not in CODE_EXCLUDE:
            codes.append(c)

    # 2. Triple-backtick code blocks
    for m in P_CODEBLOCK.finditer(content):
        c = m.group(1).strip().upper()
        if not c.isdigit() and c.lower() not in CODE_EXCLUDE:
            codes.append(c)

    # 3. Inline backtick (that weren't already matched by pattern 1)
    for m in P_INLINE_TICK.finditer(content):
        c = m.group(1).upper()
        if c not in codes and _is_valid_candidate(c):
            codes.append(c)

    # 4. Bare tokens on their own line (user pastes)
    for m in P_BARE_CODE.finditer(content):
        c = m.group(1).upper()
        if c not in codes and _is_valid_candidate(c):
            codes.append(c)

    return list(dict.fromkeys(codes))  # deduplicate preserving order


async def _redeem_for_all(session: aiohttp.ClientSession, code: str, report_channel: Optional[discord.TextChannel] = None) -> dict:
    """Redeem a code for all registered players. Returns results dict."""
    players = registered_players["players"]
    results = {"success": 0, "already_claimed": 0, "failed": 0, "invalid": False, "expired": False}

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
                results["invalid"] = True
                break
            elif err_code == 40007:
                results["expired"] = True
                break
            else:
                results["failed"] += 1
        except Exception as e:
            results["failed"] += 1
            log.warning(f"Redeem error {fid}/{code}: {e}")

        await asyncio.sleep(RATE_LIMIT_DELAY)

    return results


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class GameAPI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._processing_codes: set[str] = set()  # prevent duplicate processing

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def cog_unload(self):
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())
        self.auto_redeem_check.cancel()
        self.wiki_code_scraper.cancel()

    def start_tasks(self):
        """Start background tasks (called from on_ready)."""
        if not self.auto_redeem_check.is_running():
            self.auto_redeem_check.start()
        if not self.wiki_code_scraper.is_running():
            self.wiki_code_scraper.start()

    # -----------------------------------------------------------------------
    # AUTO-REDEEM — callable from /addcode or message watcher
    # -----------------------------------------------------------------------
    async def auto_redeem_code(self, code: str, guild: discord.Guild):
        """Redeem a gift code for all registered players. Called by /addcode or message watcher."""
        code = code.upper()
        players = registered_players["players"]
        if not players:
            log.info(f"Auto-redeem {code}: no players registered")
            return

        # Skip if already processed or currently processing
        already_done = any(c["code"] == code for c in code_history["codes"])
        if already_done or code in self._processing_codes:
            log.info(f"Auto-redeem {code}: already processed or in progress")
            return

        self._processing_codes.add(code)
        log.info(f"Auto-redeem {code}: validating for {len(players)} players...")

        session = await self._get_session()

        # Validate on first player
        first_fid = next(iter(players.values()))["fid"]
        test_resp = await api_redeem_code(session, first_fid, code)
        test_err = test_resp.get("err_code", test_resp.get("code", -1))

        if test_err == 40014:
            log.info(f"Code {code} is not a valid gift code, skipping")
            self._processing_codes.discard(code)
            return
        if test_err == 40007:
            log.info(f"Code {code} is expired, auto-marking as expired")
            _mark_code_expired(code)
            self._processing_codes.discard(code)
            return

        log.info(f"Code {code} is valid! Redeeming for {len(players)} players...")
        results = {"success": 0, "already_claimed": 0, "failed": 0}
        if test_err == 20000 or test_err == 0:
            results["success"] = 1
        elif test_err in (40008, 40011):
            results["already_claimed"] = 1
        else:
            results["failed"] = 1

        # Redeem for remaining players
        remaining = list(players.items())[1:]
        for uid, player in remaining:
            fid = player["fid"]
            try:
                resp = await api_redeem_code(session, fid, code)
                err_code = resp.get("err_code", resp.get("code", -1))
                if err_code == 20000 or err_code == 0:
                    results["success"] += 1
                elif err_code in (40008, 40011):
                    results["already_claimed"] += 1
                elif err_code == 40014:
                    break
                else:
                    results["failed"] += 1
            except Exception:
                results["failed"] += 1
            await asyncio.sleep(RATE_LIMIT_DELAY)

        # Record in history
        code_history["codes"].append({
            "code": code,
            "submitted_by": "auto",
            "submitted_at": utc_now().isoformat(),
            "results": results,
        })
        save_data("code_history", code_history)

        # Post results
        report_ch = discord.utils.get(guild.text_channels, name="gift-codes") if guild else None
        if report_ch:
            embed = discord.Embed(
                title=f"🤖 Auto-Redeemed: `{code}`",
                description="Gift code automatically redeemed for all registered players!",
                color=discord.Color.green() if results["success"] > 0 else discord.Color.orange(),
            )
            embed.add_field(name="✅ Redeemed", value=str(results["success"]), inline=True)
            embed.add_field(name="📦 Already Claimed", value=str(results["already_claimed"]), inline=True)
            embed.add_field(name="❌ Failed", value=str(results["failed"]), inline=True)
            try:
                await report_ch.send(embed=embed)
            except Exception as e:
                log.warning(f"Could not post auto-redeem results: {e}")

        log.info(f"Auto-redeem complete for {code}: {results}")
        self._processing_codes.discard(code)

    # -----------------------------------------------------------------------
    # CATCH-UP REDEMPTION — Redeem all active codes for a single new player
    # -----------------------------------------------------------------------
    async def catch_up_redeem(self, fid: str, discord_name: str, guild: discord.Guild | None = None):
        """Redeem all active (non-expired) gift codes for a newly registered player."""
        active_codes = [c["code"] for c in gift_codes.get("codes", []) if not c.get("expired")]
        if not active_codes:
            log.info(f"Catch-up redeem for {discord_name}: no active codes")
            return

        log.info(f"Catch-up redeem for {discord_name} (FID {fid}): {len(active_codes)} active code(s)")
        session = await self._get_session()
        results = {"success": 0, "already_claimed": 0, "failed": 0}

        for code in active_codes:
            try:
                resp = await api_redeem_code(session, fid, code.upper())
                err_code = resp.get("err_code", resp.get("code", -1))
                if err_code in (20000, 0):
                    results["success"] += 1
                elif err_code in (40008, 40011):
                    results["already_claimed"] += 1
                elif err_code == 40014:
                    log.info(f"Catch-up: code {code} invalid, skipping")
                elif err_code == 40007:
                    log.info(f"Catch-up: code {code} expired, skipping")
                else:
                    results["failed"] += 1
            except Exception as e:
                log.warning(f"Catch-up redeem error for {code}: {e}")
                results["failed"] += 1
            await asyncio.sleep(RATE_LIMIT_DELAY)

        log.info(f"Catch-up redeem done for {discord_name}: {results}")

        # Notify in gift-codes channel
        if guild and results["success"] > 0:
            report_ch = discord.utils.get(guild.text_channels, name="gift-codes")
            if report_ch:
                try:
                    await report_ch.send(
                        f"🆕 **{discord_name}** just registered — auto-redeemed **{results['success']}** active gift code(s) for them!"
                    )
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # MESSAGE WATCHER — Auto-detect codes from the linked official channel
    # -----------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Watch the linked official gift code channel and auto-redeem detected codes."""
        # Only watch the specific linked channel
        if message.channel.id != _get_giftcode_watch_channel():
            return

        # Ignore bot's own messages
        if message.author == self.bot.user:
            return

        # Combine message content + embed text for code extraction
        text_parts = [message.content]
        for embed in message.embeds:
            if embed.title:
                text_parts.append(embed.title)
            if embed.description:
                text_parts.append(embed.description)
            for field in embed.fields:
                text_parts.append(field.name)
                text_parts.append(field.value)
        full_text = " ".join(text_parts)

        # Extract potential codes
        codes = _extract_codes_from_message(full_text)
        if not codes:
            return

        log.info(f"Gift code(s) detected from channel watcher: {codes}")
        for code in codes:
            await self.auto_redeem_code(code, message.guild)

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

        session = await self._get_session()
        result = await api_get_player(session, player_id)

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
            value="Gift codes from the official channel will be **automatically redeemed** for you! You can also use `/redeemcode` to manually redeem codes.",
            inline=False,
        )
        embed.set_footer(text="Use /unregister to remove your link")
        await ctx.send(embed=embed, ephemeral=True)

        # Catch-up: redeem all active gift codes for this new player
        asyncio.create_task(self.catch_up_redeem(player_id, ctx.author.display_name, ctx.guild))

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
    # /submitcode — Manual redeem for all (Leader only, backup to auto-watcher)
    # -----------------------------------------------------------------------
    @commands.hybrid_command(name="submitcode")
    @app_commands.describe(code="The gift code to redeem for all registered players")
    @commands.has_any_role(*LEADER_ROLES)
    @cooldown(60)
    async def submit_code(self, ctx: commands.Context, code: str):
        """Manually redeem a gift code for ALL registered members (backup if auto-detect misses one)."""
        code = code.strip().upper()

        if code in [c["code"] for c in code_history["codes"]]:
            await ctx.send(f"⚠️ Code `{code}` has already been processed.", ephemeral=True)
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
            await asyncio.sleep(RATE_LIMIT_DELAY)

            if processed % 10 == 0:
                embed.description = f"Processing... **{processed}/{len(players)}** done"
                try:
                    await status_msg.edit(embed=embed)
                except Exception:
                    pass

        embed = discord.Embed(title=f"🎁 Code `{code}` — Results", color=discord.Color.green() if results["success"] > 0 else discord.Color.red())
        embed.add_field(name="✅ Redeemed", value=str(results["success"]), inline=True)
        embed.add_field(name="📦 Already Claimed", value=str(results["already_claimed"]), inline=True)
        embed.add_field(name="❌ Failed", value=str(results["failed"]), inline=True)
        if results["errors"]:
            embed.add_field(name="⚠️ Notes", value="\n".join(results["errors"][:10]), inline=False)
        embed.set_footer(text=f"Submitted by {ctx.author.display_name}")
        await status_msg.edit(embed=embed)

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
            source = "🤖" if c.get("submitted_by") == "auto_watcher" else "👤"
            lines.append(
                f"{source} `{c['code']}` — ✅{res.get('success', 0)} 📦{res.get('already_claimed', 0)} ❌{res.get('failed', 0)} — <t:{int(ts.timestamp())}:R>"
            )

        embed = discord.Embed(title="📋 Gift Code History", description="\n".join(lines), color=discord.Color.blue())
        embed.add_field(name="Legend", value="🤖 = Auto-detected from official channel\n👤 = Manually submitted", inline=False)
        embed.set_footer(text=f"{len(codes)} codes processed total")
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

        embed = discord.Embed(title="🔍 Player Lookup", color=discord.Color.blue())
        embed.add_field(name="FID", value=player_id, inline=True)
        embed.add_field(name="Nickname", value=pdata.get("nickname", "?"), inline=True)
        furnace = pdata.get("stove_lv", pdata.get("furnace_lv", "?"))
        embed.add_field(name="Furnace Level", value=str(furnace), inline=True)
        if pdata.get("kid"):
            embed.add_field(name="Kingdom", value=str(pdata["kid"]), inline=True)
        if pdata.get("avatar_image"):
            embed.set_thumbnail(url=pdata["avatar_image"])
        await ctx.send(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # Background task: process queued auto-redeem codes
    # -----------------------------------------------------------------------
    @tasks.loop(minutes=5)
    async def auto_redeem_check(self):
        """Process any queued auto-redeem codes."""
        pending = auto_redeem_codes.get("pending", [])
        if not pending:
            return

        session = await self._get_session()
        code_entry = pending[0]
        code = code_entry["code"]
        remaining_fids = code_entry.get("remaining_fids", [])

        if not remaining_fids:
            auto_redeem_codes["completed"].append(code_entry)
            auto_redeem_codes["pending"].pop(0)
            save_data("auto_redeem_codes", auto_redeem_codes)
            return

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

    # -----------------------------------------------------------------------
    # WIKI SCRAPER — Monitor kingshotwiki.com for gift codes & event updates
    # -----------------------------------------------------------------------
    @tasks.loop(hours=24)
    async def wiki_code_scraper(self):
        """Daily scrape of kingshotwiki.com for new gift codes and event updates."""
        cfg = load_config()
        guild_id = cfg.get("guild_id")
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None

        # --- Phase 1: Gift Code Scraping ---
        await self._scrape_gift_codes(guild)

        # --- Phase 2: Event Info Scraping ---
        await self._scrape_event_updates(guild)

    async def _fetch_wiki_page(self, url: str) -> str | None:
        """Fetch a wiki page using aiohttp with browser-like headers."""
        try:
            session = await self._get_session()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30), allow_redirects=True) as resp:
                if resp.status != 200:
                    log.warning(f"Wiki scraper: HTTP {resp.status} for {url}")
                    return None
                return await resp.text()
        except Exception as e:
            log.warning(f"Wiki scraper: fetch error for {url}: {e}")
            return None

    async def _scrape_gift_codes(self, guild: discord.Guild | None):
        """Scrape wiki for new gift codes and auto-redeem them."""
        try:
            html = await self._fetch_wiki_page(WIKI_CODES_URL)
            if not html:
                return

            # Extract gift codes from the page
            found_codes = set()
            for m in WIKI_CODE_PATTERN.finditer(html):
                candidate = m.group(1).upper()
                if candidate.lower() not in CODE_EXCLUDE and len(candidate) >= 4:
                    found_codes.add(candidate)

            # Also look for inline code blocks with gift code patterns
            inline_codes = re.findall(r'<code>([A-Za-z0-9]{6,30})</code>', html)
            for c in inline_codes:
                cu = c.upper()
                if cu.lower() not in CODE_EXCLUDE and _is_valid_candidate(c):
                    found_codes.add(cu)

            if not found_codes:
                log.debug("Wiki scraper: no codes found on page")
                return

            # Check which codes are new
            known_codes = {c["code"].upper() for c in gift_codes.get("codes", [])}
            history_codes = {c["code"].upper() for c in code_history.get("codes", [])}
            new_codes = found_codes - known_codes - history_codes

            if not new_codes:
                log.debug(f"Wiki scraper: {len(found_codes)} codes found, all already known")
                return

            log.info(f"Wiki scraper: found {len(new_codes)} new code(s): {new_codes}")

            # Extract expiry dates
            expiry_date = None
            for m in WIKI_EXPIRY_PATTERN.finditer(html):
                try:
                    date_str = m.group(1).strip()
                    for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
                        try:
                            expiry_date = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            for code in new_codes:
                code_entry = {
                    "code": code,
                    "source": "wiki_scraper",
                    "discovered_at": utc_now().isoformat(),
                    "expired": False,
                }
                if expiry_date:
                    code_entry["expires"] = expiry_date
                gift_codes.setdefault("codes", []).append(code_entry)
                save_data("gift_codes", gift_codes)

                # Notify in gift-codes channel
                if guild:
                    report_ch = discord.utils.get(guild.text_channels, name="gift-codes")
                    if report_ch:
                        embed = discord.Embed(
                            title=f"🔍 New Gift Code Found: `{code}`",
                            description="Discovered from the official Kingshot Wiki!\nAuto-redeeming for all registered players...",
                            color=discord.Color.gold(),
                        )
                        embed.set_footer(text="Source: kingshotwiki.com")
                        if code_entry.get("expires"):
                            embed.add_field(name="⏰ Expires", value=code_entry["expires"], inline=True)
                        try:
                            await report_ch.send(embed=embed)
                        except Exception as e:
                            log.error(f"Failed to send wiki gift code embed: {e}")

                # Trigger auto-redeem
                if guild:
                    await self.auto_redeem_code(code, guild)

        except Exception as e:
            log.error(f"Wiki gift code scraper error: {e}", exc_info=True)

    async def _scrape_event_updates(self, guild: discord.Guild | None):
        """Scrape wiki for event schedule updates (dates, durations, new events)."""
        try:
            # Scrape the sneak-peek page for event info
            html = await self._fetch_wiki_page(WIKI_CODES_URL)
            if not html:
                return

            # Also try to find linked pages from the sneak-peek page
            page_links = re.findall(r'href="(/sneak-peek/[^"]+)"', html)
            all_html = [html]
            for link in page_links[:5]:  # Limit to 5 sub-pages
                sub_url = f"{WIKI_BASE_URL}{link}"
                sub_html = await self._fetch_wiki_page(sub_url)
                if sub_html:
                    all_html.append(sub_html)
                await asyncio.sleep(2)  # Be polite to the server

            combined_html = "\n".join(all_html)

            # Extract event information
            updates = []

            # Look for event date ranges
            date_formats = ["%B %d", "%b %d", "%B %d, %Y", "%b %d, %Y"]

            # Extract event names, dates, and durations from structured content
            # Pattern: "Event Name: March 17 – March 23, 2026" or similar
            for m in WIKI_EVENT_DATE_PATTERN.finditer(combined_html):
                event_name = m.group(1).strip()
                # Skip if it's just HTML tag content
                if '<' in event_name or len(event_name) < 3 or len(event_name) > 60:
                    continue
                try:
                    if m.group(2) and m.group(3) and m.group(4):
                        year = m.group(4).strip()
                        start_str = f"{m.group(2).strip()}, {year}"
                        end_str = f"{m.group(3).strip()}, {year}"
                    elif m.group(5) and m.group(6):
                        start_str = m.group(5).strip()
                        end_str = m.group(6).strip()
                    else:
                        continue

                    start_date = end_date = None
                    for fmt in date_formats:
                        try:
                            start_date = datetime.strptime(start_str, fmt)
                            if start_date.year == 1900:
                                start_date = start_date.replace(year=datetime.now().year)
                            break
                        except ValueError:
                            continue
                    for fmt in date_formats:
                        try:
                            end_date = datetime.strptime(end_str, fmt)
                            if end_date.year == 1900:
                                end_date = end_date.replace(year=datetime.now().year)
                            break
                        except ValueError:
                            continue

                    if start_date and end_date:
                        updates.append({
                            "name": event_name,
                            "date_start": start_date.strftime("%Y-%m-%d"),
                            "date_end": end_date.strftime("%Y-%m-%d"),
                            "duration_days": (end_date - start_date).days + 1,
                        })
                except Exception:
                    continue

            # Extract duration updates
            for m in WIKI_DURATION_PATTERN.finditer(combined_html):
                try:
                    duration = int(m.group(1))
                    # Find nearby event name (look backwards in the text)
                    pos = m.start()
                    preceding = combined_html[max(0, pos - 200):pos]
                    # Look for the last heading or bold text
                    name_match = re.search(r'(?:<h\d[^>]*>|<strong>|<b>)\s*([^<]{3,60})', preceding)
                    if name_match:
                        updates.append({
                            "name": name_match.group(1).strip(),
                            "duration_days": duration,
                        })
                except Exception:
                    continue

            if not updates:
                log.debug("Wiki scraper: no event updates found")
                return

            # Compare with current event_cycle.json and report changes
            cycle = load_event_cycle()
            existing_events = {ev["name"].lower().strip(): ev for ev in cycle.get("events", [])}
            changes_found = []

            for update in updates:
                # Sanitize scraped content before using in embeds
                update["name"] = sanitize_html(update["name"], max_length=100)
                name_lower = update["name"].lower().strip()
                # Try fuzzy matching against existing events
                matched_event = existing_events.get(name_lower)
                if not matched_event:
                    # Try partial matching
                    for ename, ev in existing_events.items():
                        if name_lower in ename or ename in name_lower:
                            matched_event = ev
                            break

                if matched_event:
                    # Check for date changes
                    if "date_start" in update and matched_event.get("date_start") != update["date_start"]:
                        changes_found.append(
                            f"📅 **{matched_event['name']}**: dates changed → "
                            f"{update['date_start']} to {update.get('date_end', '?')}"
                        )
                    if "duration_days" in update and matched_event.get("duration_days") != update["duration_days"]:
                        changes_found.append(
                            f"⏱️ **{matched_event['name']}**: duration → "
                            f"{matched_event.get('duration_days', '?')}d → {update['duration_days']}d"
                        )
                else:
                    # Potentially a new event
                    if update.get("date_start"):
                        changes_found.append(
                            f"🆕 **{update['name']}**: {update.get('date_start', '?')} – "
                            f"{update.get('date_end', '?')} ({update.get('duration_days', '?')} days)"
                        )

            if changes_found:
                log.info(f"Wiki scraper: found {len(changes_found)} event update(s)")

                # Notify in announcements channel
                if guild:
                    report_ch = discord.utils.get(guild.text_channels, name="announcements")
                    if report_ch:
                        embed = discord.Embed(
                            title="📰 Wiki Event Updates Detected",
                            description="New event information found on the official wiki:\n\n"
                                       + "\n".join(changes_found),
                            color=discord.Color.blue(),
                        )
                        embed.set_footer(text="Source: kingshotwiki.com • Review & update with /event commands")
                        try:
                            await report_ch.send(embed=embed)
                        except Exception as e:
                            log.error(f"Failed to send wiki event update embed: {e}")

                # Save the raw scrape data for review
                scrape_log = load_data("wiki_scrape_log", {"scrapes": []})
                scrape_log["scrapes"].append({
                    "timestamp": utc_now().isoformat(),
                    "updates": updates,
                    "changes": changes_found,
                })
                # Keep only last 30 scrape entries
                scrape_log["scrapes"] = scrape_log["scrapes"][-30:]
                save_data("wiki_scrape_log", scrape_log)

        except Exception as e:
            log.error(f"Wiki event scraper error: {e}", exc_info=True)

    @wiki_code_scraper.before_loop
    async def before_wiki_scraper(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(GameAPI(bot))
