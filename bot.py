import discord
from discord.ext import commands
from discord.ext import tasks
from dotenv import load_dotenv
import os
from groq import Groq
import json
import re
import asyncio
from discord.ui import Button, View
from PIL import Image, ImageDraw, ImageFont
import io
import math
import aiohttp
import motor.motor_asyncio
from datetime import datetime, timezone

load_dotenv()

# MongoDB setup
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(os.getenv("MONGODB_URL"))
db = mongo_client["architectai"]
guilds_col = db["guilds"]

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(data: dict):
    current = load_state()
    current.update(data)
    with open(STATE_FILE, "w") as f:
        json.dump(current, f, indent=2)

async def db_save(guild_id: str, data: dict):
    try:
        await guilds_col.update_one(
            {"guild_id": guild_id},
            {"$set": data},
            upsert=True
        )
    except Exception as e:
        print(f"⚠️ DB save error: {e}")

async def db_load(guild_id: str) -> dict:
    try:
        result = await guilds_col.find_one({"guild_id": guild_id})
        return result or {}
    except Exception as e:
        print(f"⚠️ DB load error: {e}")
        return {}

async def automod_warn(guild: discord.Guild, member: discord.Member, reason: str):
    guild_id = str(guild.id)
    user_id = str(member.id)

    data = await db_load(guild_id)
    automod_warns = data.get("automod_warns", {})
    config = data.get("automod", {})
    threshold = config.get("warn_threshold", 3)

    if user_id not in automod_warns:
        automod_warns[user_id] = 0
    automod_warns[user_id] += 1
    warn_count = automod_warns[user_id]

    await db_save(guild_id, {"automod_warns": automod_warns})

    log_channel = discord.utils.get(guild.text_channels, name="「📋」mod-logs")
    if not log_channel:
        log_channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if log_channel:
        embed = discord.Embed(
            title="🛡️ Auto-Mod Action",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="User", value=f"{member.mention} ({member.name})", inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Warning Count", value=f"{warn_count}/{threshold}", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await log_channel.send(embed=embed)

    if warn_count >= threshold:
        automod_warns[user_id] = 0
        await db_save(guild_id, {"automod_warns": automod_warns})
        try:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=10)
            await member.timeout(until, reason=f"Auto-mod: {warn_count} violations")
            if log_channel:
                await log_channel.send(
                    embed=discord.Embed(
                        title="⏱️ Auto-Mod Timeout",
                        description=f"{member.mention} was timed out for 10 minutes after {warn_count} violations!",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow()
                    )
                )
        except:
            pass

LEVELS_FILE = "levels.json"

