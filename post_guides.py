#!/usr/bin/env python3
"""
Post comprehensive bot guides to #bot-guide channel.

Usage:
    python post_guides.py              # Post all guides (clears channel first)
    python post_guides.py --no-clear   # Post without clearing
    python post_guides.py --dry-run    # Preview embeds without posting

Requires BOT_TOKEN env var or bot_token in config.json.
"""

import os
import sys
import json
import time
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH) as f:
    config = json.load(f)

TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or config.get("bot_token", "")
GUILD_ID = config.get("guild_id", "1461477485911871542")
BOT_GUIDE_CHANNEL_ID = None  # Resolved at runtime

HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
}
API = "https://discord.com/api/v10"
RATE_DELAY = 1.0  # seconds between API calls


def get_channel_id(name="bot-guide"):
    """Find channel ID by name."""
    r = requests.get(f"{API}/guilds/{GUILD_ID}/channels", headers=HEADERS)
    r.raise_for_status()
    for ch in r.json():
        if ch["name"] == name and ch["type"] == 0:  # text channel
            return ch["id"]
    return None


def clear_channel(channel_id, limit=50):
    """Delete all bot messages in the channel."""
    r = requests.get(f"{API}/channels/{channel_id}/messages?limit={limit}", headers=HEADERS)
    r.raise_for_status()
    messages = r.json()
    deleted = 0
    for msg in messages:
        requests.delete(f"{API}/channels/{channel_id}/messages/{msg['id']}", headers=HEADERS)
        deleted += 1
        time.sleep(RATE_DELAY)
    print(f"  Cleared {deleted} messages from #bot-guide")


def post_embed(channel_id, embed, dry_run=False):
    """Post a single embed to a channel."""
    if dry_run:
        print(f"  [DRY RUN] Would post: {embed.get('title', 'Untitled')}")
        return
    payload = {"embeds": [embed]}
    r = requests.post(f"{API}/channels/{channel_id}/messages", headers=HEADERS, json=payload)
    r.raise_for_status()
    print(f"  Posted: {embed.get('title', 'Untitled')}")
    time.sleep(RATE_DELAY)


# ---------------------------------------------------------------------------
# Guide Embeds — v3.1.0
# ---------------------------------------------------------------------------

