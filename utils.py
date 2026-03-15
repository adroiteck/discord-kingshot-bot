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

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def load_data(name, default=None):
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
def utc_now():
    return datetime.now(timezone.utc)

def utc_from_iso(iso_str: str) -> datetime:
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def format_delta(td: timedelta) -> str:
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
# Power parser (handles k/m/b suffixes)
# ---------------------------------------------------------------------------
def parse_power(power_str: str) -> Optional[int]:
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
    tz_lower = tz_input.strip().lower().replace(" ", "_")
    if tz_lower in TZ_ALIASES:
        return TZ_ALIASES[tz_lower]
    offset_match = re.match(r"^(?:utc|gmt)\s*([+-])?\s*(\d{1,2})(?::(\d{2}))?$", tz_lower)
    if offset_match:
        sign = offset_match.group(1) or "+"
        hours = int(offset_match.group(2))
        mins = int(offset_match.group(3) or 0)
        total_offset = hours * 60 + mins
        if sign == "-": total_offset = -total_offset
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
def load_event_cycle():
    return load_json_file(EVENT_CYCLE_PATH, {"cycle_anchor": "2026-03-06", "cycle_length_days": 28, "events": []})

def get_cycle_day(dt=None):
    cycle = load_event_cycle()
    anchor = datetime.strptime(cycle["cycle_anchor"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if dt is None: dt = utc_now()
    return (dt - anchor).days % cycle.get("cycle_length_days", 28)

def get_active_events(dt=None):
    cycle = load_event_cycle()
    cycle_len = cycle.get("cycle_length_days", 28)
    day = get_cycle_day(dt)
    active = []
    for ev in cycle.get("events", []):
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

def get_upcoming_events(days_ahead=7, dt=None):
    cycle = load_event_cycle()
    cycle_len = cycle.get("cycle_length_days", 28)
    if dt is None: dt = utc_now()
    today = get_cycle_day(dt)
    upcoming = []
    for ev in cycle.get("events", []):
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
    parts = cron_str.split()
    if len(parts) != 5: return False
    fields = [(parts[0], dt.minute), (parts[1], dt.hour), (parts[2], dt.day), (parts[3], dt.month), (parts[4], dt.isoweekday() % 7)]
    for pattern, value in fields:
        if pattern == "*": continue
        allowed = set()
        for segment in pattern.split(","):
            if "/" in segment:
                base, step = segment.split("/"); step = int(step)
                start = 0 if base == "*" else int(base)
                allowed.update(range(start, 60, step))
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
        self.embeds = embeds; self.current_page = 0

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
        @wraps(func)
        async def wrapper(self_or_ctx, *args, **kwargs):
            ctx = self_or_ctx if isinstance(self_or_ctx, commands.Context) else args[0] if args else kwargs.get("ctx")
            if ctx is None:
                return await func(self_or_ctx, *args, **kwargs)
            uid = ctx.author.id
            now = time.time()
            last = _cooldowns[func.__name__].get(uid, 0)
            if now - last < seconds:
                remaining = int(seconds - (now - last))
                await ctx.send(f"⏳ Cooldown — try again in **{remaining}s**", ephemeral=True)
                return
            _cooldowns[func.__name__][uid] = now
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