def load_levels() -> dict:
    if os.path.exists(LEVELS_FILE):
        with open(LEVELS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_levels(data: dict):
    with open(LEVELS_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def load_guild_levels(guild_id: str) -> dict:
    data = await db_load(guild_id)
    levels = data.get("levels", {})
    if isinstance(levels, dict) and levels:
        return levels

    # Backward-compatible fallback for local/dev runs.
    file_levels = load_levels()
    guild_levels = file_levels.get(guild_id, {})
    if guild_levels:
        await db_save(guild_id, {"levels": guild_levels})
    return guild_levels


async def save_guild_levels(guild_id: str, guild_levels: dict):
    await db_save(guild_id, {"levels": guild_levels})

def is_staff(member: discord.Member) -> bool:
    staff_role_names = ["Admin", "Moderator", "Administrator", "Mod", "Staff"]
    for role in member.roles:
        if any(name.lower() in role.name.lower() for name in staff_role_names):
            return True
    if member.guild_permissions.kick_members:
        return True
    if member.guild_permissions.ban_members:
        return True
    if member.guild_permissions.manage_guild:
        return True
    if member == member.guild.owner:
        return True
    return False

def is_owner(member: discord.Member) -> bool:
    return member == member.guild.owner

def owner_only():
    async def predicate(ctx):
        if not is_owner(ctx.author):
            await ctx.send("❌ Only the server owner can use this command!")
            return False
        return True
    return commands.check(predicate)

def staff_only():
    async def predicate(ctx):
        if not is_staff(ctx.author):
            await ctx.send("❌ You need staff permissions to use this command!")
            return False
        return True
    return commands.check(predicate)

SERVER_TEMPLATES = {
    "gaming": {
        "emoji": "🎮",
        "label": "Gaming",
        "description": "A server for gamers with game channels, voice rooms, and gaming roles",
        "hint": "gaming server with channels for different games, game night planning, clips and screenshots"
    },
    "study": {
        "emoji": "📚",
        "label": "Study",
        "description": "A focused study server with subject channels and study rooms",
        "hint": "study server with subject channels, study rooms, resource sharing, and focus timers"
    },
    "anime": {
        "emoji": "🎌",
        "label": "Anime",
        "description": "An anime community with discussion channels and watch parties",
        "hint": "anime server with discussion channels, watch party rooms, fan art sharing, and seasonal anime chat"
    },
    "business": {
        "emoji": "💼",
        "label": "Business",
        "description": "A professional workspace with project and team channels",
        "hint": "professional business server with project channels, team meetings, client communication and file sharing"
    },
    "creative": {
        "emoji": "🎨",
        "label": "Creative",
        "description": "A creative community for artists, writers and musicians",
        "hint": "creative server with art sharing, writing workshops, music rooms, and feedback channels"
    },
    "community": {
        "emoji": "🏘️",
        "label": "Community",
        "description": "A community hub with suggestions, bug reports, reviews and events",
        "hint": "community server with suggestions, bug reports, reviews, general discussion and events"
    },
    "custom": {
        "emoji": "🎲",
        "label": "Custom",
        "description": "Describe your own server from scratch",
        "hint": None
    }
}

COMMUNITY_CHANNELS = [
    {"name": "「📢」announcements", "type": "text", "topic": "Official server announcements — only staff can post here", "staff_post_only": True},
    {"name": "「💡」suggestions", "type": "forum", "topic": "Share your ideas and suggestions for the server"},
    {"name": "「🐛」bug-reports", "type": "forum", "topic": "Report bugs or issues you find"},
    {"name": "「⭐」reviews", "type": "forum", "topic": "Leave your reviews and feedback"}
]

# ── Prompt + Role Panel Helpers ───────────────────────────────────────────────
BASE_SYSTEM_PROMPT = """
You are a Discord server architect. Convert the user's request into a JSON server template.

Return ONLY valid JSON, no explanation, no markdown, no code blocks. Just raw JSON.

Use this exact structure:
{
  "server_name": "Server Name Here",
  "roles": [
    {
      "name": "Admin",
      "color": "0xHEXCOLOR",
      "mentionable": false,
      "type": "admin"
    },
    {
      "name": "Moderator",
      "color": "0xHEXCOLOR",
      "mentionable": true,
      "type": "moderator"
    },
    {
      "name": "Member",
      "color": "0xHEXCOLOR",
      "mentionable": false,
      "type": "member"
    },
    {
      "name": "Gamer",
      "color": "0xHEXCOLOR",
      "mentionable": false,
      "type": "decorative"
    },
    {
      "name": "Night Owl",
      "color": "0xHEXCOLOR",
      "mentionable": false,
      "type": "decorative"
    }
  ],
  "categories": [
    {
      "name": "General",
      "channels": [
        {"name": "welcome", "type": "text", "topic": "Channel description"},
        {"name": "voice-lounge", "type": "voice"}
      ]
    }
  ],
  "roles_channel": "get-your-roles"
}

Rules:
- Channel names must be lowercase with hyphens after any emoji/decoration
- Include 2-4 categories based on the theme
- Include 3-5 channels per category
- Always include a General category
- Always include a welcome channel in the General category as the first channel
- Always include a text channel named "general" (general member chat) immediately after welcome in the General category
- Always include a text channel named "bot-commands" (for public bot command usage by members) in the General category
- Never include an announcements channel in any category — announcements are handled separately via !addcommunity
- Always include at least one voice channel per category
- Always include exactly these role types: admin, moderator, member
- Add 3-5 decorative roles that match the server theme (type: decorative)
- These decorative roles appear in the self-roles channel for users to pick
- Pick role colors that match the server theme
- Admin color should feel powerful (gold, red, etc)
- Moderator color should feel authoritative (blue, purple, etc)
- Member color should be neutral (grey, white, etc)
- Decorative roles should have fun vibrant colors
- roles_channel is always "get-your-roles"
- Always include a private staff category at the end called "staff-only" with these channels:
  {"name": "mod-logs", "type": "text", "topic": "Moderation logs and actions", "staff_only": true},
  {"name": "staff-chat", "type": "text", "topic": "Private staff discussion", "staff_only": true},
  {"name": "bot-commands", "type": "text", "topic": "Bot commands for staff", "staff_only": true},
  {"name": "staff-voice", "type": "voice", "staff_only": true}
- Mark all channels in this category with "staff_only": true
- Always include these pastel color roles at the end of the roles list with type "color":
  {"name": "🌸 Pink", "color": "0xFFB7C5", "type": "color"},
  {"name": "🍋 Yellow", "color": "0xFDFD96", "type": "color"},
  {"name": "🍵 Mint", "color": "0x98FF98", "type": "color"},
  {"name": "🩵 Blue", "color": "0xAEC6CF", "type": "color"},
  {"name": "🍇 Lavender", "color": "0xE6E6FA", "type": "color"},
  {"name": "🍑 Peach", "color": "0xFFCBA4", "type": "color"},
  {"name": "🤍 White", "color": "0xFFFAFA", "type": "color"}
"""

PASTEL_COLOR_EMOJIS = ["🌸", "🍋", "🍵", "🩵", "🍇", "🍑", "🤍"]
CORE_ROLE_NAMES = {"admin", "administrator", "moderator", "mod", "member", "@everyone"}
SELF_ROLE_EXCLUDE_KEYWORDS = ["prestige", "staff", "owner"]


def normalize_decoration_mode(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"none", "no", "off", "plain"}:
        return "none"
    if text in {"minimal", "min", "simple"}:
        return "minimal"
    return "full"


def build_system_prompt(decoration_mode: str = "full") -> str:
    mode = normalize_decoration_mode(decoration_mode)
    decoration_rules = {
        "none": (
            "- Use plain category/channel names with no decorative borders and no emoji prefixes.\n"
            "- Keep all names simple and readable while staying lowercase with hyphens for channels.\n"
            "- Keep the staff category name plain: \"staff-only\"."
        ),
        "minimal": (
            "- Keep category names plain (no decorative borders).\n"
            "- Use minimal decoration on channels: short emoji prefixes only where it improves clarity.\n"
            "- Keep the staff category clear and simple, e.g. \"staff-only\"."
        ),
        "full": (
            "- Category names should have decorative borders/emojis that match the server theme.\n"
            "- Text channel names should have a relevant emoji prefix like 「🎮」or 📚・.\n"
            "- Voice channel names should start with 🔊・.\n"
            "- The welcome channel may be decorated (example: 「👋」welcome)."
        ),
    }[mode]
    return f"{BASE_SYSTEM_PROMPT}\n{decoration_rules}\n"


def is_color_role_name(role_name: str) -> bool:
    return any((role_name or "").startswith(emoji) for emoji in PASTEL_COLOR_EMOJIS)


def role_creation_priority(role_data: dict) -> int:
    # User-requested behavior: create color roles before every other role type.
    role_type = (role_data or {}).get("type", "decorative")
    priorities = {
        "color": 0,
        "admin": 1,
        "moderator": 2,
        "member": 3,
        "decorative": 4,
    }
    return priorities.get(role_type, 5)


def is_self_assignable_candidate(role: discord.Role) -> bool:
    if role.managed or role.name.lower() in CORE_ROLE_NAMES:
        return False

    lowered = role.name.lower()
    if any(word in lowered for word in SELF_ROLE_EXCLUDE_KEYWORDS):
        return False

    perms = role.permissions
    elevated = (
        perms.administrator
        or perms.manage_guild
        or perms.manage_channels
        or perms.manage_roles
        or perms.kick_members
        or perms.ban_members
        or perms.moderate_members
    )
    return not elevated


async def enforce_color_role_priority(guild: discord.Guild, color_roles: list | None = None):
    try:
        bot_member = guild.me
        if not bot_member or bot_member.top_role.position <= 1:
            return

        detected_color_roles = [
            {"name": role.name, "id": role.id}
            for role in guild.roles
            if is_color_role_name(role.name) and not role.managed
        ]

        if color_roles is None:
            data = await db_load(str(guild.id))
            color_roles = data.get("color_roles", [])

        # Merge DB-tracked and detected color roles so every color role stays on top.
        merged = {}
        for entry in color_roles:
            role_id = int(entry.get("id") if isinstance(entry, dict) else entry)
            role = guild.get_role(role_id)
            if role:
                merged[role.id] = {"name": role.name, "id": role.id}
        for entry in detected_color_roles:
            merged[entry["id"]] = entry
        color_roles = list(merged.values())

        movable_roles = []
        for entry in color_roles:
            role_id = entry.get("id") if isinstance(entry, dict) else entry
            role = guild.get_role(int(role_id))
            if role and not role.managed and role.position < bot_member.top_role.position:
                movable_roles.append(role)

        if not movable_roles:
            return

        start_position = bot_member.top_role.position - 1
        positions = {}
        for idx, role in enumerate(sorted(movable_roles, key=lambda r: r.position, reverse=True)):
            target = start_position - idx
            if target > 0 and role.position != target:
                positions[role] = target

        if positions:
            await guild.edit_role_positions(positions)
    except Exception as e:
        print(f"⚠️ Could not reorder color roles in {guild.name}: {e}")


async def register_self_role(guild: discord.Guild, role: discord.Role) -> bool:
    if not is_self_assignable_candidate(role):
        return False

    data = await db_load(str(guild.id))
    bucket = "color_roles" if is_color_role_name(role.name) else "decorative_roles"
    current = data.get(bucket, [])
    if any(int(r.get("id", 0)) == role.id for r in current):
        return False

    current.append({"name": role.name, "id": role.id})
    await db_save(str(guild.id), {bucket: current})
    return True


async def sync_roles_panel(guild: discord.Guild, force_recreate: bool = False):
    guild_id = str(guild.id)
    data = await db_load(guild_id)

    roles_channel = None
    channel_id = data.get("roles_channel_id")
    if channel_id:
        roles_channel = guild.get_channel(int(channel_id))
    if not roles_channel:
        roles_channel_name = data.get("roles_channel_name") or data.get("roles_channel") or "get-your-roles"
        roles_channel = discord.utils.get(guild.text_channels, name=roles_channel_name)
    if not roles_channel:
        return False, "roles channel not found"

    decorative_roles = []
    for entry in data.get("decorative_roles", []):
        role = guild.get_role(int(entry.get("id", 0)))
        if role:
            decorative_roles.append({"name": role.name, "id": role.id})

    color_roles = []
    for entry in data.get("color_roles", []):
        role = guild.get_role(int(entry.get("id", 0)))
        if role:
            color_roles.append({"name": role.name, "id": role.id})

    await enforce_color_role_priority(guild, color_roles)

    if force_recreate:
        try:
            await roles_channel.purge(limit=25)
        except Exception:
            pass

    identity_content = (
        "**🎭 Identity Roles**\n\nPick what describes you!\nClick to get or remove a role!"
        if decorative_roles
        else "**🎭 Identity Roles**\n\nNo identity roles available yet."
    )
    color_content = (
        "**🎨 Color Roles**\n\nPick your color! Choosing a new one removes the old one automatically!"
        if color_roles
        else "**🎨 Color Roles**\n\nNo color roles available yet."
    )

    identity_view = RoleView(decorative_roles, [])
    color_view = RoleView([], color_roles)

    identity_msg = None
    color_msg = None
    if not force_recreate:
        identity_msg_id = data.get("roles_identity_message_id")
        color_msg_id = data.get("roles_color_message_id")
        if identity_msg_id:
            try:
                identity_msg = await roles_channel.fetch_message(int(identity_msg_id))
            except Exception:
                identity_msg = None
        if color_msg_id:
            try:
                color_msg = await roles_channel.fetch_message(int(color_msg_id))
            except Exception:
                color_msg = None

    if identity_msg:
        await identity_msg.edit(content=identity_content, view=identity_view)
    else:
        identity_msg = await roles_channel.send(content=identity_content, view=identity_view)

    if color_msg:
        await color_msg.edit(content=color_content, view=color_view)
    else:
        color_msg = await roles_channel.send(content=color_content, view=color_view)

    await db_save(guild_id, {
        "roles_channel_id": roles_channel.id,
        "roles_channel_name": roles_channel.name,
        "decorative_roles": decorative_roles,
        "color_roles": color_roles,
        "roles_identity_message_id": identity_msg.id,
        "roles_color_message_id": color_msg.id
    })
    bot.decorative_roles = decorative_roles
    bot.color_roles = color_roles
    return True, None

BLOCKED_WORDS = [
    "nigger", "nigga", "faggot", "fag", "retard", "retarded",
    "chink", "spic", "kike", "tranny", "dyke", "cunt",
    "wetback", "raghead", "sandnigger", "gook", "beaner",
    "cracker", "whitey", "towelhead", "zipperhead"
]

def extract_json(text):
    """Extract JSON even if the LLM adds extra text around it"""
    # Try direct parse first
    try:
        return json.loads(text)
    except:
        pass
    # Try extracting from code blocks
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except:
            pass
    return None


class SetupModal(discord.ui.Modal, title="🏗️ Server Setup"):
    def __init__(self, template_key: str, template_data: dict):
        super().__init__()
        self.template_key = template_key
        self.template_data = template_data

    server_name = discord.ui.TextInput(
        label="Server Name (optional)",
        placeholder="Leave blank to let AI decide...",
        required=False,
        max_length=100
    )

    extra_details = discord.ui.TextInput(
        label="Extra Details (optional)",
        placeholder="e.g. we play Valorant and Minecraft...",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph
    )

    add_tickets = discord.ui.TextInput(
        label="Add Ticket System? (yes/no)",
        placeholder="yes or no",
        required=False,
        max_length=3,
        default="yes"
    )

    add_stats = discord.ui.TextInput(
        label="Add Server Stats? (yes/no)",
        placeholder="yes or no",
        required=False,
        max_length=3,
        default="yes"
    )

    decoration_style = discord.ui.TextInput(
        label="Decoration Style (none/minimal/full)",
        placeholder="full",
        required=False,
        max_length=8,
        default="full"
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

        template_hint = self.template_data.get("hint", "general server")
        custom_name = self.server_name.value.strip()
        extras = self.extra_details.value.strip()
        wants_tickets = self.add_tickets.value.strip().lower() != "no"
        wants_stats = self.add_stats.value.strip().lower() != "no"
        decoration_mode = normalize_decoration_mode(self.decoration_style.value)

        user_input = template_hint or "a general purpose server"
        if custom_name:
            user_input += f". Server name should be: {custom_name}"
        if extras:
            user_input += f". Extra details: {extras}"
        user_input += f". Decoration style preference: {decoration_mode}"

        bot.setup_wants_tickets = wants_tickets
        bot.setup_wants_stats = wants_stats
        bot.setup_decoration_mode = decoration_mode
        bot.selected_template = self.template_key

        ctx_like = await interaction.channel.send("🧠 Generating your server plan...")

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": build_system_prompt(decoration_mode)},
                    {"role": "user", "content": user_input}
                ]
            )
            raw_text = response.choices[0].message.content
            server_template = extract_json(raw_text)

            if not server_template:
                await ctx_like.edit(content="❌ Couldn't parse AI response. Try again!")
                return

            categories_summary = ""
            for cat in server_template.get("categories", []):
                channels = cat.get("channels", [])
                categories_summary += f"\n**{cat['name']}**\n"
                for i, ch in enumerate(channels):
                    is_last = i == len(channels) - 1
                    prefix = "┗" if is_last else "┣"
                    icon = "🔊" if ch["type"] == "voice" else "💬"
                    categories_summary += f"{prefix} {icon} {ch['name']}\n"

            if wants_tickets:
                categories_summary += "\n**🎟️ TICKETS** *(will be added)*\n┗ 💬 ticket system\n"
            if wants_stats:
                categories_summary += "\n**📊 SERVER STATS** *(will be added)*\n┗ 📈 live stats channels\n"
            # INFO is always added for every template
            categories_summary += "\n**📌 INFO** *(always added)*\n┗ 📜 rules (react ✅ to get Member role)\n"
            if self.template_key == 'community':
                categories_summary += "\n**📋 COMMUNITY** *(added)*\n┗ 📢 announcements, forums\n"

            all_roles = server_template.get("roles", [])
            staff_roles = [r["name"] for r in all_roles if r.get("type") in ["admin", "moderator", "member"]]
            decorative_roles = [r["name"] for r in all_roles if r.get("type") == "decorative"]
            color_roles = [r["name"] for r in all_roles if r.get("type") == "color"]

            embed = discord.Embed(
                title=f"🏗️ {server_template.get('server_name', 'Your Server')} — Ready to Build!",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="📁 Channels & Categories",
                value=categories_summary[:1024] if categories_summary else "None",
                inline=False
            )
            embed.add_field(
                name="👑 Staff Roles",
                value=", ".join(staff_roles) if staff_roles else "None",
                inline=True
            )
            embed.add_field(
                name="🎭 Identity Roles",
                value=", ".join(decorative_roles) if decorative_roles else "None",
                inline=True
            )
            embed.add_field(
                name="🎨 Color Roles",
                value=", ".join(color_roles) if color_roles else "None",
                inline=True
            )
            embed.add_field(name="✨ Decoration", value=decoration_mode.title(), inline=True)

            bot.pending_template = server_template

            await ctx_like.edit(
                content=None,
                embed=embed,
                view=ConfirmBuildView()
            )

        except Exception as e:
            await ctx_like.edit(content=f"❌ Error: {str(e)}")


class ConfirmBuildView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Build Server", style=discord.ButtonStyle.green, custom_id="confirm_build")
    async def confirm_build(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        ctx.guild = interaction.guild
        ctx.channel = interaction.channel
        await ctx.invoke(bot.get_command('confirm'))

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red, custom_id="cancel_build")
    async def cancel_build(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot.pending_template = None
        embed = discord.Embed(
            title="❌ Cancelled",
            description="No changes were made. Run `!setup` again whenever you're ready!",
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=embed, view=None)


class TemplateButton(discord.ui.Button):
    def __init__(self, key: str, data: dict):
        super().__init__(
            label=f"{data['emoji']} {data['label']}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"template_btn_{key}"
        )
        self.key = key
        self.data = data

    async def callback(self, interaction: discord.Interaction):
        if self.key == "custom":
            modal = SetupModal("custom", {"hint": None})
            modal.title = "🎲 Custom Server Setup"
        else:
            modal = SetupModal(self.key, self.data)
            modal.title = f"{self.data['emoji']} {self.data['label']} Server Setup"
        await interaction.response.send_modal(modal)

class TemplateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for key, data in SERVER_TEMPLATES.items():
            self.add_item(TemplateButton(key, data))

class RoleButton(discord.ui.Button):
    def __init__(self, role_name: str, role_id: int,
                 style=discord.ButtonStyle.primary):
        super().__init__(
            label=role_name,
            style=style,
            custom_id=f"role_{role_id}"
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        try:
            guild = interaction.guild
            role = guild.get_role(self.role_id)
            member = interaction.user

            if role is None:
                await interaction.response.send_message(
                    "❌ This role no longer exists! Ask an admin to run `!refreshroles`",
                    ephemeral=True
                )
                return

            is_color_role = is_color_role_name(role.name)

            if role in member.roles:
                await member.remove_roles(role)
                await interaction.response.send_message(
                    f"✅ Removed **{role.name}** from you!",
                    ephemeral=True
                )
            else:
                if is_color_role:
                    color_roles_to_remove = [
                        r for r in member.roles
                        if is_color_role_name(r.name)
                    ]
                    if color_roles_to_remove:
                        await member.remove_roles(*color_roles_to_remove)
                await member.add_roles(role)
                if is_color_role:
                    await enforce_color_role_priority(guild)
                await interaction.response.send_message(
                    f"🎉 You now have **{role.name}**!",
                    ephemeral=True
                )
        except Exception as e:
            try:
                await interaction.response.send_message(
                    f"❌ Something went wrong: {str(e)}",
                    ephemeral=True
                )
            except:
                pass

class RoleView(discord.ui.View):
    def __init__(self, roles: list = [], color_roles: list = []):
        super().__init__(timeout=None)
        for role in roles:
            self.add_item(RoleButton(
                role["name"],
                role["id"],
                discord.ButtonStyle.primary
            ))
        for role in color_roles:
            self.add_item(RoleButton(
                role["name"],
                role["id"],
                discord.ButtonStyle.secondary
            ))

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

    # Initialize cooldowns first before anything else
    bot.xp_cooldowns = {}
    bot.spam_tracker = {}
    bot.automod_cache = {}
    bot.rules_cache = {}

    state = load_state()

    # Register views safely one by one
    views_to_register = [
        TemplateView(),
        TicketOpenView(),
        TicketCloseView(),
        TicketConfirmCloseView(),
        GuideView(),
        ConfirmBuildView(),
        AnnounceCardView()
    ]
    for view in views_to_register:
        try:
            bot.add_view(view)
        except Exception as e:
            print(f"⚠️ Could not register view {view.__class__.__name__}: {e}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="!guide for commands"
        )
    )

    if "member_role_id" in state:
        bot.member_role_id = state["member_role_id"]
        print(f"✅ Loaded member role ID: {bot.member_role_id}")
    if "stats_category_id" in state and state["stats_category_id"]:
        bot.stats_category_id = state["stats_category_id"]
        print(f"✅ Loaded stats category ID: {bot.stats_category_id}")

    auto_update_stats.start()
    print("✅ All systems ready!")

@bot.event
async def on_guild_available(guild):
    try:
        data = await db_load(str(guild.id))
        decorative_roles = data.get("decorative_roles", [])
        color_roles = data.get("color_roles", [])

        if decorative_roles or color_roles:
            bot.add_view(RoleView(decorative_roles, []))
            bot.add_view(RoleView([], color_roles))
            print(f"✅ Loaded role views for {guild.name}")

        member_role_id = data.get("member_role_id")
        if member_role_id:
            bot.member_role_id = member_role_id

        # Cache automod config
        automod_config = data.get("automod", {})
        if automod_config:
            bot.automod_cache[str(guild.id)] = automod_config

        # Cache rules message ID for reaction-role
        rules_msg_id = data.get("rules_message_id")
        if rules_msg_id:
            bot.rules_cache[str(guild.id)] = {"message_id": int(rules_msg_id)}

        # Load decoration mode used during setup (so !edit matches the server style)
        decoration_mode = data.get("decoration_mode")
        if decoration_mode:
            bot.setup_decoration_mode = decoration_mode

        await enforce_color_role_priority(guild, color_roles)

    except Exception as e:
        print(f"⚠️ Could not load data for {guild.name}: {e}")


@bot.event
async def on_guild_role_create(role: discord.Role):
    try:
        if getattr(bot, "build_in_progress", False):
            return

        # Any newly created role can shift hierarchy; re-assert color roles on top.
        await enforce_color_role_priority(role.guild)

        if is_self_assignable_candidate(role):
            data = await db_load(str(role.guild.id))
            roles_channel_name = data.get("roles_channel_name") or data.get("roles_channel") or "get-your-roles"
            roles_channel = role.guild.get_channel(int(data.get("roles_channel_id", 0))) if data.get("roles_channel_id") else None
            if not roles_channel:
                roles_channel = discord.utils.get(role.guild.text_channels, name=roles_channel_name)
            if not roles_channel:
                return

            changed = await register_self_role(role.guild, role)
            if changed:
                await sync_roles_panel(role.guild)
    except Exception as e:
        print(f"⚠️ Role create sync failed in {role.guild.name}: {e}")


@bot.event
async def on_guild_role_delete(role: discord.Role):
    try:
        data = await db_load(str(role.guild.id))
        decorative_roles = [r for r in data.get("decorative_roles", []) if int(r.get("id", 0)) != role.id]
        color_roles = [r for r in data.get("color_roles", []) if int(r.get("id", 0)) != role.id]
        if decorative_roles == data.get("decorative_roles", []) and color_roles == data.get("color_roles", []):
            return
        await db_save(str(role.guild.id), {
            "decorative_roles": decorative_roles,
            "color_roles": color_roles
        })
        await sync_roles_panel(role.guild)
    except Exception as e:
        print(f"⚠️ Role delete sync failed in {role.guild.name}: {e}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """Grant Member role when a user reacts ✅ on the rules message."""
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != "✅":
        return
    if not payload.guild_id:
        return

    guild_id = str(payload.guild_id)
    cached = bot.rules_cache.get(guild_id)
    if not cached:
        # Try loading from DB on cache miss (edge case after partial startup)
        try:
            data = await db_load(guild_id)
            rules_msg_id = data.get("rules_message_id")
            if rules_msg_id:
                cached = {"message_id": int(rules_msg_id)}
                bot.rules_cache[guild_id] = cached
        except Exception:
            return
    if not cached:
        return
    if payload.message_id != cached.get("message_id"):
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member or member.bot:
        return

    try:
        data = await db_load(guild_id)
        member_role_id = data.get("member_role_id")
        if not member_role_id:
            return
        role = guild.get_role(int(member_role_id))
        if not role or role in member.roles:
            return
        await member.add_roles(role, reason="Accepted server rules")
    except Exception as e:
        print(f"⚠️ Could not grant Member role to {member}: {e}")


@bot.command()
async def hello(ctx):
    await ctx.send("Hey! I'm Architect AI. Ready to build your server 🏗️")

@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! Latency: {round(bot.latency * 1000)}ms")

async def generate_server_plan(ctx, user_input: str):
    thinking_msg = await ctx.send("🧠 Generating your server plan...")

    try:
        response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                {"role": "system", "content": build_system_prompt("full")},
                {"role": "user", "content": user_input}
            ]
        )
        raw_text = response.choices[0].message.content
        server_template = extract_json(raw_text)

        if not server_template:
            await thinking_msg.edit(content="❌ Couldn't parse the AI response. Try again!")
            return

        # Build a clean human-readable summary instead of raw JSON
        categories_summary = ""
        for cat in server_template.get("categories", []):
            channels = cat.get("channels", [])
            categories_summary += f"\n**{cat['name']}**\n"
            for i, ch in enumerate(channels):
                is_last = i == len(channels) - 1
                prefix = "┗" if is_last else "┣"
                icon = "🔊" if ch["type"] == "voice" else "💬"
                categories_summary += f"{prefix} {icon} {ch['name']}\n"

        # INFO is always added for every template
        categories_summary += "\n**📌 INFO** *(always added)*\n┗ 📜 rules (react ✅ to get Member role)\n"

        all_roles = server_template.get("roles", [])
        staff_roles = [r["name"] for r in all_roles if r.get("type") in ["admin", "moderator", "member"]]
        decorative_roles = [r["name"] for r in all_roles if r.get("type") == "decorative"]
        color_roles = [r["name"] for r in all_roles if r.get("type") == "color"]

        embed = discord.Embed(
            title=f"🏗️ {server_template.get('server_name', 'Your Server')} — Ready to Build!",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="📁 Channels & Categories",
            value=categories_summary[:1024] if categories_summary else "None",
            inline=False
        )
        embed.add_field(
            name="👑 Staff Roles",
            value=", ".join(staff_roles) if staff_roles else "None",
            inline=True
        )
        embed.add_field(
            name="🎭 Identity Roles",
            value=", ".join(decorative_roles) if decorative_roles else "None",
            inline=True
        )
        embed.add_field(
            name="🎨 Color Roles",
            value=", ".join(color_roles) if color_roles else "None",
            inline=True
        )
        embed.set_footer(text="Type !confirm to build it • !cancel to scrap it")
        await thinking_msg.edit(content=None, embed=embed)

        bot.pending_template = server_template
        bot.selected_template = None
        bot.pending_ctx = ctx

    except Exception as e:
        await thinking_msg.edit(content=f"❌ Error: {str(e)}")

@bot.command()
@owner_only()
async def setup(ctx):
    embed = discord.Embed(
        title="🏗️ Architect AI — Choose a Template",
        description="Pick a theme to get started! A setup popup will appear for customization.",
        color=discord.Color.blurple()
    )
    for key, data in SERVER_TEMPLATES.items():
        embed.add_field(
            name=f"{data['emoji']} {data['label']}",
            value=data['description'],
            inline=True
        )
    embed.set_footer(text="Click a button below to select your template")
    await ctx.send(embed=embed, view=TemplateView())

@bot.command()
@owner_only()
async def cancel(ctx):
    bot.pending_template = None
    embed = discord.Embed(
            title="❌ Cancelled",
            description="No changes were made. Run `!setup` again whenever you're ready!",
            color=discord.Color.orange()
        )
    await ctx.send(embed=embed)

@bot.command()
@owner_only()
async def confirm(ctx):
    if not hasattr(bot, 'pending_template') or bot.pending_template is None:
        await ctx.send("❌ No pending server plan! Run `!setup` first.")
        return

    template = bot.pending_template
    guild = ctx.guild

    progress_msg = await ctx.send("🏗️ Building your server... please wait!")
    bot.build_in_progress = True

    try:
        created = {
            "roles": [],
            "categories": [],
            "channels": []
        }

        # Define permissions for each role type
        admin_perms = discord.Permissions(administrator=True)

        moderator_perms = discord.Permissions(
            kick_members=True,
            ban_members=True,
            manage_messages=True,
            manage_channels=False,
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            mention_everyone=True,
            mute_members=True,
            deafen_members=True,
            move_members=True
        )

        member_perms = discord.Permissions(
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            connect=True,
            speak=True,
            add_reactions=True,
            use_application_commands=True
        )

        decorative_perms = discord.Permissions(
            read_messages=True,
            send_messages=True,
            read_message_history=True,
            connect=True,
            speak=True
        )

        # Step 1 — Create Roles with permissions
        await progress_msg.edit(content="🎨 Creating roles and permissions...")
        role_objects = {}

        ordered_roles = sorted(template.get("roles", []), key=role_creation_priority)
        for role_data in ordered_roles:
            color_value = int(role_data.get("color", "0x3498db"), 16)
            role_type = role_data.get("type", "decorative")

            if role_type == "admin":
                perms = admin_perms
                hoist = True
            elif role_type == "moderator":
                perms = moderator_perms
                hoist = True
            elif role_type == "member":
                perms = member_perms
                hoist = True
            else:
                perms = decorative_perms
                hoist = False

            # Color roles need hoist=False but correct perms
            if role_type == "color":
                hoist = False
                perms = decorative_perms

            role = await guild.create_role(
                name=role_data["name"],
                color=discord.Color(color_value),
                mentionable=role_data.get("mentionable", False),
                permissions=perms,
                hoist=hoist
            )
            role_objects[role_data["name"]] = role
            created["roles"].append(role.id)
            await asyncio.sleep(0.5)

        await enforce_color_role_priority(guild, [
            {"name": r["name"], "id": role_objects[r["name"]].id}
            for r in template.get("roles", [])
            if r.get("type") == "color" and r["name"] in role_objects
        ])

        # Step 2 — Give creator Admin, everyone else Member
        await progress_msg.edit(content="👑 Assigning roles to members...")

        admin_role = next(
            (r for name, r in role_objects.items()
             if template.get("roles") and
             next((rd for rd in template["roles"] if rd["name"] == name and rd.get("type") == "admin"), None)),
            None
        )
        member_role = next(
            (r for name, r in role_objects.items()
             if template.get("roles") and
             next((rd for rd in template["roles"] if rd["name"] == name and rd.get("type") == "member"), None)),
            None
        )
        mod_role = next(
            (r for name, r in role_objects.items()
             if template.get("roles") and
             next((rd for rd in template["roles"] if rd["name"] == name and rd.get("type") == "moderator"), None)),
            None
        )

        # Give creator Admin
        if admin_role:
            await ctx.author.add_roles(admin_role)

        # Save member role id (used for reaction-role gate and servers without rules channel)
        bot.member_role_id = member_role.id if member_role else None
        if member_role:
            save_state({"member_role_id": member_role.id})

        # Step 3 — Create Categories and Channels
        await progress_msg.edit(content="📁 Creating categories and channels...")
        for category_data in template.get("categories", []):
            category = await guild.create_category(category_data["name"])
            created["categories"].append(category.id)

            # If staff only category, hide it from everyone except admin and mod
            is_staff_category = "STAFF" in category_data["name"].upper()
            if is_staff_category:
                # Hide from @everyone
                await category.set_permissions(guild.default_role, read_messages=False)
                # Show to admin and mod roles
                for r in template.get("roles", []):
                    if r.get("type") in ["admin", "moderator"] and r["name"] in role_objects:
                        await category.set_permissions(
                            role_objects[r["name"]],
                            read_messages=True,
                            send_messages=True
                        )
                # Always grant bot full access to the staff category so mod-logs work
                await category.set_permissions(
                    guild.me,
                    read_messages=True,
                    send_messages=True,
                    manage_messages=True,
                    embed_links=True,
                    attach_files=True,
                    read_message_history=True,
                    manage_channels=True,
                )

            for channel_data in category_data.get("channels", []):
                if channel_data["type"] == "text":
                    channel = await guild.create_text_channel(
                        name=channel_data["name"],
                        category=category,
                        topic=channel_data.get("topic", "")
                    )
                elif channel_data["type"] == "voice":
                    channel = await guild.create_voice_channel(
                        name=channel_data["name"],
                        category=category
                    )
                created["channels"].append(channel.id)
                await asyncio.sleep(0.5)

        # Ensure General category always has #general and #bot-commands
        await ensure_general_channels(guild, created)

        # Step 4 — Create info category (always, all templates) then place get-your-roles inside it
        await progress_msg.edit(content="🏗️ Setting up info & roles channels...")
        is_community_template = getattr(bot, 'selected_template', '') == 'community'
        # INFO category always created for all templates — includes rules channel
        anchor_category = await create_info_category(guild, role_objects, template, created)
        # COMMUNITY category additionally created for the community template
        if is_community_template:
            await create_community_channels(guild, role_objects, template, created)

        # Step 4b — Lock server: hide everything from @everyone, give Member access
        # @everyone can only see INFO (rules gate). Once they get Member they see the rest.
        await progress_msg.edit(content="🔒 Applying permission gates...")
        await asyncio.sleep(0.5)
        # Hide @everyone from ALL categories/channels server-wide
        await guild.edit(default_notifications=discord.NotificationLevel.only_mentions)
        _bot_full = discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            manage_messages=True,
            manage_channels=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            add_reactions=True,
            manage_threads=True,
            create_public_threads=True
        )
        for cat in guild.categories:
            await cat.set_permissions(guild.default_role, read_messages=False)
            await cat.set_permissions(guild.me, overwrite=_bot_full)
            await asyncio.sleep(0.2)
        # Also set bot overwrite on every individual channel so nothing slips through
        for ch in guild.channels:
            if not isinstance(ch, discord.CategoryChannel):
                await ch.set_permissions(guild.me, overwrite=_bot_full)
                await asyncio.sleep(0.1)
        # Grant Member role read+write access to all non-INFO, non-staff categories
        if member_role:
            for cat in guild.categories:
                if cat.name == "📌 INFO":
                    # INFO is already read-only for Member via create_info_category
                    continue
                if "STAFF" in cat.name.upper():
                    # Staff categories stay hidden from Member
                    continue
                await cat.set_permissions(
                    member_role,
                    read_messages=True,
                    send_messages=True,
                    read_message_history=True,
                    add_reactions=True
                )
                await asyncio.sleep(0.2)

        _info_read_only = discord.PermissionOverwrite(
            read_messages=True,
            send_messages=False,
            create_public_threads=False,
            create_private_threads=False,
            send_messages_in_threads=False,
            add_reactions=False
        )
        _info_staff = discord.PermissionOverwrite(
            read_messages=True, send_messages=True,
            manage_messages=True, manage_threads=True
        )
        _info_overwrites = {guild.default_role: _info_read_only, guild.me: _info_staff}
        if member_role:
            _info_overwrites[member_role] = _info_read_only
        if admin_role:
            _info_overwrites[admin_role] = _info_staff
        if mod_role:
            _info_overwrites[mod_role] = _info_staff

        roles_channel = await guild.create_text_channel(
            name=template.get("roles_channel", "get-your-roles"),
            category=anchor_category,
            overwrites=_info_overwrites
        )
        created["channels"].append(roles_channel.id)

        decorative_roles = [
            {"name": r["name"], "id": role_objects[r["name"]].id}
            for r in template.get("roles", [])
            if r.get("type") == "decorative" and r["name"] in role_objects
        ]

        color_roles = [
            {"name": r["name"], "id": role_objects[r["name"]].id}
            for r in template.get("roles", [])
            if r.get("type") == "color" and r["name"] in role_objects
        ]

        # Save role data first, then build/update role panel messages.
        await db_save(str(ctx.guild.id), {
            "roles_channel": roles_channel.name,
            "roles_channel_name": roles_channel.name,
            "roles_channel_id": roles_channel.id,
            "decorative_roles": decorative_roles,
            "color_roles": color_roles,
            "member_role_id": member_role.id if member_role else None
        })
        await sync_roles_panel(guild, force_recreate=True)
        bot.decorative_roles = decorative_roles
        bot.color_roles = color_roles

        # Step 5 — Save state for undo
        bot.last_build = created
        bot.last_template = template
        bot.pending_template = None
        # Save last build to MongoDB
        await db_save(str(ctx.guild.id), {
            "last_build": {
                "roles": created["roles"],
                "categories": created["categories"],
                "channels": created["channels"]
            },
            "last_template": template,
            "decoration_mode": getattr(bot, 'setup_decoration_mode', 'full')
        })

        embed = discord.Embed(
            title="✅ Server Built Successfully!",
            description="Your server is ready to go!",
            color=discord.Color.green()
        )
        embed.add_field(name="🎨 Roles Created", value=str(len(created['roles'])), inline=True)
        embed.add_field(name="📁 Categories Created", value=str(len(created['categories'])), inline=True)
        embed.add_field(name="💬 Channels Created", value=str(len(created['channels'])), inline=True)
        embed.add_field(name="👑 Your Role", value="Admin — you have full control!", inline=False)
        embed.add_field(
            name="✏️ Want to make changes?",
            value="Use `!edit` to open the editor!\nAdd channels, categories, roles, change colors, edit topics and more!",
            inline=False
        )
        embed.set_footer(text="Type !undo to revert everything • !guide to see all commands")
        await progress_msg.edit(content=None, embed=embed)

        # Auto setup ticket system if requested
        if getattr(bot, 'setup_wants_tickets', True):
            try:
                existing_ticket = discord.utils.get(ctx.guild.text_channels, name="「🎫」tickets")
                if not existing_ticket:
                    ticket_channel = await ctx.guild.create_text_channel(
                        name="「🎫」tickets",
                        category=anchor_category,
                        overwrites=_info_overwrites
                    )
                    ticket_embed = discord.Embed(
                        title="🎫 Support Tickets",
                        description=(
                            "Need help? Have a question? Want to report something?\n\n"
                            "Click the button below to open a private ticket with staff!\n\n"
                            "📋 **Guidelines:**\n"
                            "• One ticket per issue\n"
                            "• Be respectful to staff\n"
                            "• Provide as much detail as possible\n"
                            "• Don't spam open/close tickets"
                        ),
                        color=discord.Color.blurple()
                    )
                    ticket_embed.set_footer(text="Tickets are logged for safety purposes")
                    await ticket_channel.send(embed=ticket_embed, view=TicketOpenView())
                    created["channels"].append(ticket_channel.id)
            except Exception as e:
                print(f"⚠️ Auto ticket setup error: {e}")

        # Auto setup server stats if requested
        if getattr(bot, 'setup_wants_stats', True):
            try:
                existing_stats = discord.utils.get(ctx.guild.categories, name="📊 SERVER STATS 📊")
                if not existing_stats:
                    stats_category = await ctx.guild.create_category(
                        name="📊 SERVER STATS 📊",
                        position=0
                    )
                    await stats_category.set_permissions(ctx.guild.default_role,
                        connect=False,
                        send_messages=False,
                        read_messages=True
                    )

                    total_members, total_bots, online_members, total_channels, total_roles = await compute_server_counts(ctx.guild)

                    stats = [
                        f"👥・ Members: {total_members}",
                        f"🟢・ Online: {online_members}",
                        f"🤖・ Bots: {total_bots}",
                        f"💬・ Channels: {total_channels}",
                        f"🎭・ Roles: {total_roles}",
                    ]

                    for stat in stats:
                        stat_channel = await ctx.guild.create_voice_channel(
                            name=stat,
                            category=stats_category
                        )
                        await stat_channel.set_permissions(ctx.guild.default_role,
                            connect=False,
                            view_channel=True
                        )
                        created["channels"].append(stat_channel.id)

                    save_state({"stats_category_id": stats_category.id})
                    bot.stats_category_id = stats_category.id
                    created["categories"].append(stats_category.id)
            except Exception as e:
                print(f"⚠️ Auto stats setup error: {e}")

    except Exception as e:
        await progress_msg.edit(content=f"❌ Something went wrong: {str(e)}")
    finally:
        bot.build_in_progress = False

@bot.command()
@owner_only()
async def undo(ctx):
    if not hasattr(bot, 'last_build') or bot.last_build is None:
        # Try loading from MongoDB if not in memory
        data = await db_load(str(ctx.guild.id))
        if data.get("last_build"):
            bot.last_build = data["last_build"]
        else:
            await ctx.send("❌ Nothing to undo!")
            return

    progress_msg = await ctx.send("🗑️ Undoing everything... please wait!")
    guild = ctx.guild
    build = bot.last_build

    deleted = {"roles": 0, "categories": 0, "channels": 0}

    try:
        # Step 1 — Delete all channels first
        await progress_msg.edit(content="🗑️ Deleting channels...")
        for channel_id in build.get("channels", []):
            channel = guild.get_channel(channel_id)
            if channel:
                await channel.delete()
                deleted["channels"] += 1
                await asyncio.sleep(0.5)

        # Step 2 — Delete all categories
        await progress_msg.edit(content="🗑️ Deleting categories...")
        for category_id in build.get("categories", []):
            category = guild.get_channel(category_id)
            if category:
                await category.delete()
                deleted["categories"] += 1
                await asyncio.sleep(0.5)

        # Step 3 — Delete all roles
        await progress_msg.edit(content="🗑️ Deleting roles...")
        for role_id in build.get("roles", []):
            role = guild.get_role(role_id)
            if role:
                await role.delete()
                deleted["roles"] += 1
                await asyncio.sleep(0.5)

        # Also clean up stats category if it exists
        stats_category = discord.utils.get(guild.categories, name="📊 SERVER STATS 📊")
        if stats_category:
            for channel in stats_category.channels:
                await channel.delete()
                await asyncio.sleep(0.4)
            await stats_category.delete()
            save_state({"stats_category_id": None})
            bot.stats_category_id = None

        # Also clean up tickets category if it exists
        tickets_category = discord.utils.get(guild.categories, name="🎫 TICKETS")
        if tickets_category:
            for channel in tickets_category.channels:
                await channel.delete()
                await asyncio.sleep(0.4)
            await tickets_category.delete()

        # Also delete the tickets channel if it exists
        tickets_channel = discord.utils.get(guild.text_channels, name="「🎫」tickets")
        if tickets_channel:
            await tickets_channel.delete()

        # Clear the saved state
        bot.last_build = None

        embed = discord.Embed(
            title="🗑️ Undo Complete!",
            description="Server has been reverted to blank.",
            color=discord.Color.red()
        )
        embed.add_field(name="Roles Deleted", value=str(deleted['roles']), inline=True)
        embed.add_field(name="Categories Deleted", value=str(deleted['categories']), inline=True)
        embed.add_field(name="Channels Deleted", value=str(deleted['channels']), inline=True)
        embed.set_footer(text="Run !setup to start fresh!")
        await progress_msg.edit(content=None, embed=embed)

    except Exception as e:
        await progress_msg.edit(content=f"❌ Something went wrong: {str(e)}")

@bot.event
@bot.event
async def on_member_join(member):
    # Only auto-assign Member if this guild has no rules reaction-gate configured.
    # If a rules channel exists, they must react ✅ to earn the Member role.
    guild_id = str(member.guild.id)
    has_rules_gate = bool(bot.rules_cache.get(guild_id))
    if not has_rules_gate and hasattr(bot, 'member_role_id') and bot.member_role_id:
        member_role = member.guild.get_role(bot.member_role_id)
        if member_role:
            try:
                await member.add_roles(member_role)
            except:
                pass

    guild = member.guild

    # Find welcome channel
    welcome_channel = discord.utils.find(
        lambda c: "welcome" in c.name.lower(), guild.text_channels
    )
    if not welcome_channel:
        welcome_channel = guild.text_channels[0] if guild.text_channels else None
    if not welcome_channel:
        return

    embed = discord.Embed(
        title=f"👋 Welcome to {guild.name}!",
        description=(
            f"Hey {member.mention}, we're glad you're here!\n\n"
            f"🎭 Head to **#get-your-roles** to pick your roles.\n"
            f"💬 Introduce yourself and say hi!\n"
            f"💬 Check out the channels and make yourself at home!"
        ),
        color=discord.Color.blurple()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(
        name="Account Created",
        value=member.created_at.strftime("%b %d, %Y"),
        inline=True
    )
    embed.add_field(
        name="Member Count",
        value=f"You are member #{guild.member_count}!",
        inline=True
    )
    embed.set_footer(text=f"{guild.name} • Welcome aboard!")

    await welcome_channel.send(
        content=f"Everyone welcome {member.mention} to the server! 🎉",
        embed=embed
    )


@bot.event
async def on_member_remove(member):
    """Post a small leave message in the welcome channel."""
    guild = member.guild
    welcome_channel = discord.utils.find(
        lambda c: "welcome" in c.name.lower(), guild.text_channels
    )
    if not welcome_channel:
        return
    await welcome_channel.send(
        f"🚪 **{member.display_name}** has left the server. "
        f"We now have **{guild.member_count}** members."
    )

@bot.command()
async def build(ctx):
    if not hasattr(bot, 'selected_template') or bot.selected_template is None:
        await ctx.send("❌ Pick a template first with `!setup`!")
        return

    template_key = bot.selected_template
    template_data = SERVER_TEMPLATES[template_key]
    hint = template_data["hint"] or "a general purpose server"

    await generate_server_plan(ctx, hint)

@bot.command()
async def details(ctx, *, extra: str):
    if not hasattr(bot, 'selected_template') or bot.selected_template is None:
        await ctx.send("❌ Pick a template first with `!setup`!")
        return

    template_key = bot.selected_template
    template_data = SERVER_TEMPLATES[template_key]
    hint = f"{template_data['hint']} — extra details: {extra}"

    await generate_server_plan(ctx, hint)

@bot.command()
async def describe(ctx, *, description: str):
    await generate_server_plan(ctx, description)

async def parse_edit_instruction(instruction: str, guild: discord.Guild):
    # Build context about current server state
    categories = [f"{cat.name}: {[ch.name for ch in cat.channels]}" for cat in guild.categories]
    server_context = "\n".join(categories)

    edit_prompt = f"""
You are a Discord server editor. The user wants to make a change to their server.
Current server structure:
{server_context}

User instruction: {instruction}

Return ONLY valid JSON with exactly this structure depending on the action:

For adding a text channel:
{{"action": "add_channel", "type": "text", "name": "channel-name", "category": "EXACT CATEGORY NAME", "topic": "channel topic"}}

For adding a voice channel:
{{"action": "add_channel", "type": "voice", "name": "Channel Name", "category": "EXACT CATEGORY NAME"}}

For adding a category:
{{"action": "add_category", "name": "CATEGORY NAME"}}

For renaming a channel:
{{"action": "rename_channel", "old_name": "old-channel-name", "new_name": "new-channel-name"}}

For renaming a category:
{{"action": "rename_category", "old_name": "OLD CATEGORY NAME", "new_name": "NEW CATEGORY NAME"}}

For deleting a channel:
{{"action": "delete_channel", "name": "channel-name"}}

For deleting a category:
{{"action": "delete_category", "name": "CATEGORY NAME"}}

For adding a role:
{{"action": "add_role", "name": "Role Name", "color": "0xHEXCOLOR", "type": "decorative"}}

Rules:
- Use EXACT names from the current server structure shown above
- Channel names must be lowercase with hyphens
- Category names must match exactly as shown
- Return only one action at a time
"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": edit_prompt},
            {"role": "user", "content": instruction}
        ]
    )
    return extract_json(response.choices[0].message.content)

