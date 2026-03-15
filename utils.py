"""
Shared utilities for Kingshot Bot — data I/O, views, helpers.
"""
import discord
from discord.ui import View, Button, Select, button, select
from discord.ext import commands
from discord import app_commands
from typing import List, Optional
import json, asyncio, logging, os, time, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones
from functools import wraps
from collections import defaultdict

log = logging.getLogger("kingshot-bot")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"
DATA_PATH = BASE / "data"
DATA_PATH.mkdir(exist_ok=True)
EVENT_CYCLE_PATH = BASE / "event_cycle.json"

# ---------------------------------------------------------------------------
# Data I/O with retry + async safety
# ---------------------------------------------------------------------------
_save_retry_counts: dict[str, int] = defaultdict(int)
_bot_health = {"last_save_ok": None, "last_save_fail": None, "error_count": 0, "start_time": time.time()}

def get_bot_health():
    return _bot_health

def load_config() -> dict:
    """Load the bot configuration from config.json."""
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(cfg: dict) -> None:
    """Persist the bot configuration to config.json."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def load_data(name: str, default=None):
    path = DATA_PATH / f"{name}.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.error(f"Failed to load {name}: {e}")
    return default if default is not None else {}

def _save_data_sync(name, data, retries=3):
    """Synchronous save with retries — runs in thread executor."""
    path = DATA_PATH / f"{name}.json"
    tmp_path = DATA_PATH / f"{name}.json.tmp"
    for attempt in range(retries):
        try:
            content = json.dumps(data, indent=2)
            with open(tmp_path, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp_path), str(path))
            _bot_health["last_save_ok"] = time.time()
            _save_retry_counts[name] = 0
            return
        except Exception as e:
            _bot_health["error_count"] += 1
            _bot_health["last_save_fail"] = time.time()
            log.error(f"Failed to save {name} (attempt {attempt+1}/{retries}): {e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))  # Exponential backoff
    _save_retry_counts[name] += 1
    log.critical(f"All {retries} save attempts failed for {name}")

def save_data(name, data):
    """Save data non-blocking with retry logic."""
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _save_data_sync, name, data)
    except RuntimeError:
        _save_data_sync(name, data)

def load_json_file(path: Path, default=None):
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Failed to load {path}: {e}")
    return default if default is not None else {}

# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)

def utc_from_iso(iso_str: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def format_delta(td: timedelta) -> str:
    """Format a timedelta as a human-readable string like '2d 5h 30m'."""
    total = int(td.total_seconds())
    if total < 0:
        return "Expired"
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)

# ---------------------------------------------------------------------------
# HTML sanitization (for wiki scraper output)
# ---------------------------------------------------------------------------
_HTML_TAG_RE = re.compile(r"<[^>]+>")

def sanitize_html(text: str, max_length: int = 1024) -> str:
    """Strip HTML tags and limit length for safe embed display."""
    clean = _HTML_TAG_RE.sub("", text)
    clean = clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return clean[:max_length].strip()

# ---------------------------------------------------------------------------
# Moderation audit logging
# ---------------------------------------------------------------------------
def log_mod_action(action: str, moderator: str, target: str, reason: str = "", **extra):
    """Log a moderation action to mod_log.json for audit trail."""
    mod_log = load_data("mod_log", {"actions": []})
    entry = {
        "action": action,
        "moderator": moderator,
        "target": target,
        "reason": reason,
        "timestamp": utc_now().isoformat(),
        **extra,
    }
    mod_log["actions"].append(entry)
    # Keep last 500 entries
    if len(mod_log["actions"]) > 500:
        mod_log["actions"] = mod_log["actions"][-500:]
    save_data("mod_log", mod_log)
    log.info(f"MOD ACTION: {action} | by {moderator} | target {target} | reason: {reason}")

# ---------------------------------------------------------------------------
# Power parser (handles k/m/b suffixes)
# ---------------------------------------------------------------------------
def parse_power(power_str: str) -> Optional[int]:
    """Parse a human-readable power string (e.g. '25m', '1.2b') into an integer."""
    power = power_str.lower().replace(",", "").strip()
    multiplier = 1
    if power.endswith("k"):
        multiplier = 1_000; power = power[:-1]
    elif power.endswith("m"):
        multiplier = 1_000_000; power = power[:-1]
    elif power.endswith("b"):
        multiplier = 1_000_000_000; power = power[:-1]
    try:
        return int(float(power) * multiplier)
    except ValueError:
        return None

# ---------------------------------------------------------------------------
# Timezone resolution
# ---------------------------------------------------------------------------
TZ_ALIASES = {
    "est": "America/New_York", "edt": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago",
    "mst": "America/Denver", "mdt": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "gmt": "Europe/London", "bst": "Europe/London",
    "cet": "Europe/Berlin", "cest": "Europe/Berlin",
    "eet": "Europe/Bucharest", "eest": "Europe/Bucharest",
    "ist": "Asia/Kolkata", "jst": "Asia/Tokyo", "kst": "Asia/Seoul",
    "cst_asia": "Asia/Shanghai", "cst-asia": "Asia/Shanghai",
    "aest": "Australia/Sydney", "aedt": "Australia/Sydney",
    "nzst": "Pacific/Auckland", "nzdt": "Pacific/Auckland",
    "brt": "America/Sao_Paulo", "brst": "America/Sao_Paulo",
    "msk": "Europe/Moscow", "sgt": "Asia/Singapore",
    "hkt": "Asia/Hong_Kong", "pht": "Asia/Manila",
    "wib": "Asia/Jakarta", "gulf": "Asia/Dubai", "gst": "Asia/Dubai",
    "ast": "Asia/Riyadh", "cat": "Africa/Johannesburg", "sast": "Africa/Johannesburg",
}

def resolve_timezone(tz_input: str) -> Optional[str]:
    """Resolve a user-supplied timezone string to an IANA timezone name, or None if invalid."""
    tz_lower = tz_input.strip().lower().replace(" ", "_")
    if tz_lower in TZ_ALIASES:
        return TZ_ALIASES[tz_lower]
    offset_match = re.match(r"^(?:utc|gmt)\s*([+-])?\s*(\d{1,2})(?::(\d{2}))?$", tz_lower)
    if offset_match:
        sign = offset_match.group(1) or "+"
        hours = int(offset_match.group(2))
        mins = int(offset_match.group(3) or 0)
        # Validate offset range: UTC-12 to UTC+14
        if hours > 14 or (hours == 14 and mins > 0) or mins >= 60:
            return None
        total_offset = hours * 60 + mins
        if sign == "-": total_offset = -total_offset
        if total_offset < -720:  # UTC-12
            return None
        if mins == 0:
            etc_name = f"Etc/GMT{'+' if total_offset <= 0 else '-'}{abs(hours)}"
            if etc_name in available_timezones():
                return etc_name
        return None
    all_tzs = available_timezones()
    for tz in all_tzs:
        if tz.lower() == tz_lower:
            return tz
    matches = [tz for tz in all_tzs if tz_lower in tz.lower()]
    if len(matches) == 1:
        return matches[0]
    return None

# ---------------------------------------------------------------------------
# Event Cycle helpers
# ---------------------------------------------------------------------------
def load_event_cycle() -> dict:
    """Load the event cycle definition from event_cycle.json."""
    return load_json_file(EVENT_CYCLE_PATH, {"cycle_anchor": "2026-03-06", "cycle_length_days": 28, "events": []})

def get_cycle_day(dt=None) -> int:
    """Return the current day (0-indexed) within the 28-day event cycle."""
    cycle = load_event_cycle()
    anchor = datetime.strptime(cycle["cycle_anchor"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if dt is None: dt = utc_now()
    return (dt - anchor).days % cycle.get("cycle_length_days", 28)

def get_active_events(dt=None) -> list:
    """Return a list of event dicts that are active right now (or at the given datetime)."""
    cycle = load_event_cycle()
    cycle_len = cycle.get("cycle_length_days", 28)
    day = get_cycle_day(dt)
    if dt is None: dt = utc_now()
    active = []
    for ev in cycle.get("events", []):
        # Special (date-anchored) events — check real calendar dates
        if ev.get("special") and ev.get("date_start") and ev.get("date_end"):
            ev_start = datetime.strptime(ev["date_start"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            ev_end = datetime.strptime(ev["date_end"], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            if ev_start <= dt < ev_end:
                active.append(ev)
            continue
        # Regular cycle-based events
        start = ev["cycle_day_start"]
        duration = ev.get("duration_days", 1)
        recurring = ev.get("recurring_every_days")
        if recurring:
            for offset in range(0, cycle_len, recurring):
                ev_start = (start + offset) % cycle_len
                if ev_start <= day < ev_start + duration:
                    active.append(ev); break
        else:
            end = start + duration
            if end <= cycle_len:
                if start <= day < end: active.append(ev)
            else:
                if day >= start or day < (end % cycle_len): active.append(ev)
    return active

def get_upcoming_events(days_ahead: int = 7, dt=None) -> list:
    """Return upcoming events as (days_until, start_date, event_dict) tuples, sorted by proximity."""
    cycle = load_event_cycle()
    cycle_len = cycle.get("cycle_length_days", 28)
    if dt is None: dt = utc_now()
    today = get_cycle_day(dt)
    upcoming = []
    for ev in cycle.get("events", []):
        # Special (date-anchored) events — check real calendar dates
        if ev.get("special") and ev.get("date_start"):
            ev_start = datetime.strptime(ev["date_start"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_until = (ev_start - dt).days
            if days_until == 0:
                upcoming.append((0, dt, ev))
            elif 0 < days_until <= days_ahead:
                upcoming.append((days_until, dt + timedelta(days=days_until), ev))
            continue
        # Regular cycle-based events
        start = ev["cycle_day_start"]
        recurring = ev.get("recurring_every_days")
        if recurring:
            for offset in range(0, cycle_len, recurring):
                ev_start = (start + offset) % cycle_len
                days_until = (ev_start - today) % cycle_len
                if 0 < days_until <= days_ahead:
                    upcoming.append((days_until, dt + timedelta(days=days_until), ev))
                if ev_start == today:
                    upcoming.append((0, dt, ev))
        else:
            days_until = (start - today) % cycle_len
            if days_until == 0:
                upcoming.append((0, dt, ev))
            elif days_until <= days_ahead:
                upcoming.append((days_until, dt + timedelta(days=days_until), ev))
    upcoming.sort(key=lambda x: x[0])
    seen = set(); deduped = []
    for item in upcoming:
        if item[2]["name"] not in seen:
            seen.add(item[2]["name"]); deduped.append(item)
    return deduped

# ---------------------------------------------------------------------------
# Cron matcher
# ---------------------------------------------------------------------------
def cron_matches(cron_str: str, dt: datetime) -> bool:
    """Check whether a 5-field cron expression matches the given datetime."""
    parts = cron_str.split()
    if len(parts) != 5: return False
    # (pattern, value, field_max) — max is exclusive upper bound for step ranges
    fields = [
        (parts[0], dt.minute, 60),     # minute: 0-59
        (parts[1], dt.hour, 24),        # hour: 0-23
        (parts[2], dt.day, 32),         # day: 1-31
        (parts[3], dt.month, 13),       # month: 1-12
        (parts[4], dt.isoweekday() % 7, 7),  # weekday: 0-6
    ]
    for pattern, value, field_max in fields:
        if pattern == "*": continue
        allowed = set()
        for segment in pattern.split(","):
            if "/" in segment:
                base, step = segment.split("/"); step = int(step)
                start = 0 if base == "*" else int(base)
                allowed.update(range(start, field_max, step))
            elif "-" in segment:
                lo, hi = segment.split("-"); allowed.update(range(int(lo), int(hi) + 1))
            else:
                allowed.add(int(segment))
        if value not in allowed: return False
    return True

# ---------------------------------------------------------------------------
# Hero / Formation data loaders (from JSON config)
# ---------------------------------------------------------------------------
def load_hero_db():
    return load_json_file(DATA_PATH / "heroes.json", {})

def load_formations():
    return load_json_file(DATA_PATH / "formations.json", {})

# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------
ROLE_COLORS = {
    "R5 | Alliance Leader": discord.Color.gold(),
    "R4 | Leadership": discord.Color.from_str("#FF4500"),
    "R3 | TC25+": discord.Color.purple(),
    "R2 | TC24-": discord.Color.blue(),
    "R1 | Bear Bait": discord.Color.light_grey(),
}
LEADER_ROLES = ["R5 | Alliance Leader", "R4 | Leadership"]

# ---------------------------------------------------------------------------
# Shared UI Views
# ---------------------------------------------------------------------------
class ConfirmView(View):
    def __init__(self):
        super().__init__(); self.confirmed = False

    @button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, btn: Button):
        self.confirmed = True; await interaction.response.defer(); self.stop()

    @button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, btn: Button):
        self.confirmed = False; await interaction.response.defer(); self.stop()


class PaginatorView(View):
    def __init__(self, embeds: List[discord.Embed], timeout=180):
        super().__init__(timeout=timeout)
        self.embeds = embeds if embeds else [discord.Embed(description="No data available.")]
        self.current_page = 0

    @button(label="◀️", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, btn: Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
        else:
            await interaction.response.defer()

    @button(label="▶️", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, btn: Button):
        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
        else:
            await interaction.response.defer()


class RolePanelView(View):
    """Persistent role selection — factory pattern to avoid duplicate handlers."""
    def __init__(self):
        super().__init__(timeout=None)
        for role_name, cid in [
            ("R5 | Alliance Leader", "role_r5"), ("R4 | Leadership", "role_r4"),
            ("R3 | TC25+", "role_r3"), ("R2 | TC24-", "role_r2"), ("R1 | Bear Bait", "role_r1"),
        ]:
            btn = Button(label=role_name, style=discord.ButtonStyle.primary, custom_id=cid)
            btn.callback = self._make_callback(role_name)
            self.add_item(btn)

    def _make_callback(self, role_name: str):
        async def callback(interaction: discord.Interaction):
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if not role:
                await interaction.response.send_message("❌ Role not found", ephemeral=True); return
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message(f"❌ Removed **{role_name}**", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ Added **{role_name}**", ephemeral=True)
        return callback


# ---------------------------------------------------------------------------
# Cooldown check decorator for hybrid commands
# ---------------------------------------------------------------------------
_cooldowns: dict[str, dict[int, float]] = defaultdict(dict)

def cooldown(seconds: int):
    """Simple per-user cooldown decorator for hybrid commands."""
    def decorator(func):
        # Use module-qualified name to prevent cross-cog collisions
        cooldown_key = f"{func.__module__}.{func.__qualname__}"
        @wraps(func)
        async def wrapper(self_or_ctx, *args, **kwargs):
            ctx = self_or_ctx if isinstance(self_or_ctx, commands.Context) else args[0] if args else kwargs.get("ctx")
            if ctx is None:
                return await func(self_or_ctx, *args, **kwargs)
            uid = ctx.author.id
            now = time.time()
            last = _cooldowns[cooldown_key].get(uid, 0)
            if now - last < seconds:
                remaining = int(seconds - (now - last))
                await ctx.send(f"⏳ Cooldown — try again in **{remaining}s**", ephemeral=True)
                return
            _cooldowns[cooldown_key][uid] = now
            return await func(self_or_ctx, *args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Autocomplete helpers
# ---------------------------------------------------------------------------
async def event_name_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    from cogs.events import EVENT_GUIDES
    choices = []
    for key, ev in EVENT_GUIDES.items():
        if current.lower() in key or current.lower() in ev["name"].lower():
            choices.append(app_commands.Choice(name=f"{ev['emoji']} {ev['name']}", value=key))
    return choices[:25]

async def hero_name_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    heroes = load_hero_db()
    choices = [app_commands.Choice(name=h.title(), value=h) for h in heroes if current.lower() in h]
    return choices[:25]

async def formation_event_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    formations = load_formations()
    choices = [app_commands.Choice(name=f.get("name", k), value=k) for k, f in formations.items() if current.lower() in k or current.lower() in f.get("name", "").lower()]
    return choices[:25]

async def timer_name_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    timers = load_data("war_timers", {"timers": []})
    active = [t for t in timers.get("timers", []) if utc_from_iso(t["time"]) > utc_now()]
    return [app_commands.Choice(name=t["name"], value=t["name"]) for t in active if current.lower() in t["name"].lower()][:25]

async def timezone_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    common = [("EST (US Eastern)", "EST"), ("CST (US Central)", "CST"), ("MST (US Mountain)", "MST"),
              ("PST (US Pacific)", "PST"), ("GMT (London)", "GMT"), ("CET (Central Europe)", "CET"),
              ("IST (India)", "IST"), ("JST (Japan)", "JST"), ("KST (Korea)", "KST"),
              ("AEST (Australia)", "AEST"), ("NZST (New Zealand)", "NZST")]
    if not current:
        return [app_commands.Choice(name=n, value=v) for n, v in common[:25]]
    choices = [app_commands.Choice(name=n, value=v) for n, v in common if current.lower() in n.lower() or current.lower() in v.lower()]
    if len(choices) < 10 and len(current) >= 3:
        for tz in sorted(available_timezones()):
            if current.lower() in tz.lower():
                city = tz.split("/")[-1].replace("_", " ")
                choices.append(app_commands.Choice(name=f"{city} ({tz})", value=tz))
                if len(choices) >= 25: break
    return choices[:25]

async def announcement_name_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    cfg = load_config()
    return [app_commands.Choice(name=f"{a['name']} [{'ON' if a.get('enabled') else 'OFF'}]", value=a["name"])
            for a in cfg.get("scheduled_announcements", []) if current.lower() in a["name"].lower()][:25]
