# Kingshot Discord Bot

Discord bot for managing the Kingshot alliance guild server. Built for members (R1-R5) and leadership.

## Features

- **Event Guides** — Strategy guides for all Kingshot events (Swordland, KvK, Bear Hunt, etc.)
- **Profile System** — Track IGN, power level, leaderboard rankings
- **Gift Codes** — Submit and view active redeem codes
- **War Tools** — Rally calls, war schedule, timers with alerts
- **Moderation** — Kick, mute, clear, promote/demote
- **Scheduled Announcements** — Daily reminders, war prep alerts, tips

## Architecture

- **Runtime**: Python 3.12 + discord.py
- **Deployment**: OKD (OpenShift) via GitHub Actions CI/CD
- **Data**: Persistent volume for profiles, gift codes, timers (JSON files)
- **Config**: Bot token injected via OKD Secret, settings via ConfigMap

## Quick Start (Local)

```bash
pip install -r requirements.txt
export DISCORD_BOT_TOKEN="your-token-here"
python bot.py
```

## Deployment (OKD)

Pushing to `main` triggers automatic build and deployment via GitHub Actions.

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `OKD_TOKEN` | OKD service account token |
| `DISCORD_BOT_TOKEN` | Discord bot token |

### Manual Deploy

```bash
gh workflow run deploy.yml
```

## Commands

| Command | Access | Description |
|---------|--------|-------------|
| `!events` | Everyone | List all event guides |
| `!event <name>` | Everyone | Full guide for an event |
| `!profile` | Everyone | View your profile |
| `!setpower <n>` | Everyone | Set your power level |
| `!codes` | Everyone | View active gift codes |
| `!timers` | Everyone | View event timers |
| `!rally <text>` | R3+ | Send rally call |
| `!announce` | R4+ | Post announcement |
| `!promote/@demote` | R4+ | Manage member roles |

See `#bot-guide` in Discord for the full command reference.