@bot.command()
@owner_only()
async def edit(ctx):
    embed = discord.Embed(
        title="✏️ Edit Server",
        description="What would you like to do?",
        color=discord.Color.blue()
    )
    embed.add_field(name="➕ Channel", value="Add a new text or voice channel", inline=True)
    embed.add_field(name="➕ Category", value="Create a new category", inline=True)
    embed.add_field(name="➕ Role", value="Create a new role with AI decoration", inline=True)
    embed.add_field(name="✏️ Rename", value="Rename a channel or category", inline=True)
    embed.add_field(name="🗑️ Delete", value="Delete channel, category or role", inline=True)
    embed.add_field(name="🎨 Role Color", value="Change any role's color", inline=True)
    embed.add_field(name="📝 Edit Topic", value="Edit a channel's description", inline=True)
    await ctx.send(embed=embed, view=EditMenuView(ctx.guild))

# ── MOD LOG HELPER ───────────────────────────────────────────────────────────────────────────────
def is_higher_role(moderator: discord.Member, target: discord.Member) -> bool:
    return moderator.top_role > target.top_role

async def log_mod_action(guild, action: str, moderator, target, reason: str = "No reason provided"):
    log_channel = discord.utils.get(guild.text_channels, name="「📋」mod-logs")
    if not log_channel:
        log_channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if not log_channel:
        return

    colors = {
        "KICK": discord.Color.orange(),
        "BAN": discord.Color.red(),
        "TIMEOUT": discord.Color.yellow(),
        "UNTIMEOUT": discord.Color.green(),
        "WARN": discord.Color.gold(),
        "PROMOTE": discord.Color.blue(),
        "DEMOTE": discord.Color.purple()
    }

    embed = discord.Embed(
        title=f"🔨 Mod Action — {action}",
        color=colors.get(action, discord.Color.greyple()),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Target", value=f"{target.mention} ({target.name})", inline=True)
    embed.add_field(name="Moderator", value=f"{moderator.mention} ({moderator.name})", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=f"User ID: {target.id}")
    try:
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        # Bot is missing send/embed permissions — try to fix and retry once
        try:
            await log_channel.set_permissions(
                guild.me,
                read_messages=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
            )
            await log_channel.send(embed=embed)
        except Exception as retry_err:
            print(f"⚠️ Cannot post to mod-logs in {guild.name}: {retry_err}")
    except Exception as e:
        print(f"⚠️ mod-log error in {guild.name}: {e}")


# ── MOD COMMANDS ──────────────────────────────────────────────────────────────────────────────

@bot.command()
@staff_only()
async def promote(ctx, member: discord.Member, *, role_type: str = "mod"):
    guild = ctx.guild
    role_type = role_type.lower().strip()

    if role_type in ["mod", "moderator"]:
        role = discord.utils.get(guild.roles, name="Moderator")
    elif role_type in ["admin", "administrator"]:
        role = discord.utils.get(guild.roles, name="Admin")
    else:
        role = discord.utils.get(guild.roles, name=role_type)

    if not role:
        await ctx.send(f"❌ Couldn't find a role matching **{role_type}**. Try `mod` or `admin`.")
        return

    if not is_higher_role(ctx.author, member):
        await ctx.send("❌ You can't promote someone with an equal or higher role than you!")
        return

    await member.add_roles(role)
    embed = discord.Embed(
        title="⬆️ Member Promoted!",
        description=f"{member.mention} has been promoted to **{role.name}**!",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)
    await log_mod_action(guild, "PROMOTE", ctx.author, member, f"Promoted to {role.name}")


@bot.command()
@staff_only()
async def demote(ctx, member: discord.Member):
    guild = ctx.guild
    staff_roles = ["Admin", "Moderator"]
    removed = []

    if not is_higher_role(ctx.author, member):
        await ctx.send("❌ You can't demote someone with an equal or higher role than you!")
        return
    if member == ctx.guild.owner:
        await ctx.send("❌ You can't demote the server owner!")
        return

    for role_name in staff_roles:
        role = discord.utils.get(member.roles, name=role_name)
        if role:
            await member.remove_roles(role)
            removed.append(role.name)

    if not removed:
        await ctx.send(f"❌ {member.mention} doesn't have any staff roles.")
        return

    embed = discord.Embed(
        title="⬇️ Member Demoted!",
        description=f"{member.mention} had **{', '.join(removed)}** removed.",
        color=discord.Color.purple()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)
    await log_mod_action(guild, "DEMOTE", ctx.author, member, f"Removed roles: {', '.join(removed)}")


@bot.command()
@staff_only()
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not is_higher_role(ctx.author, member):
        await ctx.send("❌ You can't kick someone with an equal or higher role than you!")
        return
    if member == ctx.guild.owner:
        await ctx.send("❌ You can't kick the server owner!")
        return
    await member.kick(reason=reason)
    embed = discord.Embed(
        title="🥢 Member Kicked!",
        description=f"{member.mention} has been kicked.",
        color=discord.Color.orange()
    )
    embed.add_field(name="Reason", value=reason)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)
    await log_mod_action(ctx.guild, "KICK", ctx.author, member, reason)


@bot.command()
@staff_only()
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not is_higher_role(ctx.author, member):
        await ctx.send("❌ You can't ban someone with an equal or higher role than you!")
        return
    if member == ctx.guild.owner:
        await ctx.send("❌ You can't ban the server owner!")
        return
    await member.ban(reason=reason)
    embed = discord.Embed(
        title="🔨 Member Banned!",
        description=f"{member.mention} has been banned.",
        color=discord.Color.red()
    )
    embed.add_field(name="Reason", value=reason)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)
    await log_mod_action(ctx.guild, "BAN", ctx.author, member, reason)


@bot.command()
@staff_only()
async def timeout(ctx, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
    # Parse duration
    time_units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = duration[-1].lower()
    if unit not in time_units or not duration[:-1].isdigit():
        await ctx.send("❌ Invalid duration! Use formats like `10m`, `1h`, `2d`, `30s`")
        return

    seconds = int(duration[:-1]) * time_units[unit]
    if seconds > 2419200:
        await ctx.send("❌ Timeout can't be longer than 28 days!")
        return

    until = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
    if not is_higher_role(ctx.author, member):
        await ctx.send("❌ You can't timeout someone with an equal or higher role than you!")
        return
    if member == ctx.guild.owner:
        await ctx.send("❌ You can't timeout the server owner!")
        return
    await member.timeout(until, reason=reason)

    embed = discord.Embed(
        title="⏱️ Member Timed Out!",
        description=f"{member.mention} has been timed out for **{duration}**.",
        color=discord.Color.yellow()
    )
    embed.add_field(name="Reason", value=reason)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)
    await log_mod_action(ctx.guild, "TIMEOUT", ctx.author, member, f"{duration} — {reason}")


@bot.command()
@staff_only()
async def untimeout(ctx, member: discord.Member):
    await member.timeout(None)
    embed = discord.Embed(
        title="✅ Timeout Removed!",
        description=f"{member.mention}'s timeout has been removed.",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)
    await log_mod_action(ctx.guild, "UNTIMEOUT", ctx.author, member, "Timeout removed")