def build_guides():
    """Build all guide embeds."""
    guides = []

    # --- 1. Welcome Header ---
    guides.append({
        "title": "🤖 Kingshot Bot — Complete Guide v3.1",
        "description": (
            "Welcome to the **Kingshot Bot** command guide! This bot helps manage our alliance "
            "with event guides, power tracking, gift codes, war coordination, and more.\n\n"
            "**How to use:** All commands start with `/` — just type `/` in any channel "
            "to see available commands.\n\n"
            "🔒 = Requires leadership role (R4+)\n"
            "🛡️ = Requires admin permissions\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "color": 0x5865F2,  # blurple
    })

    # --- 2. Getting Started ---
    guides.append({
        "title": "🚀 Getting Started",
        "description": (
            "New to the server? Set up your profile in 60 seconds:\n\n"
            "**Step 1** — Set your in-game name:\n"
            "`/setign YourIGN`\n"
            "This also updates your server nickname automatically.\n\n"
            "**Step 2** — Set your power level:\n"
            "`/setpower 25m` (accepts m/b shorthand)\n\n"
            "**Step 3** — Link your game account:\n"
            "`/register`\n"
            "Enables auto gift code redemption for your account!\n\n"
            "**Step 4** — Set your timezone:\n"
            "`/timezone EST` (or PST, UTC+5, Europe/London, etc.)\n"
            "Event timers will show in your local time.\n\n"
            "**Step 5** — Enter your stats:\n"
            "`/mystats`\n"
            "Track your troops, research, and more.\n\n"
            "**Optional:**\n"
            "`/remindme` — Get DM alerts 1 hour before events\n"
            "`/setlang es` — Set preferred language for translations"
        ),
        "color": 0x2ecc71,  # green
    })

    # --- 3. Profile & Info ---
    guides.append({
        "title": "👤 Profile & Info Commands",
        "description": (
            "**`/profile`** — View your profile (IGN, power, roles, join date)\n"
            "`/profile @member` — View someone else's profile\n\n"
            "**`/setpower 25m`** — Update your power level\n"
            "Accepts shorthand: `25m`, `1.2b`, `5000000`\n\n"
            "**`/setign YourName`** — Set your in-game name\n"
            "Also updates your server nickname. Duplicate IGNs are blocked.\n\n"
            "**`/powerhistory`** — View your power growth over time\n"
            "Shows trends, daily/weekly/monthly rates, and 30/90 day projections.\n"
            "`/powerhistory @member` — View someone else's history\n\n"
            "**`/leaderboard`** — Alliance power rankings (paginated)\n\n"
            "**`/memberinfo`** — Detailed member info\n\n"
            "**`/serverinfo`** — Server stats and total alliance power"
        ),
        "color": 0x3498db,  # blue
    })

    # --- 4. Event Guides ---
    guides.append({
        "title": "📚 Event Guides & Schedule",
        "description": (
            "**`/events`** — Browse all available event guides\n\n"
            "**`/event <name>`** — Get a specific event guide\n"
            "Examples: `/event bear`, `/event kvk`, `/event strongest_governor`\n\n"
            "**`/today`** — See what events are active right now\n\n"
            "**`/nextevent`** — When is the next major event?\n\n"
            "**`/schedule`** — View the weekly event schedule\n\n"
            "**`/countdown`** — Countdown timers to upcoming events\n\n"
            "**`/heroes`** — Hero tier lists and build guides\n\n"
            "**`/troops`** — Troop composition recommendations\n\n"
            "**`/tips`** — Quick gameplay tips"
        ),
        "color": 0x9b59b6,  # purple
    })

    # --- 5. Gift Codes ---
    guides.append({
        "title": "🎁 Gift Codes",
        "description": (
            "**`/codes`** — View all active gift codes with rewards\n\n"
            "**`/addcode CODE123 | 500 gems, 2 speedups`** — Submit a new code\n"
            "Pipe `|` separates the code from rewards description.\n"
            "If you've linked your account with `/register`, codes are auto-redeemed!\n\n"
            "**`/expirecode CODE123`** — Mark a code as expired\n\n"
            "**`/redeemcode CODE123`** — Manually redeem a code\n\n"
            "**`/codehistory`** — View past codes and their status\n\n"
            "**Auto-Redeem:** When a new code is added, the bot automatically "
            "redeems it for all registered players. Use `/register` to link your account!"
        ),
        "color": 0xFF69B4,  # pink
    })

    # --- 6. Game Link ---
    guides.append({
        "title": "🎮 Game Account Linking",
        "description": (
            "Link your game account for auto gift code redemption and player lookup.\n\n"
            "**`/register`** — Link your Kingshot account (uses your FID)\n\n"
            "**`/whoami`** — Check your linked account details\n\n"
            "**`/lookup`** — Look up any player by FID\n\n"
            "**`/unregister`** — Unlink your game account\n\n"
            "**Why link?** When someone posts a new gift code, "
            "the bot will automatically redeem it on your behalf — "
            "no more missing codes while you're offline!"
        ),
        "color": 0xe67e22,  # orange
    })

    # --- 7. Timers & Timezone ---
    guides.append({
        "title": "⏰ Timers & Timezone",
        "description": (
            "**`/timezone EST`** — Set your timezone\n"
            "Accepts: `EST`, `PST`, `UTC+5`, `America/New_York`, `Europe/London`, etc.\n\n"
            "**`/localtime`** — See current UTC and your local time\n"
            "`/localtime 2026-03-15 20:00` — Convert a UTC time to your timezone\n\n"
            "**`/settimer Swordland | 2026-03-15 20:00 | EST`** 🔒\n"
            "Set an event timer. Timezone is optional (uses your saved TZ or UTC).\n"
            "Format: `Name | YYYY-MM-DD HH:MM | timezone`\n\n"
            "**`/timers`** — View all active timers\n\n"
            "**`/deltimer <name>`** 🔒 — Delete a timer\n\n"
            "**`/remindme`** — Toggle DM reminders before events"
        ),
        "color": 0xf39c12,  # gold
    })

    # --- 8. Stats & Optimization ---
    guides.append({
        "title": "📊 Stats & Optimization",
        "description": (
            "**`/mystats`** — Enter/update your detailed stats\n\n"
            "**`/updatetroops`** — Update troop composition data\n\n"
            "**`/alliancestats`** — View alliance-wide stat summary\n\n"
            "**`/eventready`** — Check if you're prepared for upcoming events\n\n"
            "**`/optimize`** — Get personalized optimization suggestions\n"
            "Based on your current stats, recommends where to improve.\n\n"
            "**`/myfit`** — See your fitness score for different roles\n\n"
            "**`/achievements`** — View your earned achievements\n\n"
            "**`/mvp`** — Alliance MVP leaderboard based on contributions"
        ),
        "color": 0x1abc9c,  # teal
    })

    # --- 9. War & Rally ---
    guides.append({
        "title": "⚔️ War Commands",
        "description": (
            "**`/warsched`** — View the current war schedule\n\n"
            "**`/warsignup`** — Sign up for an upcoming war\n\n"
            "**`/attendance`** — Check war attendance roster 🔒\n\n"
            "**`/rally`** — View active rallies\n\n"
            "**`/rally_create`** 🔒 — Create a new rally call\n\n"
            "**`/rally_status`** 🔒 — Check rally participation\n\n"
            "**`/warhistory`** — View war history: signups, rallies, territory changes\n"
            "(Paginated 3-page summary)\n\n"
            "**`/warlog`** 🔒 — Leadership war summary with active timers, "
            "signups, kills, scouts, and territory status"
        ),
        "color": 0xe74c3c,  # red
    })

    # --- 10. Kill Tracking & Scouting ---
    guides.append({
        "title": "💀 Kill Tracking & 🔍 Scouting",
        "description": (
            "**Kill Tracking:**\n"
            "**`/reportkill`** — Report an enemy kill with details\n"
            "**`/killers`** — View the top killers leaderboard\n"
            "**`/mykills`** — View your personal kill stats\n\n"
            "**Scouting & Intel:**\n"
            "**`/scout`** — Submit a scout report on an enemy\n"
            "**`/threat`** — Check the threat level of a target\n"
            "**`/scouts`** — View recent scout reports\n\n"
            "**Migration & Territory:**\n"
            "**`/migration`** — Track kingdom migration activity\n"
            "**`/territory`** — View current territory control\n"
            "**`/territory_report`** 🔒 — Detailed territory report"
        ),
        "color": 0x95a5a6,  # grey
    })

    # --- 11. Community ---
    guides.append({
        "title": "💡 Community Features",
        "description": (
            "Share strategies and troop compositions with your alliance!\n\n"
            "**`/suggest <event> <tip>`** — Submit a strategy tip\n"
            "Example: `/suggest bear Focus siege units on rear towers`\n\n"
            "**`/viewsuggestions <event>`** — Browse suggestions for an event\n\n"
            "**`/votesuggestion <#>`** — Upvote a community suggestion\n"
            "One vote per person, no double-voting.\n\n"
            "**`/topsuggestions`** — See the most popular suggestions\n"
            "`/topsuggestions bear` — Filter by event\n\n"
            "**`/reportcomp <event> <inf%> <cav%> <arch%> <result>`**\n"
            "Share your troop composition and how it performed.\n"
            "Percentages must add up to 100.\n\n"
            "**`/troopstats <event>`** — View aggregated troop data\n"
            "Average compositions and recent reports from alliance members.\n\n"
            "**`/approvesuggestion <#> approve/reject`** 🔒 — Moderate suggestions"
        ),
        "color": 0xf1c40f,  # gold
    })

    # --- 12. Translation ---
    guides.append({
        "title": "🌐 Translation",
        "description": (
            "**`/translate <text>`** — Translate text to English (or your set language)\n\n"
            "**`/setlang es`** — Set your preferred language\n"
            "Supported: `en`, `es`, `fr`, `de`, `pt`, `ja`, `ko`, `zh`, `ar`, `ru`, `tr`, and more\n\n"
            "**`/languages`** — View all supported languages\n\n"
            "**Flag Emoji Reactions:** React to any message with a country flag "
            "(🇪🇸 🇫🇷 🇩🇪 🇯🇵 🇰🇷 🇨🇳 🇧🇷 🇷🇺 🇹🇷 🇸🇦) to translate it.\n\n"
            "**🌐 Reaction:** React with 🌐 to translate a message to your saved language."
        ),
        "color": 0x2ecc71,  # green
    })

    # --- 13. Privacy & Data ---
    guides.append({
        "title": "🔒 Privacy & Your Data",
        "description": (
            "We respect your privacy. Here's how the bot handles your data:\n\n"
            "**`/privacy`** — See exactly what data the bot stores about you\n"
            "Shows: profile, timezone, power history, stats, game link, language, "
            "kill reports, and reminder preferences.\n\n"
            "**`/deletedata`** — **Permanently delete ALL your data**\n"
            "Removes everything: profile, power history, stats, game link, "
            "timezone, language preference, and reminder opt-in.\n"
            "⚠️ This action requires confirmation and **cannot be undone**.\n\n"
            "**What we store:**\n"
            "• In-game name & power level\n"
            "• Timezone & language preference\n"
            "• Troop/stat data you enter\n"
            "• Game account FID (if registered)\n"
            "• Kill reports you submit\n\n"
            "**What we DON'T store:**\n"
            "• Your messages or DMs\n"
            "• Your Discord password or email\n"
            "• Any data not explicitly entered via commands\n\n"
            "Daily automatic backups ensure data safety. "
            "Backups are retained for 7 days."
        ),
        "color": 0x2c3e50,  # dark blue
    })

    # --- 14. Admin & Leadership ---
    guides.append({
        "title": "🛡️ Admin & Leadership Commands",
        "description": (
            "These commands require **R4+ or Admin** permissions.\n\n"
            "**Announcements:**\n"
            "`/announce <channel> <message>` — Post an announcement\n"
            "`/listannouncements` — View 35+ scheduled announcements\n"
            "`/toggleannouncement <name>` — Enable/disable scheduled posts\n\n"
            "**Role Management:**\n"
            "`/promote @member <role>` — Assign a role\n"
            "`/demote @member <role>` — Remove a role\n"
            "`/rolepanel` 🛡️ — Post interactive role selection panel\n\n"
            "**Moderation:**\n"
            "`/kick @member [reason]` — Kick a member\n"
            "`/mute @member [minutes]` — Timeout a member\n"
            "`/unmute @member` — Remove timeout\n"
            "`/clear [amount]` — Delete messages (max 100)\n"
            "`/modlog` — View recent moderation actions\n\n"
            "**System & Config:**\n"
            "`/setup` 🛡️ — Initial server setup (creates roles)\n"
            "`/bot_health` — Check bot uptime and status\n"
            "`/data_stats` — View data storage stats\n"
            "`/backup` — Manual data backup\n"
            "`/configchannel <key> [channel]` 🛡️ — Map logical channel names\n"
            "`/channels` — View all channel mappings\n"
            "`/configaudit` 🛡️ — View config change history\n"
            "`/apistatus` 🛡️ — API health & circuit breaker status\n"
            "`/inactive [days]` — Find inactive members (default: 14 days)"
        ),
        "color": 0x34495e,  # dark grey
    })

    # --- 15. Footer ---
    guides.append({
        "title": "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "description": (
            "**Kingshot Bot v3.1.0** — Built for our alliance, by our alliance.\n\n"
            "Use `/help` anytime for a quick command reference.\n"
            "Questions? Ask in #general or ping an R4.\n\n"
            "*Last updated: March 2026*"
        ),
        "color": 0x5865F2,  # blurple
    })

    return guides


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not TOKEN or TOKEN == "ENV":
        print("ERROR: No bot token. Set DISCORD_BOT_TOKEN env var.")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv
    no_clear = "--no-clear" in sys.argv

    # Resolve channel
    print("Resolving #bot-guide channel...")
    channel_id = get_channel_id("bot-guide")
    if not channel_id:
        print("ERROR: #bot-guide channel not found in guild.")
        sys.exit(1)
    print(f"  Found: #{channel_id}")

    # Clear existing messages
    if not no_clear and not dry_run:
        print("Clearing existing guide messages...")
        clear_channel(channel_id)

    # Build and post guides
    guides = build_guides()
    print(f"Posting {len(guides)} guide embeds...")
    for embed in guides:
        post_embed(channel_id, embed, dry_run=dry_run)

    print(f"\nDone! {'(DRY RUN)' if dry_run else f'{len(guides)} embeds posted to #bot-guide'}")


if __name__ == "__main__":
    main()
