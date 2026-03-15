"""
Centralized constants for the Kingshot Discord Bot.
Avoids magic numbers scattered across cogs.
"""

# --- Rate Limits & Thresholds ---
API_RATE_LIMIT_DELAY = 2.5      # seconds between API calls
API_MAX_BACKOFF = 30.0           # max backoff delay
CIRCUIT_BREAKER_THRESHOLD = 5    # consecutive failures to trip circuit
CIRCUIT_BREAKER_RECOVERY = 1800  # seconds (30 min) before auto-recovery

# --- Data Retention ---
BACKUP_RETENTION_DAYS = 7
KILL_LOG_RETENTION_DAYS = 90
SCOUT_REPORT_RETENTION_DAYS = 90
POWER_HISTORY_MAX_PER_USER = 200
TERRITORY_LOG_MAX = 500
RALLY_SESSION_MAX = 100
WAR_SIGNUP_MAX = 50
CODE_HISTORY_MAX = 200
SCRAPE_LOG_MAX = 30

# --- Cooldowns (seconds) ---
DEFAULT_COOLDOWN = 10
LONG_COOLDOWN = 30
BACKUP_COOLDOWN = 300

# --- Limits ---
MAX_TC_LEVEL = 35
MAX_KINGDOM = 99999
LEADERBOARD_PAGE_SIZE = 10
SUGGESTION_PAGE_SIZE = 5
MAX_TIMER_NAME = 100
MAX_ZONE_NAME = 50

# --- Guild Roles (canonical names) ---
ROLE_R5 = "R5 | Alliance Leader"
ROLE_R4 = "R4 | Leadership"
ROLE_R3 = "R3 | TC25+"
ROLE_R2 = "R2 | Trusted"
ROLE_R1 = "R1 | Bear Bait"

# --- Embed Colors ---
COLOR_SUCCESS = 0x2ecc71     # green
COLOR_WARNING = 0xf39c12     # orange
COLOR_ERROR = 0xe74c3c       # red
COLOR_INFO = 0x3498db        # blue
COLOR_GOLD = 0xf1c40f        # gold

# --- Version ---
BOT_VERSION = "3.1.0"