@bot.command()
@staff_only()
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if not is_higher_role(ctx.author, member):
        await ctx.send("❌ You can't warn someone with an equal or higher role than you!")
        return
    try:
        warn_embed = discord.Embed(
            title=f"⚠️ Warning from {ctx.guild.name}",
            description=f"You have received a warning.",
            color=discord.Color.gold()
        )
        warn_embed.add_field(name="Reason", value=reason)
        warn_embed.set_footer(text="Please follow the server rules.")
        await member.send(embed=warn_embed)
        dm_sent = True
    except:
        dm_sent = False

    embed = discord.Embed(
        title="⚠️ Member Warned!",
        description=f"{member.mention} has been warned.{'✉️ DM sent!' if dm_sent else ' *(Could not DM user)*'}",
        color=discord.Color.gold()
    )
    embed.add_field(name="Reason", value=reason)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)
    await log_mod_action(ctx.guild, "WARN", ctx.author, member, reason)


# Error handler for missing permissions
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        pass  # Already handled by owner_only/staff_only with custom messages
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command!")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found! Make sure you @mention them correctly.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument! Use `!guide` for help.")


# ── FUN COMMANDS ──────────────────────────────────────────────────────────────────────────────

import random
import datetime

@bot.command()
async def coinflip(ctx):
    result = random.choice(["Heads", "Tails"])
    emoji = "🪙" if result == "Heads" else "🌑"
    embed = discord.Embed(
        title=f"{emoji} {result}!",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)


@bot.command()
async def pick(ctx, *options: str):
    if len(options) < 2:
        await ctx.send("❌ Give me at least 2 options! Example: `!pick pizza burger sushi`")
        return
    chosen = random.choice(options)
    embed = discord.Embed(
        title="🎲 I Pick...",
        description=f"**{chosen}**",
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Chosen from: {', '.join(options)}")
    await ctx.send(embed=embed)


@bot.command()
async def poll(ctx, question: str, *options: str):
    if len(options) < 2:
        await ctx.send('\u274c Need at least 2 options!\nExample: `!poll "Best game?" "Valorant" "Minecraft" "GTA"`')
        return
    if len(options) > 9:
        await ctx.send("❌ Maximum 9 options per poll!")
        return

    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
    description = "\n\n".join([f"{emojis[i]} {opt}" for i, opt in enumerate(options)])

    embed = discord.Embed(
        title=f"📊 {question}",
        description=description,
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Poll by {ctx.author.name}")
    poll_msg = await ctx.send(embed=embed)
    for i in range(len(options)):
        await poll_msg.add_reaction(emojis[i])


@bot.command()
async def quote(ctx):
    thinking = await ctx.send("💭 Getting a quote...")
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": 'Generate a single short inspiring or funny quote. Return ONLY the quote and author in this format: {"quote": "quote text here", "author": "Author Name"}. No extra text.'},
                {"role": "user", "content": "Give me a quote"}
            ]
        )
        data = extract_json(response.choices[0].message.content)
        if data:
            embed = discord.Embed(
                title="💬 Quote",
                description=f'*"{data["quote"]}"*',
                color=discord.Color.blurple()
            )
            embed.set_footer(text=f"\u2014 {data['author']}")
            await thinking.edit(content=None, embed=embed)
        else:
            await thinking.edit(content="❌ Couldn't get a quote, try again!")
    except Exception as e:
        await thinking.edit(content=f"❌ Error: {str(e)}")


@bot.command()
async def topic(ctx):
    thinking = await ctx.send("💭 Thinking of a topic...")
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Generate a single fun, interesting conversation starter for a friend group. Keep it under 20 words. Return ONLY the question, nothing else."},
                {"role": "user", "content": "Give me a conversation starter"}
            ]
        )
        topic_text = response.choices[0].message.content.strip()
        embed = discord.Embed(
            title="💬 Conversation Starter",
            description=f"**{topic_text}**",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Requested by {ctx.author.name}")
        await thinking.edit(content=None, embed=embed)
    except Exception as e:
        await thinking.edit(content=f"❌ Error: {str(e)}")


# ── EDIT MENU VIEWS & MODALS ─────────────────────────────────────────────────────────────────

class EditMenuView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=60)
        self.guild = guild

    @discord.ui.button(label="➕ Channel", style=discord.ButtonStyle.green, custom_id="edit_add_channel")
    async def add_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = []
        for cat in interaction.guild.categories[:25]:
            options.append(discord.SelectOption(
                label=cat.name[:100],
                value=str(cat.id),
                description=f"{len(cat.channels)} channels"
            ))
        if not options:
            await interaction.response.send_message("❌ No categories found!", ephemeral=True)
            return
        view = CategorySelectView(options, "add_channel")
        await interaction.response.send_message(
            "📁 Which category should the new channel go in?",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="➕ Category", style=discord.ButtonStyle.green, custom_id="edit_add_category")
    async def add_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCategoryModal())

    @discord.ui.button(label="➕ Role", style=discord.ButtonStyle.green, custom_id="edit_add_role")
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddRoleModal())

    @discord.ui.button(label="✏️ Rename", style=discord.ButtonStyle.primary, custom_id="edit_rename")
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RenameTypeView()
        await interaction.response.send_message(
            "✏️ What do you want to rename?",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.red, custom_id="edit_delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DeleteTypeView()
        await interaction.response.send_message(
            "🗑️ What do you want to delete?",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="🎨 Role Color", style=discord.ButtonStyle.primary, custom_id="edit_role_color")
    async def role_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = []
        for role in interaction.guild.roles:
            if role.name == "@everyone" or role.managed:
                continue
            options.append(discord.SelectOption(
                label=role.name[:100],
                value=str(role.id)
            ))
        if not options:
            await interaction.response.send_message("❌ No roles found!", ephemeral=True)
            return
        view = RoleColorSelectView(options[:25])
        await interaction.response.send_message(
            "🎨 Which role do you want to recolor?",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="📝 Edit Topic", style=discord.ButtonStyle.secondary, custom_id="edit_topic")
    async def edit_topic(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = []
        for channel in interaction.guild.text_channels[:25]:
            options.append(discord.SelectOption(
                label=channel.name[:100],
                value=str(channel.id),
                description=channel.topic[:50] if channel.topic else "No topic set"
            ))
        if not options:
            await interaction.response.send_message("❌ No text channels found!", ephemeral=True)
            return
        view = TopicSelectView(options)
        await interaction.response.send_message(
            "📝 Which channel's topic do you want to edit?",
            view=view,
            ephemeral=True
        )


class CategorySelectView(discord.ui.View):
    def __init__(self, options: list, action: str):
        super().__init__(timeout=60)
        self.action = action
        select = discord.ui.Select(
            placeholder="Choose a category...",
            options=options,
            custom_id="category_select"
        )
        select.callback = self.on_select
        self.add_item(select)
        self.selected_category_id = None

    async def on_select(self, interaction: discord.Interaction):
        self.selected_category_id = int(interaction.data["values"][0])
        category = interaction.guild.get_channel(self.selected_category_id)
        await interaction.response.send_modal(
            AddChannelModal(category)
        )


class AddChannelModal(discord.ui.Modal, title="➕ Add Channel"):
    def __init__(self, category: discord.CategoryChannel):
        super().__init__()
        self.category = category

    channel_name = discord.ui.TextInput(
        label="Channel Name",
        placeholder="e.g. red dead redemption, movie night...",
        required=True,
        max_length=100
    )

    channel_type = discord.ui.TextInput(
        label="Type: text / voice / forum",
        placeholder="text",
        required=False,
        max_length=5,
        default="text"
    )

    channel_topic = discord.ui.TextInput(
        label="Topic / Description (optional)",
        placeholder="e.g. Discuss Red Dead Redemption here!",
        required=False,
        max_length=1024,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        name = self.channel_name.value.strip()
        ch_type = self.channel_type.value.strip().lower() or "text"
        topic = self.channel_topic.value.strip()

        decoration_mode = getattr(bot, 'setup_decoration_mode', 'full')

        if decoration_mode == 'none':
            system_content = (
                "You are a Discord channel name formatter. "
                "Return ONLY a plain lowercase channel name with hyphens. "
                "No emoji, no decoration, no brackets. "
                "Examples: general, game-chat, voice-lounge"
            )
        elif decoration_mode == 'minimal':
            system_content = (
                "You are a Discord channel name formatter. "
                "Use a single emoji followed by ・ as a prefix. "
                "For text channels: 💬・ or a fitting emoji. "
                "For voice channels: 🔊・ "
                "For forum channels: 💡・ or a fitting emoji. "
                "Return ONLY the formatted name, lowercase with hyphens."
            )
        else:
            system_content = (
                "You are a Discord channel name formatter. "
                "Given a channel name and its category, return a decorated channel name "
                "that matches the style of the category. "
                "For text channels use emoji prefix like 「🎮」or 📺・ "
                "For voice channels start with 🔊・ "
                "For forum channels use a fitting emoji like 「💡」or 「📝」. "
                "Return ONLY the formatted channel name, nothing else. "
                "Keep it lowercase with hyphens."
            )

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": f"Category: {self.category.name}\nChannel name: {name}\nType: {ch_type}"}
                ]
            )
            formatted_name = response.choices[0].message.content.strip().strip('"')
        except:
            if decoration_mode == 'none':
                formatted_name = name.lower().replace(' ', '-')
            elif decoration_mode == 'minimal':
                if ch_type == "voice":
                    formatted_name = f"🔊・{name.lower().replace(' ', '-')}"
                elif ch_type == "forum":
                    formatted_name = f"💡・{name.lower().replace(' ', '-')}"
                else:
                    formatted_name = f"💬・{name.lower().replace(' ', '-')}"
            else:
                if ch_type == "voice":
                    formatted_name = f"🔊・{name.lower().replace(' ', '-')}"
                elif ch_type == "forum":
                    formatted_name = f"「💡」{name.lower().replace(' ', '-')}"
                else:
                    formatted_name = f"「💬」{name.lower().replace(' ', '-')}"

        try:
            if ch_type == "voice":
                channel = await guild.create_voice_channel(
                    name=formatted_name,
                    category=self.category
                )
            elif ch_type == "forum":
                try:
                    channel = await guild.create_forum(
                        name=formatted_name,
                        category=self.category,
                        topic=topic if topic else discord.utils.MISSING
                    )
                except Exception:
                    channel = await guild.create_text_channel(
                        name=formatted_name,
                        category=self.category,
                        topic=topic if topic else None
                    )
            else:
                channel = await guild.create_text_channel(
                    name=formatted_name,
                    category=self.category,
                    topic=topic if topic else None
                )

            # Track in undo history
            if not hasattr(bot, 'last_build') or not bot.last_build:
                data = await db_load(str(guild.id))
                bot.last_build = data.get("last_build") or {"roles": [], "categories": [], "channels": []}
            bot.last_build.setdefault("channels", []).append(channel.id)
            await db_save(str(guild.id), {"last_build": bot.last_build})

            embed = discord.Embed(
                title="✅ Channel Created!",
                color=discord.Color.green()
            )
            embed.add_field(name="Name", value=channel.name, inline=True)
            embed.add_field(name="Category", value=self.category.name, inline=True)
            embed.add_field(name="Type", value="🔊 Voice" if ch_type == "voice" else ("📋 Forum" if ch_type == "forum" else "💬 Text"), inline=True)
            if topic:
                embed.add_field(name="Topic", value=topic, inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")


class AddCategoryModal(discord.ui.Modal, title="➕ Add Category"):
    category_name = discord.ui.TextInput(
        label="Category Name",
        placeholder="e.g. Music Zone, Movie Night...",
        required=True,
        max_length=100
    )

    initial_channels = discord.ui.TextInput(
        label="Initial Channels (optional)",
        placeholder="e.g. general, voice: lounge, forum: ideas",
        required=False,
        max_length=300,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        name = self.category_name.value.strip()

        decoration_mode = getattr(bot, 'setup_decoration_mode', 'full')

        # Category name AI prompt based on decoration mode
        if decoration_mode == 'none':
            cat_system = (
                "You are a Discord category name formatter. "
                "Return ONLY the category name in plain uppercase with no emoji or decoration. "
                "Examples: MUSIC ZONE, GAMING, STUDY HUB"
            )
        elif decoration_mode == 'minimal':
            cat_system = (
                "You are a Discord category name formatter. "
                "Return the category name in uppercase with a single fitting emoji prefix. "
                "Examples: 🎵 MUSIC ZONE, 🎮 GAMING, 📚 STUDY HUB"
            )
        else:
            cat_system = (
                "You are a Discord category name formatter. "
                "Given a category name, return a decorated version with "
                "emoji borders like: ╔══〔 🎵 MUSIC ZONE 〕══╗ "
                "Return ONLY the formatted name, nothing else."
            )

        # Channel name AI prompt based on decoration mode
        if decoration_mode == 'none':
            ch_system = (
                "You are a Discord channel name formatter. "
                "Return ONLY a plain lowercase channel name with hyphens. No emoji or decoration."
            )
        elif decoration_mode == 'minimal':
            ch_system = (
                "You are a Discord channel name formatter. "
                "Use a single emoji followed by ・ as prefix. "
                "For text: 💬・ or fitting emoji. For voice: 🔊・ For forum: 💡・ "
                "Return ONLY the formatted name, lowercase with hyphens."
            )
        else:
            ch_system = (
                "You are a Discord channel name formatter. "
                "For text channels use emoji prefix like 「💬」or 📝・. "
                "For voice channels start with 🔊・. "
                "For forum channels use 「💡」or similar with 「」brackets. "
                "Return ONLY the formatted name, lowercase with hyphens."
            )

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": cat_system},
                    {"role": "user", "content": f"Category name: {name}"}
                ]
            )
            formatted_name = response.choices[0].message.content.strip().strip('"')
        except:
            if decoration_mode == 'none':
                formatted_name = name.upper()
            elif decoration_mode == 'minimal':
                formatted_name = f"📁 {name.upper()}"
            else:
                formatted_name = f"〔 {name.upper()} 〕"

        try:
            category = await guild.create_category(formatted_name)

            # Create initial channels if provided
            new_channel_ids = []
            channels_created = []
            channels_input = self.initial_channels.value.strip()
            if channels_input:
                for entry in channels_input.split(","):
                    entry = entry.strip()
                    if not entry:
                        continue
                    entry_lower = entry.lower()
                    if entry_lower.startswith("voice:"):
                        ch_name = entry[6:].strip()
                        ch_type = "voice"
                    elif entry_lower.startswith("forum:"):
                        ch_name = entry[6:].strip()
                        ch_type = "forum"
                    else:
                        ch_name = entry
                        ch_type = "text"

                    # AI-format the channel name
                    try:
                        ch_resp = groq_client.chat.completions.create(
                            model="llama-3.3-70b-versatile",
                            messages=[
                                {"role": "system", "content": ch_system},
                                {"role": "user", "content": f"Category: {formatted_name}\nChannel: {ch_name}\nType: {ch_type}"}
                            ]
                        )
                        formatted_ch = ch_resp.choices[0].message.content.strip().strip('"')
                    except:
                        if decoration_mode == 'none':
                            formatted_ch = ch_name.lower().replace(' ', '-')
                        elif decoration_mode == 'minimal':
                            if ch_type == 'voice':
                                formatted_ch = f"🔊・{ch_name.lower().replace(' ', '-')}"
                            elif ch_type == 'forum':
                                formatted_ch = f"💡・{ch_name.lower().replace(' ', '-')}"
                            else:
                                formatted_ch = f"💬・{ch_name.lower().replace(' ', '-')}"
                        else:
                            if ch_type == "voice":
                                formatted_ch = f"🔊・{ch_name.lower().replace(' ', '-')}"
                            elif ch_type == "forum":
                                formatted_ch = f"「💡」{ch_name.lower().replace(' ', '-')}"
                            else:
                                formatted_ch = f"「💬」{ch_name.lower().replace(' ', '-')}"

                    try:
                        if ch_type == "voice":
                            ch = await guild.create_voice_channel(formatted_ch, category=category)
                        elif ch_type == "forum":
                            try:
                                ch = await guild.create_forum(formatted_ch, category=category)
                            except Exception:
                                ch = await guild.create_text_channel(formatted_ch, category=category)
                        else:
                            ch = await guild.create_text_channel(formatted_ch, category=category)
                        channels_created.append(formatted_ch)
                        new_channel_ids.append(ch.id)
                    except Exception as ch_e:
                        print(f"⚠️ Could not create channel {formatted_ch}: {ch_e}")
                    await asyncio.sleep(0.4)

            # Track in undo history
            if not hasattr(bot, 'last_build') or not bot.last_build:
                data = await db_load(str(guild.id))
                bot.last_build = data.get("last_build") or {"roles": [], "categories": [], "channels": []}
            bot.last_build.setdefault("categories", []).append(category.id)
            bot.last_build.setdefault("channels", []).extend(new_channel_ids)
            await db_save(str(guild.id), {"last_build": bot.last_build})

            embed = discord.Embed(
                title="✅ Category Created!",
                color=discord.Color.green()
            )
            embed.add_field(name="Category", value=category.name, inline=False)
            if channels_created:
                embed.add_field(
                    name=f"💬 Channels Created ({len(channels_created)})",
                    value="\n".join(f"‣ `{c}`" for c in channels_created),
                    inline=False
                )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")


class RenameTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="💬 Rename Channel", style=discord.ButtonStyle.primary, custom_id="rename_channel_btn")
    async def rename_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = []
        for channel in interaction.guild.text_channels[:25]:
            options.append(discord.SelectOption(
                label=channel.name[:100],
                value=str(channel.id)
            ))
        if not options:
            await interaction.response.send_message("❌ No text channels found!", ephemeral=True)
            return
        view = ChannelRenameSelectView(options)
        await interaction.response.edit_message(
            content="💬 Which channel do you want to rename?",
            view=view
        )

    @discord.ui.button(label="📁 Rename Category", style=discord.ButtonStyle.primary, custom_id="rename_category_btn")
    async def rename_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = []
        for cat in interaction.guild.categories[:25]:
            options.append(discord.SelectOption(
                label=cat.name[:100],
                value=str(cat.id)
            ))
        if not options:
            await interaction.response.send_message("❌ No categories found!", ephemeral=True)
            return
        view = CategoryRenameSelectView(options)
        await interaction.response.edit_message(
            content="📁 Which category do you want to rename?",
            view=view
        )


class ChannelRenameSelectView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a channel...",
            options=options,
            custom_id="channel_rename_select"
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        channel = interaction.guild.get_channel(channel_id)
        await interaction.response.send_modal(RenameChannelModal(channel))


class RenameChannelModal(discord.ui.Modal, title="✏️ Rename Channel"):
    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    new_name = discord.ui.TextInput(
        label="New Channel Name",
        placeholder="e.g. game-lounge, movie-night...",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        old_name = self.channel.name
        try:
            await self.channel.edit(name=self.new_name.value.strip().lower().replace(" ", "-"))
            embed = discord.Embed(
                title="✅ Channel Renamed!",
                description=f"**{old_name}** → **{self.channel.name}**",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")


class CategoryRenameSelectView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a category...",
            options=options,
            custom_id="category_rename_select"
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        category_id = int(interaction.data["values"][0])
        category = interaction.guild.get_channel(category_id)
        await interaction.response.send_modal(RenameCategoryModal(category))


class RenameCategoryModal(discord.ui.Modal, title="✏️ Rename Category"):
    def __init__(self, category):
        super().__init__()
        self.category = category

    new_name = discord.ui.TextInput(
        label="New Category Name",
        placeholder="e.g. Music Zone, Movie Night...",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        old_name = self.category.name

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": "Format this as a decorated Discord category name with emoji borders like: ╔══〔 🎵 MUSIC ZONE 〕══╗. Return ONLY the formatted name."
                    },
                    {"role": "user", "content": self.new_name.value.strip()}
                ]
            )
            formatted = response.choices[0].message.content.strip().strip('"')
        except:
            formatted = f"〔 {self.new_name.value.upper()} 〕"

        try:
            await self.category.edit(name=formatted)
            embed = discord.Embed(
                title="✅ Category Renamed!",
                description=f"**{old_name}** → **{formatted}**",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")


class DeleteTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="💬 Delete Channel", style=discord.ButtonStyle.red, custom_id="delete_channel_btn")
    async def delete_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = []
        for channel in interaction.guild.text_channels[:25]:
            options.append(discord.SelectOption(
                label=channel.name[:100],
                value=str(channel.id)
            ))
        if not options:
            await interaction.response.send_message("❌ No text channels found!", ephemeral=True)
            return
        view = ChannelDeleteSelectView(options)
        await interaction.response.edit_message(
            content="💬 Which channel do you want to delete?",
            view=view
        )

    @discord.ui.button(label="📁 Delete Category", style=discord.ButtonStyle.red, custom_id="delete_category_btn")
    async def delete_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = []
        for cat in interaction.guild.categories[:25]:
            options.append(discord.SelectOption(
                label=cat.name[:100],
                value=str(cat.id)
            ))
        if not options:
            await interaction.response.send_message("❌ No categories found!", ephemeral=True)
            return
        view = CategoryDeleteSelectView(options)
        await interaction.response.edit_message(
            content="📁 Which category do you want to delete?",
            view=view
        )

    @discord.ui.button(label="🎭 Delete Role", style=discord.ButtonStyle.red, custom_id="delete_role_btn")
    async def delete_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = []
        for role in interaction.guild.roles:
            if role.name == "@everyone" or role.managed:
                continue
            options.append(discord.SelectOption(
                label=role.name[:100],
                value=str(role.id)
            ))
        if not options:
            await interaction.response.send_message("❌ No deletable roles found!", ephemeral=True)
            return
        view = RoleDeleteSelectView(options[:25])
        await interaction.response.edit_message(
            content="🎭 Which role do you want to delete?",
            view=view
        )


