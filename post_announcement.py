#!/usr/bin/env python3
"""
Post v3.1 features & privacy announcement to #announcements.

Usage:
    python post_announcement.py
    python post_announcement.py --dry-run   # Preview without posting

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

HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
}
API = "https://discord.com/api/v10"


def get_channel_id(name="announcements"):
    """Find channel ID by name."""
    r = requests.get(f"{API}/guilds/{GUILD_ID}/channels", headers=HEADERS)
    r.raise_for_status()
    for ch in r.json():
        if ch["name"] == name and ch["type"] == 0:
            return ch["id"]
    return None


def post_embeds(channel_id, embeds, dry_run=False):
    """Post embeds to a channel."""
    if dry_run:
        for e in embeds:
            print(f"  [DRY RUN] Would post: {e.get('title', 'Untitled')}")
        return
    payload = {"embeds": embeds}
    r = requests.post(f"{API}/channels/{channel_id}/messages", headers=HEADERS, json=payload)
    r.raise_for_status()
    print(f"  Posted {len(embeds)} embed(s)")


# ---------------------------------------------------------------------------
# Announcement Content
# ---------------------------------------------------------------------------

def build_announcement():
    """Build the v3.1 feature announcement embeds."""
    # Main announcement (up to 10 embeds per message)
    embeds = []

    # Header
    embeds.append({
        "title": "🚀 Kingshot Bot v3.1 — Major Update!",
        "description": (
            "We've been hard at work improving the bot with **40+ enhancements** "
            "across security, privacy, war tools, and community features. "
            "Here's what's new!\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "color": 0x5865F2,
    })

    # Privacy & Data Protection
    embeds.append({
        "title": "🔒 Privacy & Data Protection",
        "description": (
            "Your data, your control. Two new commands give you full transparency "
            "and ownership over your stored information.\n\n"
            "**`/privacy`** — See exactly what the bot stores about you\n"
            "View your profile, timezone, power history, stats, game link, "
            "language, kill reports, and reminder settings — all in one place.\n\n"
            "**`/deletedata`** — Permanently delete ALL your data\n"
            "One command removes everything. Requires confirmation. "
            "Cannot be undone.\n\n"
            "**What we DON'T store:** Your messages, DMs, Discord credentials, "
            "or anything you haven't explicitly entered via commands."
        ),
        "color": 0x2c3e50,
    })

    # War & Strategy
    embeds.append({
        "title": "⚔️ War & Strategy Tools",
        "description": (
            "**`/warhistory`** — Full war history in one view\n"
            "Paginated 3-page summary showing signups, rallies, and territory changes.\n\n"
            "**`/warlog`** 🔒 — Leadership dashboard\n"
            "Quick overview: active timers, signup count, rally stats, "
            "kills, scouts, and territory status.\n\n"
            "**`/settimer`** — Now timezone-aware!\n"
            "Format: `/settimer Event Name | 2026-03-20 20:00 | EST`\n"
            "Timezone is optional — defaults to your saved timezone, then UTC.\n\n"
            "**`/inactive [days]`** 🔒 — Find inactive members\n"
            "Checks across stats, troops, and power updates. Default: 14 days."
        ),
        "color": 0xe74c3c,
    })

    # Community Features
    embeds.append({
        "title": "💡 Community Crowdsourcing",
        "description": (
            "The suggestion system is now supercharged with voting!\n\n"
            "**`/votesuggestion <#>`** — Upvote community suggestions\n"
            "One vote per person. No double-voting.\n\n"
            "**`/topsuggestions`** — See the most popular strategies\n"
            "Filter by event or view all. Ranked by community votes.\n\n"
            "Combined with existing `/suggest`, `/viewsuggestions`, "
            "and `/reportcomp` — your alliance can now crowdsource "
            "the best strategies and troop compositions."
        ),
        "color": 0xf1c40f,
    })

    # Infrastructure & Reliability
    embeds.append({
        "title": "🛡️ Reliability & Admin Tools",
        "description": (
            "**API Circuit Breaker** — Auto-redeem now has smart failure protection. "
            "After 5 consecutive API failures, auto-redeem pauses for 30 minutes "
            "to prevent rate limiting. Leadership can check with `/apistatus`.\n\n"
            "**Configurable Channels** — All bot channels are now remappable:\n"
            "`/configchannel announcements my-announcements`\n"
            "`/channels` — View all mappings\n\n"
            "**Config Audit Trail** — Every config change is logged with who/what/when.\n"
            "`/configaudit` — View the change history\n\n"
            "**Automated Backups** — Daily data backups with 7-day retention.\n"
            "`/backup` — Manual backup anytime\n\n"
            "**Onboarding DMs** — New members automatically receive a "
            "welcome DM with essential commands to get started."
        ),
        "color": 0x3498db,
    })

    # Footer
    embeds.append({
        "title": "📖 Updated Bot Guide",
        "description": (
            "The **#bot-guide** channel has been fully updated with "
            "all v3.1 commands, organized by category.\n\n"
            "Use `/help` anytime for a quick reference, or browse "
            "#bot-guide for detailed explanations and examples.\n\n"
            "Questions or feedback? Drop a message in #general "
            "or use `/suggest` to submit ideas!\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "*Kingshot Bot v3.1.0 — March 2026*"
        ),
        "color": 0x5865F2,
    })

    return embeds


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not TOKEN or TOKEN == "ENV":
        print("ERROR: No bot token. Set DISCORD_BOT_TOKEN env var.")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv

    print("Resolving #announcements channel...")
    channel_id = get_channel_id("announcements")
    if not channel_id:
        print("ERROR: #announcements channel not found.")
        sys.exit(1)
    print(f"  Found: #{channel_id}")

    embeds = build_announcement()
    print(f"Posting announcement ({len(embeds)} embeds)...")
    post_embeds(channel_id, embeds, dry_run=dry_run)

    print(f"\nDone! {'(DRY RUN)' if dry_run else 'Announcement posted to #announcements'}")


if __name__ == "__main__":
    main()
