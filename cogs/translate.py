"""Translate cog — auto-detect and translate messages for international guilds."""
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import re

from utils import load_data, save_data, cooldown

log = logging.getLogger("kingshot-bot")

# ---------------------------------------------------------------------------
# Language mappings
# ---------------------------------------------------------------------------
# Flag emoji → language code (regional indicator pairs)
FLAG_TO_LANG = {
    "🇺🇸": "en", "🇬🇧": "en", "🇪🇸": "es", "🇲🇽": "es",
    "🇫🇷": "fr", "🇩🇪": "de", "🇮🇹": "it", "🇵🇹": "pt",
    "🇧🇷": "pt", "🇷🇺": "ru", "🇨🇳": "zh-CN", "🇹🇼": "zh-TW",
    "🇯🇵": "ja", "🇰🇷": "ko", "🇮🇳": "hi", "🇸🇦": "ar",
    "🇹🇷": "tr", "🇹🇭": "th", "🇻🇳": "vi", "🇮🇩": "id",
    "🇵🇱": "pl", "🇳🇱": "nl", "🇸🇪": "sv", "🇳🇴": "no",
    "🇩🇰": "da", "🇫🇮": "fi", "🇨🇿": "cs", "🇷🇴": "ro",
    "🇭🇺": "hu", "🇬🇷": "el", "🇺🇦": "uk", "🇵🇭": "tl",
    "🇲🇾": "ms", "🇮🇱": "he",
}

# User-friendly language names
LANG_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)", "ja": "Japanese", "ko": "Korean",
    "hi": "Hindi", "ar": "Arabic", "tr": "Turkish", "th": "Thai",
    "vi": "Vietnamese", "id": "Indonesian", "pl": "Polish", "nl": "Dutch",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
    "cs": "Czech", "ro": "Romanian", "hu": "Hungarian", "el": "Greek",
    "uk": "Ukrainian", "tl": "Filipino", "ms": "Malay", "he": "Hebrew",
    "bg": "Bulgarian", "hr": "Croatian", "sk": "Slovak", "sl": "Slovenian",
    "et": "Estonian", "lv": "Latvian", "lt": "Lithuanian",
}

# Supported language codes for autocomplete
SUPPORTED_LANGS = list(LANG_NAMES.keys())

# ---------------------------------------------------------------------------
# Translation helper (uses deep-translator)
# ---------------------------------------------------------------------------
_translator_available = False
try:
    from deep_translator import GoogleTranslator
    _translator_available = True
except ImportError:
    log.warning("deep-translator not installed — translate cog will be limited")


async def _translate_text(text: str, target: str, source: str = "auto") -> str | None:
    """Translate text using Google Translate via deep-translator. Returns None on failure."""
    if not _translator_available:
        return None
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: GoogleTranslator(source=source, target=target).translate(text[:4500])
        )
        return result
    except Exception as e:
        log.error(f"Translation failed: {e}")
        return None



# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class Translate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # User language preferences: {user_id: "es"}
        self.user_langs = load_data("user_languages", {})

    # --- /translate ---
    @commands.hybrid_command(name="translate")
    @app_commands.describe(
        text="Text to translate",
        to="Target language (e.g. es, fr, de, ja, ko, zh-CN)",
    )
    @cooldown(5)
    async def translate_cmd(self, ctx: commands.Context, to: str, *, text: str):
        """Translate text to any supported language."""
        if not _translator_available:
            await ctx.send("❌ Translation service unavailable. Please try later.", ephemeral=True)
            return

        target = to.lower().strip()
        if target not in LANG_NAMES:
            suggestions = ", ".join(f"`{k}` ({v})" for k, v in list(LANG_NAMES.items())[:15])
            await ctx.send(
                f"❌ Unknown language code `{to}`. Try one of: {suggestions}...\n"
                f"Use `/languages` to see all supported languages.",
                ephemeral=True,
            )
            return

        await ctx.defer()
        result = await _translate_text(text, target)
        if result is None:
            await ctx.send("❌ Translation failed. Please try again.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🌐 Translation → {LANG_NAMES.get(target, target)}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="📝 Original", value=text[:1024], inline=False)
        embed.add_field(name=f"🔄 {LANG_NAMES.get(target, target)}", value=result[:1024], inline=False)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @translate_cmd.autocomplete("to")
    async def translate_to_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = []
        for code, name in LANG_NAMES.items():
            if current.lower() in code.lower() or current.lower() in name.lower():
                choices.append(app_commands.Choice(name=f"{name} ({code})", value=code))
        return choices[:25]

    # --- /setlang ---
    @commands.hybrid_command(name="setlang")
    @app_commands.describe(language="Your preferred language code (e.g. es, fr, de)")
    @cooldown(10)
    async def setlang(self, ctx: commands.Context, language: str):
        """Set your preferred language. Bot responses with 🌐 can be auto-translated."""
        lang = language.lower().strip()
        if lang == "off" or lang == "none":
            self.user_langs.pop(str(ctx.author.id), None)
            save_data("user_languages", self.user_langs)
            await ctx.send("✅ Auto-translate preference removed.", ephemeral=True)
            return
        if lang not in LANG_NAMES:
            await ctx.send(f"❌ Unknown language `{language}`. Use `/languages` to see options.", ephemeral=True)
            return
        self.user_langs[str(ctx.author.id)] = lang
        save_data("user_languages", self.user_langs)
        await ctx.send(
            f"✅ Language set to **{LANG_NAMES[lang]}** ({lang}).\n"
            f"React with 🌐 on any message to translate it to {LANG_NAMES[lang]}!",
            ephemeral=True,
        )

    @setlang.autocomplete("language")
    async def setlang_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = [app_commands.Choice(name="Remove preference", value="off")]
        for code, name in LANG_NAMES.items():
            if current.lower() in code.lower() or current.lower() in name.lower():
                choices.append(app_commands.Choice(name=f"{name} ({code})", value=code))
        return choices[:25]

    # --- /languages ---
    @commands.hybrid_command(name="languages")
    @cooldown(10)
    async def languages(self, ctx: commands.Context):
        """Show all supported languages for translation."""
        lines = [f"`{code}` — {name}" for code, name in sorted(LANG_NAMES.items(), key=lambda x: x[1])]
        mid = len(lines) // 2
        col1 = "\n".join(lines[:mid])
        col2 = "\n".join(lines[mid:])
        embed = discord.Embed(
            title="🌐 Supported Languages",
            description="Use these codes with `/translate` or `/setlang`.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="A–L", value=col1[:1024], inline=True)
        embed.add_field(name="M–Z", value=col2[:1024], inline=True)
        embed.add_field(
            name="💡 How to use",
            value=(
                "**Option 1:** `/translate to:es text:Hello everyone!`\n"
                "**Option 2:** `/setlang es` then react with 🌐 on messages\n"
                "**Option 3:** React with a flag emoji (🇪🇸🇫🇷🇩🇪🇯🇵) on any message"
            ),
            inline=False,
        )
        await ctx.send(embed=embed, ephemeral=True)

    # --- Flag reaction translation ---
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Translate a message when someone reacts with a flag emoji or 🌐."""
        if payload.user_id == self.bot.user.id:
            return  # Ignore bot's own reactions

        emoji_str = str(payload.emoji)

        # Determine target language
        target_lang = None
        if emoji_str == "🌐":
            # Use the user's preferred language
            target_lang = self.user_langs.get(str(payload.user_id))
            if not target_lang:
                return  # No preference set, ignore
        elif emoji_str in FLAG_TO_LANG:
            target_lang = FLAG_TO_LANG[emoji_str]
        else:
            return  # Not a translation emoji

        if not _translator_available:
            return

        # Fetch the message
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        if not message.content or len(message.content.strip()) < 2:
            return  # Nothing to translate

        # Translate
        result = await _translate_text(message.content, target_lang)
        if result is None or result.strip().lower() == message.content.strip().lower():
            return  # Translation failed or text is already in target language

        # Send as ephemeral-like reply (we can't send ephemeral outside interactions,
        # so we send a short-lived reply that auto-deletes after 60s)
        embed = discord.Embed(
            description=f"**{LANG_NAMES.get(target_lang, target_lang)}:** {result[:2000]}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Translated for {payload.member.display_name if payload.member else 'user'} • deletes in 60s")
        try:
            reply = await message.reply(embed=embed, mention_author=False)
            # Auto-delete after 60 seconds to keep chat clean
            import asyncio
            await asyncio.sleep(60)
            await reply.delete()
        except Exception as e:
            log.debug(f"Could not send/delete translation reply: {e}")

    # --- Auto-translate listener for non-English messages ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Auto-add 🌐 reaction to messages that appear non-English, as a translate hint."""
        if message.author.bot or not message.content:
            return
        # Only hint if message has enough text and contains non-ASCII characters
        text = message.content.strip()
        if len(text) < 10:
            return
        # Quick heuristic: if more than 30% of chars are non-ASCII, likely non-English
        non_ascii = sum(1 for c in text if ord(c) > 127)
        if non_ascii / len(text) > 0.3:
            try:
                await message.add_reaction("🌐")
            except Exception:
                pass  # Missing permissions or similar


    # --- One-time feature announcement ---
    def start_tasks(self):
        """Called from bot.py after cog is loaded."""
        self._announce_feature.start()

    @tasks.loop(count=1)
    async def _announce_feature(self):
        """Post the translate feature announcement once, then never again."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)  # Wait for guild cache to populate

        announce_data = load_data("translate_announced", {"done": False})
        if announce_data.get("done"):
            return

        guild = self.bot.guilds[0] if self.bot.guilds else None
        if not guild:
            return

        # --- Post to #announcements ---
        ann_ch = discord.utils.get(guild.text_channels, name="announcements")
        if ann_ch:
            embed = discord.Embed(
                title="🌐 NEW: Auto-Translate is Here!",
                description=(
                    "Our guild has members from all over the world — now the bot "
                    "can translate messages instantly so everyone can communicate!\n\n"
                ),
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="🏳️ Flag Reactions",
                value=(
                    "React with a country flag on any message to translate it:\n"
                    "🇪🇸 Spanish • 🇫🇷 French • 🇩🇪 German • 🇯🇵 Japanese\n"
                    "🇰🇷 Korean • 🇧🇷 Portuguese • 🇷🇺 Russian • 🇨🇳 Chinese\n"
                    "...and 25+ more languages!"
                ),
                inline=False,
            )
            embed.add_field(
                name="🌐 Personal Language",
                value=(
                    "Set your preferred language with `/setlang`\n"
                    "Then just react with 🌐 on any message to translate it to YOUR language!"
                ),
                inline=False,
            )
            embed.add_field(
                name="💬 Manual Translate",
                value="Use `/translate to:es text:Hello everyone!` to translate any text on demand.",
                inline=False,
            )
            embed.add_field(
                name="📖 All Languages",
                value="Type `/languages` to see every supported language code.",
                inline=False,
            )
            embed.set_footer(text="Translations auto-delete after 60s to keep chat clean")
            try:
                await ann_ch.send(embed=embed)
                log.info("Posted translate feature announcement to #announcements")
            except Exception as e:
                log.error(f"Failed to post translate announcement: {e}")

        # --- Post to #bot-guide ---
        guide_ch = discord.utils.get(guild.text_channels, name="bot-guide")
        if guide_ch:
            guide_embed = discord.Embed(
                title="🌐 Translation Commands",
                description="Communicate across languages — translate messages in real time!",
                color=discord.Color.blue(),
            )
            guide_embed.add_field(
                name="/translate",
                value="`/translate to:<lang> text:<message>`\nTranslate any text to a supported language.\nExample: `/translate to:es text:Let's rally at 12:00 UTC!`",
                inline=False,
            )
            guide_embed.add_field(
                name="/setlang",
                value="`/setlang <language>`\nSet your preferred language. Then react 🌐 on any message to translate it.\nExample: `/setlang ko` → react 🌐 → instant Korean translation\nUse `/setlang off` to remove.",
                inline=False,
            )
            guide_embed.add_field(
                name="/languages",
                value="View all 30+ supported language codes.",
                inline=False,
            )
            guide_embed.add_field(
                name="🏳️ Flag Reactions",
                value=(
                    "React with a country flag emoji on any message to translate:\n"
                    "🇺🇸🇬🇧 English • 🇪🇸🇲🇽 Spanish • 🇫🇷 French • 🇩🇪 German\n"
                    "🇮🇹 Italian • 🇵🇹🇧🇷 Portuguese • 🇷🇺 Russian • 🇨🇳 Chinese\n"
                    "🇯🇵 Japanese • 🇰🇷 Korean • 🇮🇳 Hindi • 🇸🇦 Arabic\n"
                    "🇹🇷 Turkish • 🇹🇭 Thai • 🇻🇳 Vietnamese • 🇮🇩 Indonesian\n"
                    "🇵🇱 Polish • 🇳🇱 Dutch • 🇸🇪 Swedish • 🇺🇦 Ukrainian\n"
                    "...and more!"
                ),
                inline=False,
            )
            guide_embed.add_field(
                name="🌐 Auto-Detect",
                value="The bot auto-adds a 🌐 reaction to non-English messages as a translate hint.",
                inline=False,
            )
            guide_embed.set_footer(text="Translated replies auto-delete after 60s to keep chat clean")
            try:
                await guide_ch.send(embed=guide_embed)
                log.info("Posted translate guide to #bot-guide")
            except Exception as e:
                log.error(f"Failed to post translate guide: {e}")

        # Mark as done so it never fires again
        save_data("translate_announced", {"done": True})


async def setup(bot):
    await bot.add_cog(Translate(bot))