class ChannelDeleteSelectView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a channel to delete...",
            options=options,
            custom_id="channel_delete_select"
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        channel = interaction.guild.get_channel(channel_id)
        name = channel.name
        await channel.delete()
        embed = discord.Embed(
            title="🗑️ Channel Deleted!",
            description=f"Deleted **{name}**",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


class CategoryDeleteSelectView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a category to delete...",
            options=options,
            custom_id="category_delete_select"
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        category_id = int(interaction.data["values"][0])
        category = interaction.guild.get_channel(category_id)
        name = category.name
        for channel in category.channels:
            await channel.delete()
        await category.delete()
        embed = discord.Embed(
            title="🗑️ Category Deleted!",
            description=f"Deleted **{name}** and all its channels",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


class RoleDeleteSelectView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a role to delete...",
            options=options,
            custom_id="role_delete_select"
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        role_id = int(interaction.data["values"][0])
        role = interaction.guild.get_role(role_id)
        name = role.name
        try:
            await role.delete()
            embed = discord.Embed(
                title="🗑️ Role Deleted!",
                description=f"Deleted role **{name}**",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"❌ Error: {str(e)}", view=None)


class AddRoleModal(discord.ui.Modal, title="➕ Add Role"):
    role_name = discord.ui.TextInput(
        label="Role Name",
        placeholder="e.g. Artist, Musician, Night Owl...",
        required=True,
        max_length=100
    )

    role_color = discord.ui.TextInput(
        label="Color (hex code or color name)",
        placeholder="e.g. #FF5733 or red, blue, gold...",
        required=False,
        max_length=20,
        default="random"
    )

    role_type = discord.ui.TextInput(
        label="Show separately in member list? (yes/no)",
        placeholder="yes",
        required=False,
        max_length=3,
        default="yes"
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        name = self.role_name.value.strip()
        color_input = self.role_color.value.strip().lower()
        hoist = self.role_type.value.strip().lower() != "no"

        color_map = {
            "red": 0xFF0000, "blue": 0x0000FF, "green": 0x00FF00,
            "gold": 0xFFD700, "orange": 0xFF8C00, "purple": 0x8B008B,
            "pink": 0xFF69B4, "white": 0xFFFFFF, "black": 0x000000,
            "yellow": 0xFFFF00, "cyan": 0x00FFFF, "teal": 0x008080
        }

        try:
            if color_input == "random" or not color_input:
                import random
                role_color = discord.Color(random.randint(0x100000, 0xFFFFFF))
            elif color_input in color_map:
                role_color = discord.Color(color_map[color_input])
            elif color_input.startswith("#"):
                role_color = discord.Color(int(color_input[1:], 16))
            else:
                role_color = discord.Color.default()
        except:
            role_color = discord.Color.default()

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a Discord role name formatter. "
                            "Given a role name, add a fitting emoji prefix. "
                            "Examples: Night Owl → 🦉 Night Owl, Artist → 🎨 Artist, Gamer → 🎮 Gamer. "
                            "Return ONLY the formatted role name, nothing else."
                        )
                    },
                    {"role": "user", "content": f"Role name: {name}"}
                ]
            )
            formatted_name = response.choices[0].message.content.strip().strip('"')
        except:
            formatted_name = name

        try:
            role = await guild.create_role(
                name=formatted_name,
                color=role_color,
                hoist=hoist,
                mentionable=True
            )
            registered = await register_self_role(guild, role)
            if registered:
                await sync_roles_panel(guild)
            if is_color_role_name(role.name):
                await enforce_color_role_priority(guild)
            embed = discord.Embed(
                title="✅ Role Created!",
                color=role_color
            )
            embed.add_field(name="Name", value=role.name, inline=True)
            embed.add_field(name="Color", value=str(role_color), inline=True)
            embed.add_field(name="Shown Separately", value="Yes" if hoist else "No", inline=True)
            if registered:
                embed.add_field(name="Self-Role Panel", value="Updated automatically", inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")


class RoleColorSelectView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a role...",
            options=options,
            custom_id="role_color_select"
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        role_id = int(interaction.data["values"][0])
        role = interaction.guild.get_role(role_id)
        await interaction.response.send_modal(RoleColorModal(role))


class RoleColorModal(discord.ui.Modal, title="🎨 Change Role Color"):
    def __init__(self, role: discord.Role):
        super().__init__()
        self.role = role

    new_color = discord.ui.TextInput(
        label="New Color (hex or color name)",
        placeholder="e.g. #FF5733 or red, blue, gold...",
        required=True,
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        color_input = self.new_color.value.strip().lower()

        color_map = {
            "red": 0xFF0000, "blue": 0x0000FF, "green": 0x00FF00,
            "gold": 0xFFD700, "orange": 0xFF8C00, "purple": 0x8B008B,
            "pink": 0xFF69B4, "white": 0xFFFFFF, "black": 0x000000,
            "yellow": 0xFFFF00, "cyan": 0x00FFFF, "teal": 0x008080
        }

        try:
            if color_input in color_map:
                new_color = discord.Color(color_map[color_input])
            elif color_input.startswith("#"):
                new_color = discord.Color(int(color_input[1:], 16))
            else:
                await interaction.followup.send("❌ Invalid color! Use hex like `#FF5733` or a name like `red`")
                return
        except:
            await interaction.followup.send("❌ Invalid color format!")
            return

        try:
            await self.role.edit(color=new_color)
            embed = discord.Embed(
                title="🎨 Role Color Updated!",
                description=f"**{self.role.name}** is now colored!",
                color=new_color
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")


class TopicSelectView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a channel...",
            options=options,
            custom_id="topic_select"
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        channel = interaction.guild.get_channel(channel_id)
        await interaction.response.send_modal(EditTopicModal(channel))


class EditTopicModal(discord.ui.Modal, title="📝 Edit Channel Topic"):
    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    new_topic = discord.ui.TextInput(
        label="New Topic / Description",
        placeholder="e.g. Discuss your favorite games here!",
        required=True,
        max_length=1024,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            await self.channel.edit(topic=self.new_topic.value.strip())
            embed = discord.Embed(
                title="📝 Topic Updated!",
                color=discord.Color.green()
            )
            embed.add_field(name="Channel", value=self.channel.name, inline=True)
            embed.add_field(name="New Topic", value=self.new_topic.value.strip(), inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")


# ── GUIDE COMMANDS ────────────────────────────────────────────────────────────────────────────

class GuideView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🏗️ Setup", style=discord.ButtonStyle.primary, custom_id="guide_setup")
    async def setup_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🏗️ Setup Commands",
            color=discord.Color.blue()
        )
        embed.add_field(name="!setup", value="Start building your server (includes Community template!)", inline=False)
        embed.add_field(name="!edit", value="Open the server editor — add channels (text/voice/forum), categories with initial channels, roles, rename, delete and more!", inline=False)
        embed.add_field(name="!undo / !redo", value="Undo or redo the last build", inline=False)
        embed.add_field(name="!nuke", value="Wipe server clean to start fresh\nOwner only", inline=False)
        embed.add_field(name="!refreshroles", value="Fix role buttons after bot restart", inline=False)
        embed.add_field(name="!serverstats", value="Add live stats to your server", inline=False)
        embed.add_field(name="!ticket setup", value="Add a ticket system", inline=False)
        embed.add_field(name="!addcommunity", value="Add community channels: announcements (staff-only), forums for suggestions/bugs/reviews\nAlso moves orphan channels into the category", inline=False)
        embed.add_field(name="!announce", value="Post a styled announcement — pick channel and role from dropdowns", inline=False)
        embed.set_footer(text="Architect AI • !guide for main menu")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🔨 Moderation", style=discord.ButtonStyle.danger, custom_id="guide_mod")
    async def mod_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🔨 Moderation Commands",
            color=discord.Color.red()
        )
        embed.add_field(name="!promote @user mod/admin", value="Give staff role", inline=False)
        embed.add_field(name="!demote @user", value="Remove staff roles", inline=False)
        embed.add_field(name="!kick @user reason", value="Kick a member", inline=False)
        embed.add_field(name="!ban @user reason", value="Ban a member", inline=False)
        embed.add_field(name="!timeout @user 10m reason", value="Mute a member temporarily", inline=False)
        embed.add_field(name="!untimeout @user", value="Remove a timeout", inline=False)
        embed.add_field(name="!warn @user reason", value="Warn a member via DM", inline=False)
        embed.add_field(name="!addrole / !removerole", value="Manually add or remove roles", inline=False)
        embed.add_field(name="!automod", value="Open the auto-mod control panel\nConfigure spam, caps, links, banned words and more", inline=False)
        embed.add_field(name="!announce", value="Post a styled announcement — choose title, message, role ping & channel", inline=False)
        embed.set_footer(text="All actions logged in #mod-logs • !guide for main menu")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🎮 Fun & Levels", style=discord.ButtonStyle.success, custom_id="guide_fun")
    async def fun_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎮 Fun & Level Commands",
            color=discord.Color.green()
        )
        embed.add_field(name="!rank @user", value="See your XP rank card", inline=False)
        embed.add_field(name="!leaderboard", value="Top 10 most active members", inline=False)
        embed.add_field(name="!giveaway 1h 1 Prize", value="Start a giveaway", inline=False)
        embed.add_field(name='!poll "Q" "A" "B"', value="Create a poll", inline=False)
        embed.add_field(name="!pick opt1 opt2", value="Randomly pick an option", inline=False)
        embed.add_field(name="!coinflip", value="Flip a coin", inline=False)
        embed.add_field(name="!quote", value="Get an AI quote", inline=False)
        embed.add_field(name="!topic", value="Get a conversation starter", inline=False)
        embed.set_footer(text="Architect AI • !guide for main menu")
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.command()
@staff_only()
async def addrole(ctx, member: discord.Member, *, role_name: str):
    guild = ctx.guild
    role = discord.utils.get(guild.roles, name=role_name)

    if not role:
        await ctx.send(f"❌ Role **{role_name}** not found! Make sure the name is exact.")
        return
    if not is_higher_role(ctx.author, member):
        await ctx.send("❌ You can't manage roles for someone with an equal or higher role than you!")
        return
    if role in member.roles:
        await ctx.send(f"❌ {member.mention} already has **{role.name}**!")
        return

    await member.add_roles(role)
    embed = discord.Embed(
        title="✅ Role Added!",
        description=f"Gave **{role.name}** to {member.mention}",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)
    await log_mod_action(ctx.guild, "PROMOTE", ctx.author, member, f"Manually added role: {role.name}")


@bot.command()
@staff_only()
async def removerole(ctx, member: discord.Member, *, role_name: str):
    guild = ctx.guild
    role = discord.utils.get(guild.roles, name=role_name)

    if not role:
        await ctx.send(f"❌ Role **{role_name}** not found! Make sure the name is exact.")
        return
    if not is_higher_role(ctx.author, member):
        await ctx.send("❌ You can't manage roles for someone with an equal or higher role than you!")
        return
    if role not in member.roles:
        await ctx.send(f"❌ {member.mention} doesn't have **{role.name}**!")
        return

    await member.remove_roles(role)
    embed = discord.Embed(
        title="✅ Role Removed!",
        description=f"Removed **{role.name}** from {member.mention}",
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)
    await log_mod_action(ctx.guild, "DEMOTE", ctx.author, member, f"Manually removed role: {role.name}")


@bot.command()
@staff_only()
async def clean(ctx, channel: discord.TextChannel = None):
    """Delete all messages in a channel. Defaults to the current channel."""
    target = channel or ctx.channel
    # Require manage_messages on the target channel
    if not ctx.author.guild_permissions.manage_messages and ctx.author != ctx.guild.owner:
        await ctx.send("❌ You need **Manage Messages** permission to clean a channel!")
        return

    confirm_embed = discord.Embed(
        title="🧹 Clean Channel",
        description=(
            f"⚠️ This will delete **all messages** in {target.mention}.\n\n"
            "Type `!confirmclean` within 30 seconds to proceed.\n"
            "Type `!cancelclean` to cancel."
        ),
        color=discord.Color.orange()
    )
    confirm_embed.set_footer(text="This action cannot be undone!")
    await ctx.send(embed=confirm_embed)
    bot.clean_pending = True
    bot.clean_requester = ctx.author.id
    bot.clean_target = target

    await asyncio.sleep(30)
    if hasattr(bot, 'clean_pending') and bot.clean_pending:
        bot.clean_pending = False
        try:
            await ctx.send("❌ Clean cancelled — timed out after 30 seconds.")
        except:
            pass


@bot.command()
async def confirmclean(ctx):
    if not hasattr(bot, 'clean_pending') or not bot.clean_pending:
        await ctx.send("❌ No clean pending! Run `!clean` first.")
        return
    if ctx.author.id != bot.clean_requester:
        await ctx.send("❌ Only the person who ran `!clean` can confirm it!")
        return

    bot.clean_pending = False
    target: discord.TextChannel = bot.clean_target

    progress_msg = await ctx.send(f"🧹 Cleaning {target.mention}... please wait!")

    try:
        # Clone the channel (preserves settings, wipes messages)
        new_channel = await target.clone(reason=f"Channel cleaned by {ctx.author}")
        await target.delete(reason=f"Channel cleaned by {ctx.author}")
        await new_channel.edit(position=target.position)
        await new_channel.send(
            embed=discord.Embed(
                title="🧹 Channel Cleaned",
                description=f"All messages have been removed by {ctx.author.mention}.",
                color=discord.Color.green()
            )
        )
        # Edit original progress message if it's in a different channel, otherwise it's gone
        if ctx.channel.id != target.id:
            await progress_msg.edit(content=f"✅ {new_channel.mention} has been cleaned!")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to manage that channel!")
    except Exception as e:
        await ctx.send(f"❌ Something went wrong: {e}")


@bot.command()
async def cancelclean(ctx):
    if not hasattr(bot, 'clean_pending') or not bot.clean_pending:
        await ctx.send("❌ No clean pending.")
        return
    if ctx.author.id != bot.clean_requester:
        await ctx.send("❌ Only the person who ran `!clean` can cancel it!")
        return
    bot.clean_pending = False
    await ctx.send("❌ Channel clean cancelled.")


@bot.command()
@commands.has_permissions(administrator=True)
async def nuke(ctx):
    # Only server owner can nuke
    if ctx.author != ctx.guild.owner:
        await ctx.send("❌ Only the server owner can use this command!")
        return

    # Confirmation step so it cant be done accidentally
    confirm_embed = discord.Embed(
        title="💣 NUKE SERVER",
        description=(
            "⚠️ **This will delete EVERYTHING:**\n\n"
            "• All channels\n"
            "• All categories\n"
            "• All roles (except @everyone and bot roles)\n\n"
            "**Members will NOT be removed.**\n\n"
            "Type `!confirmnuke` within 30 seconds to proceed.\n"
            "Type `!cancelnuke` to cancel."
        ),
        color=discord.Color.red()
    )
    confirm_embed.set_footer(text="This action cannot be undone!")
    await ctx.send(embed=confirm_embed)
    bot.nuke_pending = True
    bot.nuke_requester = ctx.author.id

    # Auto cancel after 30 seconds
    await asyncio.sleep(30)
    if hasattr(bot, 'nuke_pending') and bot.nuke_pending:
        bot.nuke_pending = False
        try:
            await ctx.send("❌ Nuke cancelled — timed out after 30 seconds.")
        except:
            pass


@bot.command()
async def confirmnuke(ctx):
    if not hasattr(bot, 'nuke_pending') or not bot.nuke_pending:
        await ctx.send("❌ No nuke pending! Run `!nuke` first.")
        return
    if ctx.author.id != bot.nuke_requester:
        await ctx.send("❌ Only the person who ran `!nuke` can confirm it!")
        return
    if ctx.author != ctx.guild.owner:
        await ctx.send("❌ Only the server owner can nuke the server!")
        return

    bot.nuke_pending = False
    guild = ctx.guild

    progress_msg = await ctx.send("💣 Nuking server... please wait!")

    try:
        # Step 1 — Delete all channels and categories
        await progress_msg.edit(content="💣 Deleting all channels...")
        for channel in guild.channels:
            try:
                await channel.delete()
                await asyncio.sleep(0.4)
            except:
                pass

        # Step 2 — Delete all roles except @everyone and bot roles
        await asyncio.sleep(1)
        for role in guild.roles:
            if role.name == "@everyone":
                continue
            if role.managed:
                continue
            if role == guild.me.top_role:
                continue
            try:
                await role.delete()
                await asyncio.sleep(0.4)
            except:
                pass

        # Step 3 — Clear saved state since everything is wiped
        bot.last_build = None
        bot.member_role_id = None
        save_state({"member_role_id": None})

        # Step 4 — Create a fresh general channel to confirm completion
        new_channel = await guild.create_text_channel("general")
        await new_channel.send(
            embed=discord.Embed(
                title="💣 Nuke Complete!",
                description=(
                    "Server has been wiped clean.\n\n"
                    "All members are still here!\n\n"
                    "Run `!setup` to build a fresh server from scratch 🏗️"
                ),
                color=discord.Color.green()
            )
        )

    except Exception as e:
        try:
            new_channel = await guild.create_text_channel("general")
            await new_channel.send(f"❌ Nuke failed halfway: {str(e)}")
        except:
            pass


@bot.command()
async def cancelnuke(ctx):
    if not hasattr(bot, 'nuke_pending') or not bot.nuke_pending:
        await ctx.send("❌ No nuke pending!")
        return
    bot.nuke_pending = False
    await ctx.send("✅ Nuke cancelled. Server is safe!")


# ── TICKET SYSTEM ─────────────────────────────────────────────────────────────────────────────

class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Open Ticket", style=discord.ButtonStyle.green, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user

        # Check if user already has an open ticket
        existing = discord.utils.get(guild.text_channels, name=f"ticket-{member.name.lower()}")
        if existing:
            await interaction.response.send_message(
                f"❌ You already have an open ticket! {existing.mention}",
                ephemeral=True
            )
            return

        # Get staff roles
        admin_role = discord.utils.get(guild.roles, name="Admin")
        mod_role = discord.utils.get(guild.roles, name="Moderator")

        # Set permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                attach_files=True,
                embed_links=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_channels=True
            )
        }
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True
            )
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True
            )

        # Find or create tickets category
        category = discord.utils.get(guild.categories, name="🎫 TICKETS")
        if not category:
            category = await guild.create_category("🎫 TICKETS")
            if admin_role:
                await category.set_permissions(admin_role, read_messages=True)
            if mod_role:
                await category.set_permissions(mod_role, read_messages=True)
            await category.set_permissions(guild.default_role, read_messages=False)

        # Create ticket channel
        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{member.name.lower()}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket opened by {member.name} | {member.id}"
        )

        # Send welcome message in ticket
        embed = discord.Embed(
            title="🎫 Ticket Opened!",
            description=(
                f"Hey {member.mention}, thanks for opening a ticket!\n\n"
                f"Please describe your issue and a staff member will help you shortly.\n\n"
                f"**Staff:** {admin_role.mention if admin_role else ''} "
                f"{mod_role.mention if mod_role else ''}"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="Click 🔒 Close Ticket when your issue is resolved")
        await ticket_channel.send(
            embed=embed,
            view=TicketCloseView()
        )

        await interaction.response.send_message(
            f"✅ Your ticket has been created! {ticket_channel.mention}",
            ephemeral=True
        )

        # Log to mod-logs
        log_channel = discord.utils.get(guild.text_channels, name="「📋」mod-logs")
        if not log_channel:
            log_channel = discord.utils.get(guild.text_channels, name="mod-logs")
        if log_channel:
            log_embed = discord.Embed(
                title="🎫 Ticket Opened",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member.name})", inline=True)
            log_embed.add_field(name="Channel", value=ticket_channel.mention, inline=True)
            log_embed.set_thumbnail(url=member.display_avatar.url)
            await log_channel.send(embed=log_embed)


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        guild = interaction.guild
        member = interaction.user

        # Only staff or the ticket owner can close
        admin_role = discord.utils.get(guild.roles, name="Admin")
        mod_role = discord.utils.get(guild.roles, name="Moderator")
        is_staff = (
            admin_role in member.roles or
            mod_role in member.roles or
            member == guild.owner
        )
        is_ticket_owner = str(member.id) in (channel.topic or "")

        if not is_staff and not is_ticket_owner:
            await interaction.response.send_message(
                "❌ Only staff or the ticket owner can close this ticket!",
                ephemeral=True
            )
            return

        # Confirmation
        confirm_embed = discord.Embed(
            title="🔒 Close Ticket?",
            description="Are you sure you want to close this ticket?",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(
            embed=confirm_embed,
            view=TicketConfirmCloseView(),
            ephemeral=True
        )


class TicketConfirmCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Yes, Close It", style=discord.ButtonStyle.red, custom_id="confirm_close_ticket")
    async def confirm_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        guild = interaction.guild
        member = interaction.user

        # Collect transcript
        messages = []
        async for message in channel.history(limit=100, oldest_first=True):
            if not message.author.bot:
                messages.append(f"[{message.created_at.strftime('%H:%M')}] {message.author.name}: {message.content}")

        transcript = "\n".join(messages) if messages else "No messages"

        # Log to mod-logs with transcript
        log_channel = discord.utils.get(guild.text_channels, name="「📋」mod-logs")
        if not log_channel:
            log_channel = discord.utils.get(guild.text_channels, name="mod-logs")
        if log_channel:
            log_embed = discord.Embed(
                title="🔒 Ticket Closed",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            log_embed.add_field(name="Channel", value=channel.name, inline=True)
            log_embed.add_field(name="Closed By", value=f"{member.mention}", inline=True)
            log_embed.add_field(
                name="Transcript",
                value=f"```{transcript[:800]}```" if transcript else "Empty",
                inline=False
            )
            try:
                await log_channel.send(embed=log_embed)
            except discord.Forbidden:
                print(f"⚠️ Cannot send to mod-logs ({log_channel.id}) — missing permissions")

        await interaction.response.send_message("🔒 Closing ticket...")
        await asyncio.sleep(2)
        await channel.delete()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_close_ticket")
    async def cancel_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Ticket close cancelled!", ephemeral=True)


@bot.command()
@owner_only()
async def ticket(ctx, action: str = "setup", member: discord.Member = None):
    guild = ctx.guild

    if action == "setup":
        # Create ticket channel with open button
        existing = discord.utils.get(guild.text_channels, name="「🎫」tickets")
        if existing:
            await ctx.send(f"❌ Ticket channel already exists! {existing.mention}")
            return

        ticket_channel = await guild.create_text_channel(name="「🎫」tickets")

        embed = discord.Embed(
            title="🎫 Support Tickets",
            description=(
                "Need help? Have a question? Want to report something?\n\n"
                "Click the button below to open a private ticket with staff!\n\n"
                "📋 **Guidelines:**\n"
                "• One ticket per issue\n"
                "• Be respectful to staff\n"
                "• Provide as much detail as possible\n"
                "• Don't spam open/close tickets"
            ),
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Tickets are logged for safety purposes")
        await ticket_channel.send(embed=embed, view=TicketOpenView())

        await ctx.send(
            embed=discord.Embed(
                title="✅ Ticket System Setup!",
                description=f"Ticket channel created at {ticket_channel.mention}",
                color=discord.Color.green()
            )
        )

    elif action == "add" and member:
        if not ctx.channel.name.startswith("ticket-"):
            await ctx.send("❌ This command only works inside a ticket channel!")
            return
        await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
        await ctx.send(f"✅ Added {member.mention} to the ticket!")

    elif action == "remove" and member:
        if not ctx.channel.name.startswith("ticket-"):
            await ctx.send("❌ This command only works inside a ticket channel!")
            return
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(f"✅ Removed {member.mention} from the ticket!")

    else:
        await ctx.send(
            "❌ Invalid usage!\n"
            "`!ticket setup` — creates the ticket channel\n"
            "`!ticket add @user` — adds user to current ticket\n"
            "`!ticket remove @user` — removes user from current ticket"
        )


# ── LEVELING SYSTEM ─────────────────────────────────────────────────────────────────────────────

def get_xp_for_level(level: int) -> int:
    return 100 * (level ** 2) + 50 * level

def get_level_from_xp(xp: int) -> int:
    level = 0
    while xp >= get_xp_for_level(level + 1):
        level += 1
    return level

def get_title(level: int, prestige: int) -> str:
    if prestige >= 5:
        return "👑 Legend"
    elif prestige == 4:
        return "🌌 Ascended"
    elif prestige == 3:
        return "💫 Mythic"
    elif prestige == 2:
        return "🔮 Mystic"
    elif prestige == 1:
        return "✨ Prestige"
    elif level >= 30:
        return "💎 Elite"
    elif level >= 20:
        return "🔥 Veteran"
    elif level >= 10:
        return "⚡ Active"
    elif level >= 5:
        return "👣 Regular"
    else:
        return "🌱 Newcomer"

def get_prestige_badge(prestige: int) -> str:
    badges = {
        0: "",
        1: "✨",
        2: "🔮",
        3: "💫",
        4: "🌌",
        5: "👑"
    }
    return badges.get(prestige, "👑")

async def generate_rank_card(member: discord.Member, data: dict, rank_position: int) -> discord.File:
    # Get role color
    role_color = (88, 101, 242)  # Default discord blurple
    # Better fallback colors based on level
    default_colors = [
        (88, 101, 242),   # Blurple
        (87, 242, 135),   # Green
        (254, 231, 92),   # Yellow
        (235, 69, 158),   # Pink
        (255, 115, 55),   # Orange
    ]
    # Skip staff roles, only use decorative/color roles
    staff_role_names = ["admin", "moderator", "mod", "member", "staff"]
    for role in reversed(member.roles):
        if role.color.value != 0:
            if not any(s in role.name.lower() for s in staff_role_names):
                role_color = (role.color.r, role.color.g, role.color.b)
                break

    current_xp = data["xp"]
    current_level = get_level_from_xp(current_xp)
    prestige = data.get("prestige", 0)
    next_level_xp = get_xp_for_level(current_level + 1)
    current_level_xp = get_xp_for_level(current_level)
    progress_xp = current_xp - current_level_xp
    needed_xp = next_level_xp - current_level_xp
    progress_percent = progress_xp / needed_xp if needed_xp > 0 else 1

    # Create card
    width, height = 800, 200
    img = Image.new("RGBA", (width, height), (30, 30, 35, 255))
    draw = ImageDraw.Draw(img)

    # Background gradient using role color
    for i in range(width):
        alpha = int(180 * (i / width))
        r = int(role_color[0] * (i / width))
        g = int(role_color[1] * (i / width))
        b = int(role_color[2] * (i / width))
        draw.line([(i, 0), (i, height)], fill=(r, g, b, alpha))

    # Dark overlay for readability
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 120))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Avatar circle
    try:
        avatar_url = str(member.display_avatar.replace(size=128))
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url) as resp:
                avatar_data = await resp.read()
        avatar = Image.open(io.BytesIO(avatar_data)).convert("RGBA")
        avatar = avatar.resize((120, 120))
        mask = Image.new("L", (120, 120), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 120, 120), fill=255)
        img.paste(avatar, (40, 40), mask)
    except:
        draw.ellipse([40, 40, 160, 160], fill=role_color)

    # Role color circle border
    draw.ellipse([36, 36, 164, 164], outline=role_color, width=4)

    # Username
    draw.text((200, 35), member.display_name[:20], fill=(255, 255, 255), font=None)

    # Title
    title = get_title(current_level, prestige)
    draw.text((200, 65), title, fill=role_color, font=None)

    # Rank and Level
    draw.text((200, 95), f"RANK #{rank_position}     LEVEL {current_level}", fill=(200, 200, 200), font=None)

    # Prestige badge
    if prestige > 0:
        badge = get_prestige_badge(prestige)
        draw.text((200, 115), f"Prestige {prestige} {badge}", fill=(255, 215, 0), font=None)

    # XP bar background
    bar_x, bar_y = 200, 145
    bar_width, bar_height = 540, 20
    draw.rounded_rectangle(
        [bar_x, bar_y, bar_x + bar_width, bar_y + bar_height],
        radius=10,
        fill=(60, 60, 65)
    )

    # XP bar fill
    fill_width = int(bar_width * progress_percent)
    if fill_width > 0:
        draw.rounded_rectangle(
            [bar_x, bar_y, bar_x + fill_width, bar_y + bar_height],
            radius=10,
            fill=role_color
        )

    # XP text
    draw.text(
        (bar_x, bar_y + 25),
        f"{progress_xp} / {needed_xp} XP",
        fill=(180, 180, 180),
        font=None
    )

    # Messages count
    draw.text(
        (bar_x + bar_width - 100, bar_y + 25),
        f"💬 {data.get('messages', 0)} msgs",
        fill=(180, 180, 180),
        font=None
    )

    # Convert to file
    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="rank.png")


@bot.command()
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    guild_id = str(ctx.guild.id)
    user_id = str(member.id)

    guild_levels = await load_guild_levels(guild_id)
    if user_id not in guild_levels:
        await ctx.send(f"❌ {member.mention} hasn't earned any XP yet! Start chatting!")
        return

    data = guild_levels[user_id]
    sorted_users = sorted(guild_levels.items(), key=lambda x: x[1].get("xp", 0), reverse=True)
    rank_position = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), "?")

    thinking = await ctx.send("🎨 Generating your rank card...")
    try:
        rank_card = await generate_rank_card(member, data, rank_position)
        await thinking.delete()
        await ctx.send(file=rank_card)
    except Exception as e:
        current_xp = data["xp"]
        current_level = get_level_from_xp(current_xp)
        prestige = data.get("prestige", 0)
        next_level_xp = get_xp_for_level(current_level + 1)
        current_level_xp = get_xp_for_level(current_level)
        progress_xp = current_xp - current_level_xp
        needed_xp = next_level_xp - current_level_xp
        progress_percent = int((progress_xp / needed_xp) * 20)
        progress_bar = "█" * progress_percent + "░" * (20 - progress_percent)
        title = get_title(current_level, prestige)
        badge = get_prestige_badge(prestige)

        embed = discord.Embed(
            title=f"⭐ {member.display_name}'s Rank",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="🏆 Rank", value=f"#{rank_position}", inline=True)
        embed.add_field(name="⭐ Level", value=str(current_level), inline=True)
        embed.add_field(name="🎭 Title", value=f"{badge} {title}", inline=True)
        embed.add_field(name="💬 Messages", value=str(data.get("messages", 0)), inline=True)
        embed.add_field(
            name=f"XP — {progress_xp}/{needed_xp}",
            value=f"`{progress_bar}` {int((progress_xp/needed_xp)*100)}%",
            inline=False
        )
        await thinking.edit(content=None, embed=embed)


@bot.command()
async def leaderboard(ctx):
    try:
        guild_id = str(ctx.guild.id)
        guild_data = await load_guild_levels(guild_id)
        if not guild_data:
            await ctx.send("❌ No one has earned XP yet! Start chatting!")
            return

        # Filter out users with 0 XP
        active_users = {uid: data for uid, data in guild_data.items() if data.get("xp", 0) > 0}

        if not active_users:
            await ctx.send("❌ No one has earned XP yet! Start chatting!")
            return

        sorted_users = sorted(active_users.items(), key=lambda x: x[1]["xp"], reverse=True)[:10]

        embed = discord.Embed(
            title=f"🏆 {ctx.guild.name} Leaderboard",
            color=discord.Color.gold()
        )

        medals = ["🥇", "🥈", "🥉"]
        description = ""
        for i, (user_id, data) in enumerate(sorted_users):
            try:
                member = ctx.guild.get_member(int(user_id))
                name = member.display_name if member else f"Unknown User"
                level = get_level_from_xp(data.get("xp", 0))
                xp = data.get("xp", 0)
                prestige = data.get("prestige", 0)
                badge = get_prestige_badge(prestige) if prestige > 0 else ""
                medal = medals[i] if i < 3 else f"`#{i+1}`"
                description += f"{medal} **{name}** {badge} — Level {level} ({xp} XP)\n"
            except:
                continue

        if not description:
            await ctx.send("❌ Couldn't load leaderboard data!")
            return

        embed.description = description
        embed.set_footer(text="Use !rank to see your detailed stats")
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Leaderboard error: {str(e)}")


@bot.event
async def on_message(message):
    # Ignore bots and DMs
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    # ── AUTO-MOD ──────────────────────────────────────────
    if not is_staff(message.author) and message.author != message.guild.owner:
        guild_id = str(message.guild.id)
        config = bot.automod_cache.get(guild_id, {})

        if config.get("enabled"):
            content = message.content
            member = message.author
            flagged = False
            flag_reason = ""

            # 1. Slur filter — always on when automod enabled
            content_lower = content.lower()
            for word in BLOCKED_WORDS:
                if word in content_lower:
                    flagged = True
                    flag_reason = "Slur detected"
                    break

            # 2. Custom banned words
            if not flagged:
                for word in config.get("banned_words", []):
                    if word in content_lower:
                        flagged = True
                        flag_reason = "Banned word"
                        break

            # 3. Spam — 5 messages in 5 seconds
            if not flagged and config.get("block_spam", True):
                spam_key = f"{guild_id}_{member.id}"
                now = asyncio.get_event_loop().time()
                if spam_key not in bot.spam_tracker:
                    bot.spam_tracker[spam_key] = []
                bot.spam_tracker[spam_key].append(now)
                bot.spam_tracker[spam_key] = [
                    t for t in bot.spam_tracker[spam_key] if now - t < 5
                ]
                if len(bot.spam_tracker[spam_key]) >= 5:
                    flagged = True
                    flag_reason = "Spam detected"
                    bot.spam_tracker[spam_key] = []

            # 4. Caps spam — 80%+ caps in messages over 8 chars
            if not flagged and config.get("block_caps", True):
                if len(content) > 8:
                    caps_ratio = sum(1 for c in content if c.isupper()) / len(content)
                    if caps_ratio > 0.8:
                        flagged = True
                        flag_reason = "Excessive caps"

            # 5. Links
            if not flagged and config.get("block_links", False):
                import re as _re
                url_pattern = _re.compile(r'https?://\S+|www\.\S+|discord\.gg/\S+')
                if url_pattern.search(content):
                    flagged = True
                    flag_reason = "Links not allowed"

            # 6. Mass mentions
            if not flagged and config.get("block_mentions", True):
                if len(message.mentions) >= 5 or message.mention_everyone:
                    flagged = True
                    flag_reason = "Mass mentions"

            if flagged:
                try:
                    await message.delete()
                    await message.channel.send(
                        f"⚠️ {member.mention} — {flag_reason}!",
                        delete_after=5
                    )
                    await automod_warn(message.guild, member, flag_reason)
                except:
                    pass
                await bot.process_commands(message)
                return
    # ── END AUTO-MOD ──────────────────────────────────────

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)

    # XP cooldown — 60s per user to prevent spam farming
    cooldown_key = f"{guild_id}:{user_id}"
    now = asyncio.get_event_loop().time()
    last = bot.xp_cooldowns.get(cooldown_key, 0)
    if now - last >= 60:
        bot.xp_cooldowns[cooldown_key] = now

        guild_levels = await load_guild_levels(guild_id)
        if user_id not in guild_levels:
            guild_levels[user_id] = {"xp": 0, "level": 0, "messages": 0, "prestige": 0}

        xp_gain = random.randint(15, 25)
        guild_levels[user_id]["xp"] += xp_gain
        guild_levels[user_id]["messages"] = guild_levels[user_id].get("messages", 0) + 1

        old_level = get_level_from_xp(guild_levels[user_id]["xp"] - xp_gain)
        new_level = get_level_from_xp(guild_levels[user_id]["xp"])

        # Level up!
        if new_level > old_level:
            guild_levels[user_id]["level"] = new_level
            prestige = guild_levels[user_id].get("prestige", 0)

            # Check for prestige at level 50
            if new_level >= 50:
                prestige += 1
                guild_levels[user_id]["prestige"] = prestige
                guild_levels[user_id]["xp"] = 0
                guild_levels[user_id]["level"] = 0
                await save_guild_levels(guild_id, guild_levels)

                badge = get_prestige_badge(prestige)
                prestige_embed = discord.Embed(
                    title=f"🌟 PRESTIGE {prestige} {badge}",
                    description=(
                        f"🎊 {message.author.mention} has reached **Prestige {prestige}!**\n\n"
                        f"Their XP has been reset but they carry the prestigious **{badge}** badge!\n"
                        f"New title: **{get_title(0, prestige)}**"
                    ),
                    color=discord.Color.gold()
                )
                prestige_embed.set_thumbnail(url=message.author.display_avatar.url)

                # Give prestige role
                prestige_role_name = f"{badge} Prestige {prestige}"
                prestige_role = discord.utils.get(message.guild.roles, name=prestige_role_name)
                if not prestige_role:
                    prestige_role = await message.guild.create_role(
                        name=prestige_role_name,
                        color=discord.Color.gold(),
                        hoist=True
                    )
                await message.author.add_roles(prestige_role)
                await message.channel.send(embed=prestige_embed)
                await bot.process_commands(message)
                return

            await save_guild_levels(guild_id, guild_levels)
            title = get_title(new_level, prestige)
            badge = get_prestige_badge(prestige)

            embed = discord.Embed(
                title="⭐ Level Up!",
                description=(
                    f"🎉 {message.author.mention} just reached **Level {new_level}!**\n"
                    f"Title: **{title}**"
                    + (f" {badge}" if prestige > 0 else "")
                ),
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)

            # Title role rewards
            title_roles = {
                5:  ("👣 Regular",  discord.Color.green()),
                10: ("⚡ Active",   discord.Color.blue()),
                20: ("🔥 Veteran",  discord.Color.orange()),
                30: ("💎 Elite",    discord.Color.purple()),
            }
            if new_level in title_roles:
                role_name, role_color = title_roles[new_level]
                # Remove old title roles
                old_title_roles = [r for r in message.author.roles if r.name in [v[0] for v in title_roles.values()]]
                if old_title_roles:
                    await message.author.remove_roles(*old_title_roles)
                # Add new title role
                role = discord.utils.get(message.guild.roles, name=role_name)
                if not role:
                    role = await message.guild.create_role(
                        name=role_name,
                        color=role_color,
                        hoist=True
                    )
                await message.author.add_roles(role)
                embed.add_field(
                    name="🎁 New Title Unlocked!",
                    value=f"You are now a **{role_name}**!",
                    inline=False
                )

            await message.channel.send(embed=embed)
        else:
            await save_guild_levels(guild_id, guild_levels)

    await bot.process_commands(message)


@bot.command()
@owner_only()
async def redo(ctx):
    if not hasattr(bot, 'last_template') or bot.last_template is None:
        data = await db_load(str(ctx.guild.id))
        if data.get("last_template"):
            bot.last_template = data["last_template"]
        else:
            await ctx.send("❌ Nothing to redo! There's no previous server template saved.")
            return

    embed = discord.Embed(
        title="🔄 Redo Last Build?",
        description=(
            "This will rebuild your server using the last template.\n\n"
            f"**Server:** {bot.last_template.get('server_name', 'Unknown')}\n\n"
            "Type `!confirmredo` to proceed or `!cancelredo` to cancel."
        ),
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)
    bot.redo_pending = True


@bot.command()
@owner_only()
async def confirmredo(ctx):
    if not hasattr(bot, 'redo_pending') or not bot.redo_pending:
        await ctx.send("❌ No redo pending! Run `!redo` first.")
        return

    if not hasattr(bot, 'last_template') or bot.last_template is None:
        data = await db_load(str(ctx.guild.id))
        if data.get("last_template"):
            bot.last_template = data["last_template"]
        else:
            await ctx.send("❌ Couldn't find a saved template to redo.")
            bot.redo_pending = False
            return

    bot.redo_pending = False
    bot.pending_template = bot.last_template
    bot.pending_ctx = ctx
    await ctx.send("🔄 Restoring last build...")
    await ctx.invoke(bot.get_command('confirm'))


@bot.command()
@owner_only()
async def cancelredo(ctx):
    if not hasattr(bot, 'redo_pending') or not bot.redo_pending:
        await ctx.send("❌ No redo pending!")
        return
    bot.redo_pending = False
    await ctx.send("✅ Redo cancelled!")


# ── SERVER STATS ───────────────────────────────────────────────────────────────────────────

async def compute_server_counts(guild: discord.Guild):
    # Ensure member cache is warm so online/member numbers are accurate.
    if not guild.chunked:
        try:
            await guild.chunk(cache=True)
        except Exception:
            pass

    total_members = len([m for m in guild.members if not m.bot])
    total_bots = len([m for m in guild.members if m.bot])
    online_members = len([m for m in guild.members if not m.bot and m.status != discord.Status.offline])
    total_channels = len(guild.text_channels) + len(guild.voice_channels)
    total_roles = len(guild.roles) - 1
    return total_members, total_bots, online_members, total_channels, total_roles


async def update_server_stats(guild: discord.Guild):
    try:
        state = load_state()
        category_id = state.get("stats_category_id") or getattr(bot, 'stats_category_id', None)
        if not category_id:
            return

        category = guild.get_channel(int(category_id))
        if not category:
            return

        total_members, total_bots, online_members, total_channels, total_roles = await compute_server_counts(guild)

        stats = [
            f"👥・ Members: {total_members}",
            f"🟢・ Online: {online_members}",
            f"🤖・ Bots: {total_bots}",
            f"💬・ Channels: {total_channels}",
            f"🎭・ Roles: {total_roles}",
            f"🏠・ Server: {guild.name[:15]}",
        ]

        channels = [c for c in category.channels]
        for i, channel in enumerate(channels):
            if i < len(stats):
                await channel.edit(name=stats[i])

    except Exception as e:
        print(f"⚠️ Stats update error: {e}")


@bot.command()
@owner_only()
async def serverstats(ctx):
    guild = ctx.guild
    progress = await ctx.send("📊 Setting up server stats...")

    try:
        # Create stats category
        existing = discord.utils.get(guild.categories, name="📊 SERVER STATS 📊")
        if existing:
            await ctx.send("❌ Server stats already exists! Use `!updatestats` to refresh.")
            return

        # Make category visible but channels read only
        category = await guild.create_category(
            name="📊 SERVER STATS 📊",
            position=0
        )

        # Hide from everyone except viewing
        await category.set_permissions(guild.default_role,
            connect=False,
            send_messages=False,
            read_messages=True
        )

        # Count stats
        total_members, total_bots, online_members, total_channels, total_roles = await compute_server_counts(guild)

        # Create stat channels
        stats = [
            f"👥・ Members: {total_members}",
            f"🟢・ Online: {online_members}",
            f"🤖・ Bots: {total_bots}",
            f"💬・ Channels: {total_channels}",
            f"🎭・ Roles: {total_roles}",
            f"🏠・ Server: {guild.name[:15]}",
        ]

        for stat in stats:
            channel = await guild.create_voice_channel(
                name=stat,
                category=category
            )
            await channel.set_permissions(guild.default_role,
                connect=False,
                view_channel=True
            )

        # Save category id for updates
        save_state({"stats_category_id": category.id})
        bot.stats_category_id = category.id

        embed = discord.Embed(
            title="📊 Server Stats Setup!",
            description=(
                "Stats channels created at the top of your server!\n\n"
                "They update automatically every 10 minutes.\n"
                "Use `!updatestats` to force an update anytime."
            ),
            color=discord.Color.green()
        )
        await progress.edit(content=None, embed=embed)

    except Exception as e:
        await progress.edit(content=f"❌ Error: {str(e)}")


@bot.command()
@owner_only()
async def updatestats(ctx):
    await update_server_stats(ctx.guild)
    await ctx.send("✅ Server stats updated!", delete_after=3)


@bot.command()
@owner_only()
async def removestats(ctx):
    guild = ctx.guild
    category = discord.utils.get(guild.categories, name="📊 SERVER STATS 📊")
    if not category:
        await ctx.send("❌ No stats category found!")
        return
    for channel in category.channels:
        await channel.delete()
    await category.delete()
    save_state({"stats_category_id": None})
    await ctx.send("✅ Server stats removed!")


@tasks.loop(minutes=10)
async def auto_update_stats():
    for guild in bot.guilds:
        await update_server_stats(guild)

@auto_update_stats.before_loop
async def before_stats():
    await bot.wait_until_ready()


@bot.command()
@owner_only()
async def refreshroles(ctx):
    guild = ctx.guild
    data = await db_load(str(guild.id))
    if not data.get("decorative_roles") and not data.get("color_roles"):
        await ctx.send("❌ No saved role data found! Run `!setup` and `!confirm` first.")
        return

    ok, reason = await sync_roles_panel(guild, force_recreate=True)
    if not ok:
        await ctx.send(f"❌ Couldn't refresh role panels: {reason}")
        return
    await ctx.send("✅ Role buttons refreshed!", delete_after=3)


# ── GIVEAWAY SYSTEM ────────────────────────────────────────────────────────────────

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(
        label="🎉 Enter Giveaway",
        style=discord.ButtonStyle.green,
        custom_id="enter_giveaway_btn"
    )
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            guild_id = str(interaction.guild.id)
            user_id = str(interaction.user.id)

            data = await db_load(guild_id)
            giveaways = data.get("giveaways", {})

            if self.giveaway_id not in giveaways:
                await interaction.response.send_message(
                    "❌ This giveaway no longer exists!",
                    ephemeral=True
                )
                return

            giveaway = giveaways[self.giveaway_id]

            if giveaway.get("ended"):
                await interaction.response.send_message(
                    "❌ This giveaway has already ended!",
                    ephemeral=True
                )
                return

            entries = giveaway.get("entries", [])

            if user_id in entries:
                entries.remove(user_id)
                giveaways[self.giveaway_id]["entries"] = entries
                await db_save(guild_id, {"giveaways": giveaways})
                await interaction.response.send_message(
                    "✅ You left the giveaway!",
                    ephemeral=True
                )
            else:
                entries.append(user_id)
                giveaways[self.giveaway_id]["entries"] = entries
                await db_save(guild_id, {"giveaways": giveaways})
                await interaction.response.send_message(
                    f"🎉 You entered the giveaway! Good luck!\n"
                    f"Total entries: **{len(entries)}**",
                    ephemeral=True
                )

            # Update the embed entry count
            channel = interaction.channel
            try:
                msg = await channel.fetch_message(int(self.giveaway_id))
                embed = msg.embeds[0]
                for i, field in enumerate(embed.fields):
                    if "Entries" in field.name:
                        embed.set_field_at(i, name="👥 Entries", value=str(len(entries)), inline=True)
                        break
                await msg.edit(embed=embed)
            except:
                pass

        except Exception as e:
            try:
                await interaction.response.send_message(
                    f"❌ Error: {str(e)}", ephemeral=True
                )
            except:
                pass


def parse_duration(duration: str) -> int:
    time_units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = duration[-1].lower()
    if unit not in time_units or not duration[:-1].isdigit():
        return None
    return int(duration[:-1]) * time_units[unit]


async def end_giveaway(guild_id: str, message_id: str, channel_id: str, winners_count: int):
    try:
        data = await db_load(guild_id)
        giveaways = data.get("giveaways", {})

        if message_id not in giveaways:
            return

        giveaway = giveaways[message_id]
        if giveaway.get("ended"):
            return

        giveaway["ended"] = True
        await db_save(guild_id, {"giveaways": giveaways})

        guild = bot.get_guild(int(guild_id))
        if not guild:
            return

        channel = guild.get_channel(int(channel_id))
        if not channel:
            return

        try:
            msg = await channel.fetch_message(int(message_id))
        except:
            return

        entries = giveaway.get("entries", [])
        prize = giveaway.get("prize", "Unknown Prize")

        if not entries:
            embed = discord.Embed(
                title="🎉 Giveaway Ended — No Winner",
                description=f"**Prize:** {prize}\n\nNo one entered the giveaway!",
                color=discord.Color.red()
            )
            await msg.edit(embed=embed, view=None)
            await channel.send("😢 The giveaway ended with no entries!")
            return

        actual_winners = min(winners_count, len(entries))
        winner_ids = random.sample(entries, actual_winners)
        winners = []
        for wid in winner_ids:
            member = guild.get_member(int(wid))
            if member:
                winners.append(member)

        winner_mentions = ", ".join([w.mention for w in winners])

        embed = discord.Embed(
            title="🎉 Giveaway Ended!",
            description=f"**Prize:** {prize}",
            color=discord.Color.gold()
        )
        embed.add_field(name="🏆 Winner(s)", value=winner_mentions, inline=False)
        embed.add_field(name="👥 Total Entries", value=str(len(entries)), inline=True)
        embed.set_footer(text="Giveaway ended")
        await msg.edit(embed=embed, view=None)

        await channel.send(
            f"🎉 Congratulations {winner_mentions}! "
            f"You won **{prize}**!"
        )

    except Exception as e:
        print(f"⚠️ Giveaway end error: {e}")


@bot.command()
@staff_only()
async def giveaway(ctx, duration: str, winners: int, *, prize: str):
    seconds = parse_duration(duration)
    if not seconds:
        await ctx.send(
            "❌ Invalid duration! Use formats like `30s` `10m` `2h` `1d`\n"
            "Example: `!giveaway 1h 1 Steam Key`"
        )
        return

    if winners < 1 or winners > 20:
        await ctx.send("❌ Winners must be between 1 and 20!")
        return

    if seconds < 10:
        await ctx.send("❌ Giveaway must be at least 10 seconds long!")
        return

    end_time = int(discord.utils.utcnow().timestamp()) + seconds

    embed = discord.Embed(
        title=f"🎉 GIVEAWAY — {prize}",
        description=(
            f"Click the button below to enter!\n"
            f"Click again to leave the giveaway.\n\n"
            f"⏰ **Ends:** <t:{end_time}:R>"
        ),
        color=discord.Color.gold()
    )
    embed.add_field(name="🏆 Winners", value=str(winners), inline=True)
    embed.add_field(name="👥 Entries", value="0", inline=True)
    embed.add_field(name="🎁 Prize", value=prize, inline=True)
    embed.set_footer(text=f"Hosted by {ctx.author.name}")

    giveaway_msg = await ctx.send(embed=embed)

    view = GiveawayView(str(giveaway_msg.id))
    await giveaway_msg.edit(view=view)

    guild_id = str(ctx.guild.id)
    data = await db_load(guild_id)
    giveaways = data.get("giveaways", {})
    giveaways[str(giveaway_msg.id)] = {
        "prize": prize,
        "winners": winners,
        "entries": [],
        "ended": False,
        "channel_id": str(ctx.channel.id),
        "host": str(ctx.author.id),
        "end_time": end_time
    }
    await db_save(guild_id, {"giveaways": giveaways})

    await ctx.message.delete()

    async def schedule_end():
        await asyncio.sleep(seconds)
        await end_giveaway(
            str(ctx.guild.id),
            str(giveaway_msg.id),
            str(ctx.channel.id),
            winners
        )

    asyncio.create_task(schedule_end())


@bot.command()
@staff_only()
async def gend(ctx, message_id: str):
    await end_giveaway(
        str(ctx.guild.id),
        message_id,
        str(ctx.channel.id),
        1
    )
    await ctx.send("✅ Giveaway ended early!", delete_after=3)


@bot.command()
@staff_only()
async def greroll(ctx, message_id: str):
    guild_id = str(ctx.guild.id)
    data = await db_load(guild_id)
    giveaways = data.get("giveaways", {})

    if message_id not in giveaways:
        await ctx.send("❌ Giveaway not found!")
        return

    giveaway = giveaways[message_id]
    entries = giveaway.get("entries", [])

    if not entries:
        await ctx.send("❌ No entries to reroll from!")
        return

    winner_id = random.choice(entries)
    member = ctx.guild.get_member(int(winner_id))

    if not member:
        await ctx.send("❌ Could not find the winner in the server!")
        return

    await ctx.send(
        f"🎉 New winner: {member.mention}! "
        f"Congratulations on winning **{giveaway.get('prize', 'the prize')}**!"
    )


# ── SERVER STRUCTURE HELPERS ─────────────────────────────────────────────────────────────────

async def create_rules_channel(guild: discord.Guild, category: discord.CategoryChannel,
                               role_objects: dict = None, template: dict = None,
                               created: dict = None):
    """Create a 📜 rules channel with ✅ reaction-role in the given category. Idempotent."""
    existing = discord.utils.find(lambda c: "rules" in c.name.lower(), category.channels)
    if existing:
        return existing

    guild_id = str(guild.id)

    # Resolve admin / mod / member roles
    admin_role = discord.utils.get(guild.roles, name="Admin")
    mod_role = discord.utils.get(guild.roles, name="Moderator")
    member_role = None
    if role_objects and template:
        for r in template.get("roles", []):
            if r.get("type") == "admin" and r["name"] in role_objects:
                admin_role = role_objects[r["name"]]
            elif r.get("type") == "moderator" and r["name"] in role_objects:
                mod_role = role_objects[r["name"]]
            elif r.get("type") == "member" and r["name"] in role_objects:
                member_role = role_objects[r["name"]]
    if not member_role:
        member_role = discord.utils.find(
            lambda r: r.name.lower() in ("member", "members") and not r.managed,
            guild.roles
        )

    _read_only = discord.PermissionOverwrite(
        read_messages=True,
        send_messages=False,
        create_public_threads=False,
        create_private_threads=False,
        send_messages_in_threads=False,
        add_reactions=True
    )
    overwrites = {
        guild.default_role: _read_only,
        guild.me: discord.PermissionOverwrite(
            send_messages=True, read_messages=True, add_reactions=True
        )
    }
    if member_role:
        overwrites[member_role] = _read_only
    if admin_role:
        overwrites[admin_role] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True,
            create_public_threads=True, manage_threads=True
        )
    if mod_role:
        overwrites[mod_role] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True,
            create_public_threads=True, manage_threads=True
        )

    rules_ch = await guild.create_text_channel(
        name="「📜」rules",
        category=category,
        topic="Server rules — react ✅ to gain access",
        overwrites=overwrites
    )
    if created is not None:
        created["channels"].append(rules_ch.id)
    await asyncio.sleep(0.5)

    rules_embed = discord.Embed(
        title="📜 Server Rules",
        description=(
            "Please read and follow these rules to keep our community safe and fun:\n\n"
            "**1.** 🤝 Be respectful — treat all members with kindness\n"
            "**2.** 🚫 No harassment, hate speech, or discrimination\n"
            "**3.** 💬 Keep topics relevant to the correct channels\n"
            "**4.** 🔞 No NSFW, explicit, or disturbing content\n"
            "**5.** 🔇 No spamming, flooding, or excessive caps\n"
            "**6.** 🔗 No malicious links, scams, or phishing\n"
            "**7.** 📢 Follow all staff instructions without argument\n"
            "**8.** 🎭 No impersonation of members or staff\n"
            "**9.** 🔔 No mass pinging or unnecessary role mentions\n"
            "**10.** 📋 Use the correct channels for your content\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "**React with ✅ below to agree and gain access to the server!**"
        ),
        color=discord.Color.from_rgb(255, 127, 80)
    )
    rules_embed.set_footer(text="By reacting you agree to follow all rules above")
    rules_msg = await rules_ch.send(embed=rules_embed)
    await rules_msg.add_reaction("✅")

    await db_save(guild_id, {
        "rules_message_id": rules_msg.id,
        "rules_channel_id": rules_ch.id
    })
    bot.rules_cache[guild_id] = {"message_id": rules_msg.id}

    return rules_ch


async def create_info_category(guild: discord.Guild, role_objects: dict = None,
                                template: dict = None, created: dict = None) -> discord.CategoryChannel:
    """Create a 📌 INFO category with a rules channel, for all server templates."""
    existing = discord.utils.get(guild.categories, name="📌 INFO")
    if existing:
        # Still ensure rules channel exists inside it
        await create_rules_channel(guild, existing, role_objects, template, created)
        return existing
    has_stats = discord.utils.get(guild.categories, name="📊 SERVER STATS 📊") is not None
    position = 1 if has_stats else 0
    category = await guild.create_category("📌 INFO", position=position)
    if created is not None:
        created["categories"].append(category.id)
    # INFO is visible to everyone (@everyone can read but not write)
    # @everyone: can see INFO so they know where to go to get their role
    await category.set_permissions(
        guild.default_role,
        read_messages=True,
        send_messages=False,
        add_reactions=True
    )
    # Resolve member role and make INFO read-only for them too
    member_role = None
    if role_objects and template:
        for r in template.get("roles", []):
            if r.get("type") == "member" and r["name"] in role_objects:
                member_role = role_objects[r["name"]]
                break
    if not member_role:
        member_role = discord.utils.find(
            lambda r: r.name.lower() in ("member", "members") and not r.managed,
            guild.roles
        )
    if member_role:
        await category.set_permissions(
            member_role,
            read_messages=True,
            send_messages=False,
            read_message_history=True,
            add_reactions=True
        )
    await create_rules_channel(guild, category, role_objects, template, created)

    # Invite channel — read-only, bot posts the permanent link
    existing_ch_names = [c.name for c in category.channels]
    if not any("invite" in n for n in existing_ch_names):
        # Build read-only overwrites matching the rest of INFO
        invite_overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=False,
                add_reactions=False,
                create_public_threads=False,
                create_private_threads=False,
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_messages=True,
            ),
        }
        if member_role:
            invite_overwrites[member_role] = discord.PermissionOverwrite(
                read_messages=True,
                send_messages=False,
                add_reactions=False,
                create_public_threads=False,
                create_private_threads=False,
            )
        invite_ch = await guild.create_text_channel(
            "《🔗》invite",
            category=category,
            topic="Permanent invite link — share with friends!",
            overwrites=invite_overwrites,
        )
        if created is not None:
            created["channels"].append(invite_ch.id)
        await asyncio.sleep(0.4)
        try:
            invite = await invite_ch.create_invite(max_age=0, max_uses=0, reason="Server invite channel")
            embed = discord.Embed(
                title="🔗 Invite Friends!",
                description=(
                    f"Share this permanent invite link with your friends and family:\n\n"
                    f"**{invite.url}**\n\n"
                    "This link never expires and has no use limit."
                ),
                color=discord.Color.blurple(),
            )
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            embed.set_footer(text="Copy the link above and send it to anyone!")
            await invite_ch.send(embed=embed)
        except Exception as e:
            print(f"⚠️ Could not create invite: {e}")

    return category


async def ensure_general_channels(guild: discord.Guild, created: dict):
    """Guarantee the General category has #general, #bot-commands, and #invite channels.
    Also locks the welcome channel to bot-only posting."""
    general_cat = None
    for cat in guild.categories:
        if "general" in cat.name.lower():
            general_cat = cat
            break
    if not general_cat:
        return

    existing_names = [ch.name.lower() for ch in general_cat.text_channels]

    # Lock the welcome channel — bot-only posting
    welcome_ch = discord.utils.find(
        lambda c: "welcome" in c.name.lower(),
        general_cat.text_channels
    )
    if welcome_ch:
        await welcome_ch.set_permissions(guild.default_role,
            read_messages=True,
            send_messages=False,
            add_reactions=False,
            create_public_threads=False,
            create_private_threads=False
        )
        await welcome_ch.set_permissions(guild.me,
            read_messages=True,
            send_messages=True
        )

    # Check for a general chat channel (not welcome, not staff-related)
    has_general = any(
        "general" in n and "staff" not in n and "welcome" not in n
        for n in existing_names
    )
    if not has_general:
        ch = await guild.create_text_channel(
            "「💬」general",
            category=general_cat,
            topic="General chat for everyone 💬"
        )
        created["channels"].append(ch.id)
        await asyncio.sleep(0.4)

    # Check for a public bot-commands channel
    has_bot_cmds = any("bot" in n and "command" in n for n in existing_names)
    if not has_bot_cmds:
        ch = await guild.create_text_channel(
            "「🤖」bot-commands",
            category=general_cat,
            topic="Use bot commands here 🤖"
        )
        created["channels"].append(ch.id)
        await asyncio.sleep(0.4)




# ── COMMUNITY CHANNELS HELPER ─────────────────────────────────────────────────────────────────

async def create_community_channels(guild: discord.Guild, role_objects: dict = None, template: dict = None, created: dict = None) -> discord.CategoryChannel:
    """Create the 📋 COMMUNITY category with announcements and forum channels."""
    existing = discord.utils.get(guild.categories, name="📋 COMMUNITY")
    if existing:
        return existing

    # Place after stats (pos 0) and INFO (pos 1) if present
    has_stats = discord.utils.get(guild.categories, name="📊 SERVER STATS 📊") is not None
    has_info = discord.utils.get(guild.categories, name="📌 INFO") is not None
    if has_stats and has_info:
        position = 2
    elif has_stats or has_info:
        position = 1
    else:
        position = 0
    category = await guild.create_category("📋 COMMUNITY", position=position)
    if created is not None:
        created["categories"].append(category.id)

    # Resolve admin / mod roles
    admin_role = discord.utils.get(guild.roles, name="Admin")
    mod_role = discord.utils.get(guild.roles, name="Moderator")
    if role_objects:
        for r in (template or {}).get("roles", []):
            if r.get("type") == "admin" and r["name"] in role_objects:
                admin_role = role_objects[r["name"]]
            elif r.get("type") == "moderator" and r["name"] in role_objects:
                mod_role = role_objects[r["name"]]

    for ch_data in COMMUNITY_CHANNELS:
        ch_type = ch_data["type"]
        ch_name = ch_data["name"]
        ch_topic = ch_data.get("topic", "")

        if ch_type == "text":
            overwrites = {}
            if ch_data.get("staff_post_only"):
                overwrites[guild.default_role] = discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False,
                    add_reactions=False
                )
                # Member role: same read-only restriction
                member_role = discord.utils.find(
                    lambda r: r.name.lower() in ("member", "members") and not r.managed,
                    guild.roles
                )
                if member_role:
                    overwrites[member_role] = overwrites[guild.default_role]
                if admin_role:
                    overwrites[admin_role] = discord.PermissionOverwrite(
                        read_messages=True, send_messages=True,
                        create_public_threads=True, manage_threads=True
                    )
                if mod_role:
                    overwrites[mod_role] = discord.PermissionOverwrite(
                        read_messages=True, send_messages=True,
                        create_public_threads=True, manage_threads=True
                    )
            text_ch = await guild.create_text_channel(
                name=ch_name,
                category=category,
                topic=ch_topic,
                overwrites=overwrites if overwrites else discord.utils.MISSING
            )
            if created is not None:
                created["channels"].append(text_ch.id)
            await asyncio.sleep(0.5)

        elif ch_type == "forum":
            try:
                forum_ch = await guild.create_forum(
                    name=ch_name,
                    category=category,
                    topic=ch_topic if ch_topic else discord.utils.MISSING
                )
            except Exception as e:
                print(f"⚠️ Forum channel fallback for {ch_name}: {e}")
                forum_ch = await guild.create_text_channel(name=ch_name, category=category, topic=ch_topic)
            if created is not None:
                created["channels"].append(forum_ch.id)
            await asyncio.sleep(0.5)

    return category


# ── !addcommunity COMMAND ─────────────────────────────────────────────────────────────────

@bot.command()
@owner_only()
async def addcommunity(ctx):
    guild = ctx.guild

    existing = discord.utils.get(guild.categories, name="📋 COMMUNITY")
    if existing:
        await ctx.send("❌ Community channels already exist! Check the **📋 COMMUNITY** category.")
        return

    progress = await ctx.send("📋 Creating community channels...")
    try:
        community_created = {"roles": [], "categories": [], "channels": []}
        community_cat = await create_community_channels(guild, created=community_created)

        # Update undo history with newly created community channels
        if not hasattr(bot, 'last_build') or not bot.last_build:
            loaded = await db_load(str(guild.id))
            bot.last_build = loaded.get("last_build") or {"roles": [], "categories": [], "channels": []}
        bot.last_build.setdefault("categories", []).extend(community_created["categories"])
        bot.last_build.setdefault("channels", []).extend(community_created["channels"])
        await db_save(str(guild.id), {"last_build": bot.last_build})

        # Move orphan get-your-roles / tickets channels into the community category
        moved = []
        for ch in list(guild.text_channels):
            if ch.category_id is not None:
                continue
            if "get-your-roles" in ch.name or "tickets" in ch.name:
                await ch.edit(category=community_cat)
                moved.append(ch.name)
                await asyncio.sleep(0.3)

        moved_text = ("\n\nAlso moved into this category: " + ", ".join(f"`{m}`" for m in moved)) if moved else ""
        embed = discord.Embed(
            title="✅ Community Channels Added!",
            description=(
                "Created **📋 COMMUNITY** category with:\n\n"
                " **announcements** — staff-only posting\n"
                "💡 **suggestions** — forum (members post threads)\n"
                "🐛 **bug-reports** — forum (members post threads)\n"
                "⭐ **reviews** — forum (members post threads)\n\n"
                "Use `!announce` to post a styled announcement!" + moved_text
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="Architect AI • !guide for all commands")
        await progress.edit(content=None, embed=embed)
    except Exception as e:
        await progress.edit(content=f"❌ Error: {str(e)}")


# ── !announce COMMAND + MODAL ─────────────────────────────────────────────────────────────

class AnnounceCardView(discord.ui.View):
    """Persistent card shown by !announce. Clicking opens an ephemeral setup step."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✍️ Write Announcement", style=discord.ButtonStyle.primary, custom_id="write_announcement_btn")
    async def write_announcement(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only staff can write announcements!", ephemeral=True)
            return
        view = AnnounceSetupView(interaction.guild)
        embed = discord.Embed(
            title="📢 Configure Announcement",
            description=(
                "**1.** Pick a target channel (leave empty to post in the current channel)\n"
                "**2.** Pick who to mention (optional)\n"
                "**3.** Click **✍️ Continue** to write your title and message"
            ),
            color=discord.Color.from_rgb(255, 127, 80)
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AnnounceSetupView(discord.ui.View):
    """Ephemeral two-select step: choose channel + mention role, then open modal."""
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=120)
        self.selected_channel_id = None
        self.selected_role_value = "none"

        # Channel select (built-in Discord channel picker)
        channel_select = discord.ui.ChannelSelect(
            placeholder="📢 Target channel (leave blank = post here)...",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=0,
            max_values=1,
            custom_id="announce_channel_pick"
        )
        channel_select.callback = self.on_channel
        self.add_item(channel_select)

        # Mention select: @everyone / @here / No mention / guild roles
        mention_options = [
            discord.SelectOption(label="No mention", value="none", emoji="🔕", default=True),
            discord.SelectOption(label="@everyone", value="everyone", emoji="📢"),
            discord.SelectOption(label="@here", value="here", emoji="📣"),
        ]
        for role in reversed(guild.roles):
            if role.is_default() or role.managed:
                continue
            if role.permissions.administrator:
                continue
            if len(mention_options) >= 25:
                break
            mention_options.append(discord.SelectOption(label=role.name[:100], value=f"role:{role.id}"))

        role_select = discord.ui.Select(
            placeholder="🔔 Who to mention (optional)...",
            options=mention_options,
            min_values=1,
            max_values=1,
            custom_id="announce_role_pick"
        )
        role_select.callback = self.on_role
        self.add_item(role_select)

    async def on_channel(self, interaction: discord.Interaction):
        values = interaction.data.get("values", [])
        self.selected_channel_id = int(values[0]) if values else None
        await interaction.response.defer()

    async def on_role(self, interaction: discord.Interaction):
        values = interaction.data.get("values", [])
        self.selected_role_value = values[0] if values else "none"
        await interaction.response.defer()

    @discord.ui.button(label="✍️ Continue", style=discord.ButtonStyle.green, custom_id="announce_continue_btn")
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            AnnounceModal(self.selected_channel_id, self.selected_role_value)
        )


class AnnounceModal(discord.ui.Modal, title="📢 Write Announcement"):
    """Final modal: just title + message. Channel and role already chosen via dropdowns."""
    def __init__(self, channel_id=None, role_value="none"):
        super().__init__()
        self.channel_id = channel_id
        self.role_value = role_value or "none"

    announcement_title = discord.ui.TextInput(
        label="Title",
        placeholder="e.g. Server Update, New Event, Maintenance...",
        required=True,
        max_length=256
    )

    announcement_message = discord.ui.TextInput(
        label="Message",
        placeholder="Write your announcement here...",
        required=True,
        max_length=4000,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild

        # Resolve target channel
        target = None
        if self.channel_id:
            target = guild.get_channel(self.channel_id)
        if not target:
            target = interaction.channel

        # Resolve mention
        mention_text = ""
        if self.role_value == "everyone":
            mention_text = "@everyone"
        elif self.role_value == "here":
            mention_text = "@here"
        elif self.role_value and self.role_value.startswith("role:"):
            try:
                role_id = int(self.role_value.split(":")[1])
                role = guild.get_role(role_id)
                if role:
                    mention_text = role.mention
            except (ValueError, IndexError):
                pass

        # Build styled coral embed
        embed = discord.Embed(
            title=f"📢 {self.announcement_title.value}",
            description=self.announcement_message.value,
            color=discord.Color.from_rgb(255, 127, 80)
        )
        embed.set_footer(
            text=f"Announced by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url
        )
        embed.timestamp = discord.utils.utcnow()

        try:
            await target.send(content=mention_text if mention_text else None, embed=embed)
            await interaction.followup.send(
                f"✅ Announcement posted in {target.mention}!",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ I don't have permission to post in {target.mention}!",
                ephemeral=True
            )


@bot.command()
@staff_only()
async def announce(ctx):
    embed = discord.Embed(
        title="📢 Announcement Creator",
        description=(
            "Click the button below to write and post a styled announcement.\n\n"
            "**You can:**\n"
            "• Pick the target channel from a dropdown\n"
            "• Mention @everyone, @here, or any role from a dropdown\n"
            "• Write a custom title and message\n"
        ),
        color=discord.Color.from_rgb(255, 127, 80)
    )
    embed.set_footer(text="Only Admin and Moderator can use this")
    await ctx.send(embed=embed, view=AnnounceCardView())


@bot.command(name="guide")
async def guide(ctx):
    embed = discord.Embed(
        title="🏗️ Architect AI",
        description=(
            "I build fully customized Discord servers from scratch!\n\n"
            "**Getting Started:**\n"
            "Type `!setup` and pick a template — a popup will guide you!\n\n"
            "Select a category below to see commands:"
        ),
        color=discord.Color.blurple()
    )
    embed.add_field(name="🏗️ Setup", value="Server building & community commands", inline=True)
    embed.add_field(name="🔨 Moderation", value="Kick, ban, warn, announce & more", inline=True)
    embed.add_field(name="🎮 Fun & Levels", value="XP, giveaways, games", inline=True)
    embed.set_footer(text="Architect AI • Built with discord.py + Groq")
    await ctx.send(embed=embed, view=GuideView())


class AutoModMenuView(discord.ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=60)
        self.config = config

    @discord.ui.button(label="⚙️ Configure", style=discord.ButtonStyle.primary, custom_id="automod_configure")
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AutoModConfigModal(self.config))

    @discord.ui.button(label="✅ Enable", style=discord.ButtonStyle.green, custom_id="automod_enable")
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        data = await db_load(guild_id)
        config = data.get("automod", self.config)
        config["enabled"] = True
        await db_save(guild_id, {"automod": config})
        bot.automod_cache[guild_id] = config
        embed = discord.Embed(
            title="🛡️ Auto-Mod Enabled!",
            description="Auto-mod is now active and watching messages.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="❌ Disable", style=discord.ButtonStyle.red, custom_id="automod_disable")
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        data = await db_load(guild_id)
        config = data.get("automod", self.config)
        config["enabled"] = False
        await db_save(guild_id, {"automod": config})
        bot.automod_cache[guild_id] = config
        embed = discord.Embed(
            title="🛡️ Auto-Mod Disabled!",
            description="Auto-mod has been turned off.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="➕ Add Banned Word", style=discord.ButtonStyle.secondary, custom_id="automod_addword")
    async def add_word(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddBannedWordModal())

    @discord.ui.button(label="🗑️ Remove Banned Word", style=discord.ButtonStyle.secondary, custom_id="automod_removeword")
    async def remove_word(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild.id)
        data = await db_load(guild_id)
        config = data.get("automod", {})
        banned = config.get("banned_words", [])
        if not banned:
            await interaction.response.send_message(
                "❌ No custom banned words yet! Add some first.",
                ephemeral=True
            )
            return
        options = [
            discord.SelectOption(label=word, value=word)
            for word in banned[:25]
        ]
        view = RemoveBannedWordView(options)
        await interaction.response.send_message(
            "🗑️ Which word do you want to remove?",
            view=view,
            ephemeral=True
        )


class AutoModConfigModal(discord.ui.Modal, title="⚙️ Configure Auto-Mod"):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config

    block_spam = discord.ui.TextInput(
        label="Block Spam? (yes/no)",
        placeholder="yes",
        required=False,
        max_length=3,
        default="yes"
    )

    block_caps = discord.ui.TextInput(
        label="Block Caps Spam? (yes/no)",
        placeholder="yes",
        required=False,
        max_length=3,
        default="yes"
    )

    block_links = discord.ui.TextInput(
        label="Block Links? (yes/no)",
        placeholder="no",
        required=False,
        max_length=3,
        default="no"
    )

    block_mentions = discord.ui.TextInput(
        label="Block Mass Mentions? (yes/no)",
        placeholder="yes",
        required=False,
        max_length=3,
        default="yes"
    )

    warn_threshold = discord.ui.TextInput(
        label="Warnings before timeout (1-10)",
        placeholder="3",
        required=False,
        max_length=2,
        default="3"
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = str(interaction.guild.id)
        data = await db_load(guild_id)
        config = data.get("automod", self.config)

        config["block_spam"] = self.block_spam.value.strip().lower() != "no"
        config["block_caps"] = self.block_caps.value.strip().lower() != "no"
        config["block_links"] = self.block_links.value.strip().lower() == "yes"
        config["block_mentions"] = self.block_mentions.value.strip().lower() != "no"

        try:
            threshold = int(self.warn_threshold.value.strip())
            config["warn_threshold"] = max(1, min(10, threshold))
        except:
            config["warn_threshold"] = 3

        await db_save(guild_id, {"automod": config})
        bot.automod_cache[guild_id] = config

        embed = discord.Embed(
            title="✅ Auto-Mod Configured!",
            color=discord.Color.green()
        )
        embed.add_field(name="Anti-Spam", value="✅" if config["block_spam"] else "❌", inline=True)
        embed.add_field(name="Anti-Caps", value="✅" if config["block_caps"] else "❌", inline=True)
        embed.add_field(name="Anti-Links", value="✅" if config["block_links"] else "❌", inline=True)
        embed.add_field(name="Anti-Mentions", value="✅" if config["block_mentions"] else "❌", inline=True)
        embed.add_field(name="Warn Threshold", value=str(config["warn_threshold"]), inline=True)
        await interaction.followup.send(embed=embed)


class AddBannedWordModal(discord.ui.Modal, title="➕ Add Banned Word"):
    word = discord.ui.TextInput(
        label="Word to ban",
        placeholder="Enter the word...",
        required=True,
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = str(interaction.guild.id)
        data = await db_load(guild_id)
        config = data.get("automod", {})
        banned = config.get("banned_words", [])
        word = self.word.value.strip().lower()

        if word in banned:
            await interaction.followup.send(
                f"❌ **{word}** is already banned!",
                ephemeral=True
            )
            return

        banned.append(word)
        config["banned_words"] = banned
        await db_save(guild_id, {"automod": config})
        await interaction.followup.send(
            f"✅ Added **{word}** to banned words! ({len(banned)} total)",
            ephemeral=True
        )


class RemoveBannedWordView(discord.ui.View):
    def __init__(self, options: list):
        super().__init__(timeout=60)
        select = discord.ui.Select(
            placeholder="Choose a word to remove...",
            options=options,
            custom_id="remove_word_select"
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        word = interaction.data["values"][0]
        guild_id = str(interaction.guild.id)
        data = await db_load(guild_id)
        config = data.get("automod", {})
        banned = config.get("banned_words", [])

        if word in banned:
            banned.remove(word)
            config["banned_words"] = banned
            await db_save(guild_id, {"automod": config})
            await interaction.response.edit_message(
                content=f"✅ Removed **{word}** from banned words!",
                view=None
            )
        else:
            await interaction.response.edit_message(
                content=f"❌ Word not found!",
                view=None
            )


@bot.command()
@staff_only()
async def automod(ctx):
    guild_id = str(ctx.guild.id)
    data = await db_load(guild_id)
    config = data.get("automod", {
        "enabled": False,
        "block_links": False,
        "block_caps": True,
        "block_spam": True,
        "block_mentions": True,
        "warn_threshold": 3,
        "banned_words": []
    })

    status = "✅ Enabled" if config.get("enabled") else "❌ Disabled"
    banned_count = len(config.get("banned_words", []))

    embed = discord.Embed(
        title="🛡️ Auto-Mod",
        description=f"**Status:** {status}",
        color=discord.Color.green() if config.get("enabled") else discord.Color.red()
    )
    embed.add_field(name="🚫 Anti-Spam", value="✅" if config.get("block_spam", True) else "❌", inline=True)
    embed.add_field(name="🔠 Anti-Caps", value="✅" if config.get("block_caps", True) else "❌", inline=True)
    embed.add_field(name="🔗 Anti-Links", value="✅" if config.get("block_links", False) else "❌", inline=True)
    embed.add_field(name="📢 Anti-Mentions", value="✅" if config.get("block_mentions", True) else "❌", inline=True)
    embed.add_field(name="🤬 Slur Filter", value="✅ Always On", inline=True)
    embed.add_field(name="⚠️ Warn Threshold", value=str(config.get("warn_threshold", 3)), inline=True)
    embed.add_field(name="📝 Custom Banned Words", value=str(banned_count), inline=True)
    embed.set_footer(text="Use the buttons below to configure")
    bot.automod_cache[guild_id] = config
    await ctx.send(embed=embed, view=AutoModMenuView(config))


bot.run(os.getenv("DISCORD_TOKEN"))
