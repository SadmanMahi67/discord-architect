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

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

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

LEVELS_FILE = "levels.json"

def load_levels() -> dict:
    if os.path.exists(LEVELS_FILE):
        with open(LEVELS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_levels(data: dict):
    with open(LEVELS_FILE, "w") as f:
        json.dump(data, f, indent=2)

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
        "hint": "professional business server with project channels, team meetings, announcements and file sharing"
    },
    "creative": {
        "emoji": "🎨",
        "label": "Creative",
        "description": "A creative community for artists, writers and musicians",
        "hint": "creative server with art sharing, writing workshops, music rooms, and feedback channels"
    },
    "custom": {
        "emoji": "🎲",
        "label": "Custom",
        "description": "Describe your own server from scratch",
        "hint": None
    }
}

# ── The system prompt we send to the LLM ──────────────────────────────────────
SYSTEM_PROMPT = """
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
      "name": "╔══〔 🎮 GAMING ZONE 〕══╗",
      "channels": [
        {"name": "「🎮」game-chat", "type": "text", "topic": "Channel description"},
        {"name": "🔊・voice-lounge", "type": "voice"}
      ]
    }
  ],
  "roles_channel": "get-your-roles"
}

Rules:
- Channel names must be lowercase with hyphens after any emoji/decoration
- Category names should have decorative borders/emojis that match the server theme
- Text channel names should have a relevant emoji prefix like 「🎮」or 📚・
- Voice channel names should start with 🔊・
- Include 2-4 categories based on the theme
- Include 3-5 channels per category
- Always include a General category
- Always include a 「👋」welcome channel in the General category as the first channel
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
- Always include a private staff category at the end called "〔🔒〕STAFF ONLY" with these channels:
  {"name": "「📋」mod-logs", "type": "text", "topic": "Moderation logs and actions", "staff_only": true},
  {"name": "「💬」staff-chat", "type": "text", "topic": "Private staff discussion", "staff_only": true},
  {"name": "「🤖」bot-commands", "type": "text", "topic": "Bot commands for staff", "staff_only": true},
  {"name": "🔊・staff-voice", "type": "voice", "staff_only": true}
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
        bot.selected_template = self.key

        if self.key == "custom":
            embed = discord.Embed(
                title="🎲 Custom Server",
                description="Describe your server in detail and I'll build it for you!",
                color=discord.Color.blurple()
            )
            embed.set_footer(text="Type !describe <your description> to continue")
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            embed = discord.Embed(
                title=f"{self.data['emoji']} {self.data['label']} Template Selected!",
                description=f"Any extra details to add? For example: number of members, specific games, topics etc.\n\nOr type `!build` to generate with defaults!",
                color=discord.Color.blurple()
            )
            embed.set_footer(text=f"Type !details <your extras> or !build to continue")
            await interaction.response.edit_message(embed=embed, view=None)

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

            pastel_emojis = ["🌸", "🍋", "🍵", "🩵", "🍇", "🍑", "🤍"]
            is_color_role = any(role.name.startswith(emoji) for emoji in pastel_emojis)

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
                        if any(r.name.startswith(emoji) for emoji in pastel_emojis)
                    ]
                    if color_roles_to_remove:
                        await member.remove_roles(*color_roles_to_remove)
                await member.add_roles(role)
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

    # Load saved role data first
    state = load_state()
    saved_decorative = state.get("decorative_roles", [])
    saved_color = state.get("color_roles", [])
    bot.decorative_roles = saved_decorative
    bot.color_roles = saved_color

    # Register views safely one by one
    views_to_register = [
        RoleView(saved_decorative, []),
        RoleView([], saved_color),
        TemplateView(),
        TicketOpenView(),
        TicketCloseView(),
        TicketConfirmCloseView()
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
                {"role": "system", "content": SYSTEM_PROMPT},
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
async def setup(ctx):
    embed = discord.Embed(
        title="🏗️ Architect AI — Choose a Template",
        description="Pick a theme to get started! The AI will customize it based on your choice.",
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
async def cancel(ctx):
    bot.pending_template = None
    embed = discord.Embed(
            title="❌ Cancelled",
            description="No changes were made. Run `!setup` again whenever you're ready!",
            color=discord.Color.orange()
        )
    await ctx.send(embed=embed)

@bot.command()
async def confirm(ctx):
    if not hasattr(bot, 'pending_template') or bot.pending_template is None:
        await ctx.send("❌ No pending server plan! Run `!setup` first.")
        return

    template = bot.pending_template
    guild = ctx.guild

    progress_msg = await ctx.send("🏗️ Building your server... please wait!")

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

        for role_data in template.get("roles", []):
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

        # Give creator Admin
        if admin_role:
            await ctx.author.add_roles(admin_role)

        # Give everyone else Member
        if member_role:
            for m in ctx.guild.members:
                if m == ctx.author or m.bot:
                    continue
                await m.add_roles(member_role)
                await asyncio.sleep(0.3)

        # Save member role id for future joins
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

        # Step 4 — Create get-your-roles channel with only decorative roles
        await progress_msg.edit(content="🎭 Setting up roles channel...")
        roles_channel = await guild.create_text_channel(
            name=template.get("roles_channel", "get-your-roles")
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

        # Send identity roles as first message
        identity_view = RoleView(decorative_roles, [])
        await roles_channel.send(
            content=(
                "**🎭 Identity Roles**\n\n"
                "Pick what describes you!\n"
                "Click to get or remove a role!"
            ),
            view=identity_view
        )

        # Send color roles as second message
        color_view = RoleView([], color_roles)
        await roles_channel.send(
            content=(
                "**🎨 Color Roles**\n\n"
                "Pick your color! Choosing a new one removes the old one automatically!"
            ),
            view=color_view
        )

        # Save role data to state.json so buttons survive restarts
        save_state({
            "decorative_roles": decorative_roles,
            "color_roles": color_roles
        })
        bot.decorative_roles = decorative_roles
        bot.color_roles = color_roles

        # Step 5 — Save state for undo
        bot.last_build = created
        bot.last_template = template
        bot.pending_template = None

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
            value="Use `!edit` to modify without rebuilding!\nExamples:\n`!edit add a movie-night channel`\n`!edit rename general-chat to lobby`\n`!edit add a new category called MUSIC ZONE`",
            inline=False
        )
        embed.set_footer(text="Type !undo to revert everything • !guide to see all commands")
        await progress_msg.edit(content=None, embed=embed)

    except Exception as e:
        await progress_msg.edit(content=f"❌ Something went wrong: {str(e)}")

@bot.command()
async def undo(ctx):
    if not hasattr(bot, 'last_build') or bot.last_build is None:
        await ctx.send("❌ Nothing to undo! Build a server first with `!setup`.")
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

@bot.command(name="guide")
async def guide(ctx):
    embed = discord.Embed(
        title="🏗️ Architect AI — Commands",
        description="I build Discord servers from a single sentence!",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="!setup",
        value="Choose a server template to get started\nExample: `!setup` → pick a theme → `!build`",
        inline=False
    )
    embed.add_field(
        name="!details <extras>",
        value="Add extra details after picking a template\nExample: `!details for 10 friends who play Valorant`",
        inline=False
    )
    embed.add_field(
        name="!describe <description>",
        value="Skip templates and describe your server from scratch\nExample: `!describe a cozy anime server for 5 friends`",
        inline=False
    )
    embed.add_field(
        name="!confirm",
        value="Build the server from the generated plan",
        inline=False
    )
    embed.add_field(
        name="!edit <instruction>",
        value="Modify your server without rebuilding everything\nExample: `!edit add a movie-night channel`\nExample: `!edit rename general-chat to lobby`\nExample: `!edit add a new category called MUSIC ZONE`",
        inline=False
    )
    embed.add_field(
        name="!undo",
        value="Delete everything the bot built and revert to blank",
        inline=False
    )
    embed.add_field(
        name="!cancel",
        value="Scrap the current plan without building",
        inline=False
    )
    embed.add_field(name="📖 More Commands", value="`!modguide` — Moderation commands\n`!funguide` — Fun & misc commands", inline=False)
    embed.add_field(
        name="💣 !nuke",
        value="Wipes all channels and roles so you can start fresh with !setup\nOnly the server owner can use this\nExample: `!nuke` → `!confirmnuke` or `!cancelnuke`",
        inline=False
    )
    embed.add_field(
        name="🎫 !ticket setup",
        value="Creates the ticket channel so users can open support tickets\nAlso: `!ticket add @user` and `!ticket remove @user`",
        inline=False
    )
    embed.add_field(
        name="🔄 !redo",
        value="Rebuilds the server using the last template after an undo\nExample: `!undo` → `!redo` → `!confirmredo`",
        inline=False
    )
    embed.add_field(
        name="📊 !serverstats",
        value="Creates a live stats display at the top of your server\nAlso: `!updatestats` to force refresh, `!removestats` to remove",
        inline=False
    )
    embed.add_field(
        name="🔄 !refreshroles",
        value="Fixes role buttons after a bot restart or rebuild",
        inline=False
    )
    embed.set_footer(text="Architect AI • Built with discord.py + Groq")
    await ctx.send(embed=embed)

@bot.event
async def on_member_join(member):
    # Auto assign member role if it exists
    if hasattr(bot, 'member_role_id') and bot.member_role_id:
        member_role = member.guild.get_role(bot.member_role_id)
        if member_role:
            try:
                await member.add_roles(member_role)
            except:
                pass

    guild = member.guild

    # Find welcome channel — looks for one named welcome or general
    welcome_channel = discord.utils.get(guild.text_channels, name="welcome")
    if not welcome_channel:
        welcome_channel = discord.utils.get(guild.text_channels, name="「👋」welcome")
    if not welcome_channel:
        # Fall back to first available text channel
        welcome_channel = guild.text_channels[0] if guild.text_channels else None

    if not welcome_channel:
        return

    embed = discord.Embed(
        title=f"👋 Welcome to {guild.name}!",
        description=(
            f"Hey {member.mention}, we're glad you're here!\n\n"
            f"📋 Check out the rules channel to get started.\n"
            f"🎭 Head to **#get-your-roles** to pick your roles.\n"
            f"💬 Introduce yourself and say hi!"
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
async def edit(ctx, *, instruction: str):
    guild = ctx.guild
    progress_msg = await ctx.send(f"✏️ Processing: *{instruction}*...")

    try:
        action_data = await parse_edit_instruction(instruction, guild)

        if not action_data:
            await progress_msg.edit(content="❌ Couldn't understand that instruction. Try being more specific!")
            return

        action = action_data.get("action")

        # Add a channel
        if action == "add_channel":
            category = discord.utils.get(guild.categories, name=action_data.get("category"))
            if action_data.get("type") == "voice":
                channel = await guild.create_voice_channel(
                    name=action_data["name"],
                    category=category
                )
            else:
                channel = await guild.create_text_channel(
                    name=action_data["name"],
                    category=category,
                    topic=action_data.get("topic", "")
                )
            embed = discord.Embed(
                title="✅ Channel Added!",
                description=f"Created **{channel.name}** in **{category.name if category else 'No Category'}**",
                color=discord.Color.green()
            )

        # Add a category
        elif action == "add_category":
            category = await guild.create_category(action_data["name"])
            embed = discord.Embed(
                title="✅ Category Added!",
                description=f"Created category **{category.name}**",
                color=discord.Color.green()
            )

        # Rename a channel
        elif action == "rename_channel":
            channel = discord.utils.get(guild.channels, name=action_data["old_name"])
            if not channel:
                await progress_msg.edit(content=f"❌ Couldn't find channel **{action_data['old_name']}**")
                return
            old_name = channel.name
            await channel.edit(name=action_data["new_name"])
            embed = discord.Embed(
                title="✅ Channel Renamed!",
                description=f"**{old_name}** → **{action_data['new_name']}**",
                color=discord.Color.green()
            )

        # Rename a category
        elif action == "rename_category":
            category = discord.utils.get(guild.categories, name=action_data["old_name"])
            if not category:
                await progress_msg.edit(content=f"❌ Couldn't find category **{action_data['old_name']}**")
                return
            old_name = category.name
            await category.edit(name=action_data["new_name"])
            embed = discord.Embed(
                title="✅ Category Renamed!",
                description=f"**{old_name}** → **{action_data['new_name']}**",
                color=discord.Color.green()
            )

        # Delete a channel
        elif action == "delete_channel":
            channel = discord.utils.get(guild.channels, name=action_data["name"])
            if not channel:
                await progress_msg.edit(content=f"❌ Couldn't find channel **{action_data['name']}**")
                return
            await channel.delete()
            embed = discord.Embed(
                title="🗑️ Channel Deleted!",
                description=f"Deleted **{action_data['name']}**",
                color=discord.Color.red()
            )

        # Delete a category
        elif action == "delete_category":
            category = discord.utils.get(guild.categories, name=action_data["name"])
            if not category:
                await progress_msg.edit(content=f"❌ Couldn't find category **{action_data['name']}**")
                return
            await category.delete()
            embed = discord.Embed(
                title="🗑️ Category Deleted!",
                description=f"Deleted **{action_data['name']}**",
                color=discord.Color.red()
            )

        # Add a role
        elif action == "add_role":
            color_value = int(action_data.get("color", "0x3498db"), 16)
            role = await guild.create_role(
                name=action_data["name"],
                color=discord.Color(color_value),
                mentionable=True
            )
            embed = discord.Embed(
                title="✅ Role Added!",
                description=f"Created role **{role.name}**",
                color=discord.Color.green()
            )

        else:
            await progress_msg.edit(content="❌ Unknown action. Try rephrasing your instruction!")
            return

        await progress_msg.edit(content=None, embed=embed)

    except Exception as e:
        await progress_msg.edit(content=f"❌ Something went wrong: {str(e)}")

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
    await log_channel.send(embed=embed)


# ── MOD COMMANDS ──────────────────────────────────────────────────────────────────────────────

@bot.command()
@commands.has_permissions(kick_members=True)
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
@commands.has_permissions(kick_members=True)
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
@commands.has_permissions(kick_members=True)
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
@commands.has_permissions(ban_members=True)
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
@commands.has_permissions(moderate_members=True)
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
@commands.has_permissions(moderate_members=True)
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
@commands.has_permissions(kick_members=True)
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
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command!")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found! Make sure you @mention them correctly.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument! Use `!guide` or `!modguide` for help.")


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


# ── GUIDE COMMANDS ────────────────────────────────────────────────────────────────────────────

@bot.command()
async def modguide(ctx):
    embed = discord.Embed(
        title="🔨 Architect AI — Mod Commands",
        description="Staff-only moderation commands",
        color=discord.Color.red()
    )
    embed.add_field(
        name="!promote @user mod/admin",
        value="Promotes a user to Mod or Admin\nExample: `!promote @John mod`",
        inline=False
    )
    embed.add_field(
        name="!demote @user",
        value="Removes all staff roles from a user\nExample: `!demote @John`",
        inline=False
    )
    embed.add_field(
        name="!kick @user reason",
        value="Kicks a member from the server\nExample: `!kick @John breaking rules`",
        inline=False
    )
    embed.add_field(
        name="!ban @user reason",
        value="Permanently bans a member\nExample: `!ban @John spamming`",
        inline=False
    )
    embed.add_field(
        name="!timeout @user duration reason",
        value="Mutes a member for a duration\nDuration formats: `30s` `10m` `2h` `1d`\nExample: `!timeout @John 10m spamming`",
        inline=False
    )
    embed.add_field(
        name="!untimeout @user",
        value="Removes a timeout early\nExample: `!untimeout @John`",
        inline=False
    )
    embed.add_field(
        name="!warn @user reason",
        value="Sends user a DM warning and logs it\nExample: `!warn @John please follow the rules`",
        inline=False
    )
    embed.add_field(
        name="!addrole @user role name",
        value="Manually gives a role to a user\nExample: `!addrole @John Moderator`",
        inline=False
    )
    embed.add_field(
        name="!removerole @user role name",
        value="Manually removes a role from a user\nExample: `!removerole @John Moderator`",
        inline=False
    )
    embed.set_footer(text="All mod actions are logged in #mod-logs • !guide for setup • !funguide for fun commands")
    await ctx.send(embed=embed)


@bot.command()
async def funguide(ctx):
    embed = discord.Embed(
        title="🎮 Architect AI — Fun Commands",
        description="Fun and misc commands for everyone",
        color=discord.Color.green()
    )
    embed.add_field(
        name="!coinflip",
        value="Flips a coin\nExample: `!coinflip`",
        inline=False
    )
    embed.add_field(
        name="!pick option1 option2 ...",
        value="Randomly picks one of your options\nExample: `!pick pizza burger sushi`",
        inline=False
    )
    embed.add_field(
        name='!poll "question" "option1" "option2"',
        value="Creates a poll with reactions\nExample: `!poll \"Best game?\" \"Valorant\" \"Minecraft\"`",
        inline=False
    )
    embed.add_field(
        name="!quote",
        value="Gets an AI-generated inspiring quote\nExample: `!quote`",
        inline=False
    )
    embed.add_field(
        name="!topic",
        value="Gets an AI-generated conversation starter\nExample: `!topic`",
        inline=False
    )
    embed.set_footer(text="Use !guide for setup commands • !modguide for mod commands")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(manage_roles=True)
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
@commands.has_permissions(manage_roles=True)
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
            await log_channel.send(embed=log_embed)

        await interaction.response.send_message("🔒 Closing ticket...")
        await asyncio.sleep(2)
        await channel.delete()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_close_ticket")
    async def cancel_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Ticket close cancelled!", ephemeral=True)


@bot.command()
@commands.has_permissions(administrator=True)
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
    for role in reversed(member.roles):
        if role.color.value != 0:
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

    levels = load_levels()
    if guild_id not in levels or user_id not in levels[guild_id]:
        await ctx.send(f"❌ {member.mention} hasn't earned any XP yet! Start chatting!")
        return

    data = levels[guild_id][user_id]
    guild_data = levels.get(guild_id, {})
    sorted_users = sorted(guild_data.items(), key=lambda x: x[1]["xp"], reverse=True)
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
        levels = load_levels()

        if guild_id not in levels or not levels[guild_id]:
            await ctx.send("❌ No one has earned XP yet! Start chatting!")
            return

        guild_data = levels[guild_id]

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

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)

    # XP cooldown — 60s per user to prevent spam farming
    cooldown_key = f"{guild_id}:{user_id}"
    now = asyncio.get_event_loop().time()
    last = bot.xp_cooldowns.get(cooldown_key, 0)
    if now - last >= 60:
        bot.xp_cooldowns[cooldown_key] = now

        levels = load_levels()
        if guild_id not in levels:
            levels[guild_id] = {}
        if user_id not in levels[guild_id]:
            levels[guild_id][user_id] = {"xp": 0, "level": 0, "messages": 0, "prestige": 0}

        xp_gain = random.randint(15, 25)
        levels[guild_id][user_id]["xp"] += xp_gain
        levels[guild_id][user_id]["messages"] = levels[guild_id][user_id].get("messages", 0) + 1

        old_level = get_level_from_xp(levels[guild_id][user_id]["xp"] - xp_gain)
        new_level = get_level_from_xp(levels[guild_id][user_id]["xp"])

        # Level up!
        if new_level > old_level:
            levels[guild_id][user_id]["level"] = new_level
            prestige = levels[guild_id][user_id].get("prestige", 0)

            # Check for prestige at level 50
            if new_level >= 50:
                prestige += 1
                levels[guild_id][user_id]["prestige"] = prestige
                levels[guild_id][user_id]["xp"] = 0
                levels[guild_id][user_id]["level"] = 0
                save_levels(levels)

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

            save_levels(levels)
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
            save_levels(levels)

    await bot.process_commands(message)


@bot.command()
async def redo(ctx):
    if not hasattr(bot, 'last_template') or bot.last_template is None:
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
async def confirmredo(ctx):
    if not hasattr(bot, 'redo_pending') or not bot.redo_pending:
        await ctx.send("❌ No redo pending! Run `!redo` first.")
        return

    bot.redo_pending = False
    bot.pending_template = bot.last_template
    bot.pending_ctx = ctx
    await ctx.send("🔄 Restoring last build...")
    await ctx.invoke(bot.get_command('confirm'))


@bot.command()
async def cancelredo(ctx):
    if not hasattr(bot, 'redo_pending') or not bot.redo_pending:
        await ctx.send("❌ No redo pending!")
        return
    bot.redo_pending = False
    await ctx.send("✅ Redo cancelled!")


# ── SERVER STATS ───────────────────────────────────────────────────────────────────────────

async def update_server_stats(guild: discord.Guild):
    try:
        state = load_state()
        category_id = state.get("stats_category_id") or getattr(bot, 'stats_category_id', None)
        if not category_id:
            return

        category = guild.get_channel(int(category_id))
        if not category:
            return

        total_members = len([m for m in guild.members if not m.bot])
        total_bots = len([m for m in guild.members if m.bot])
        online_members = len([m for m in guild.members if not m.bot and m.status != discord.Status.offline])
        total_channels = len(guild.text_channels) + len(guild.voice_channels)
        total_roles = len(guild.roles) - 1

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
@commands.has_permissions(administrator=True)
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
        total_members = len([m for m in guild.members if not m.bot])
        total_bots = len([m for m in guild.members if m.bot])
        online_members = len([m for m in guild.members if not m.bot and m.status != discord.Status.offline])
        total_channels = len(guild.text_channels) + len(guild.voice_channels)
        total_roles = len(guild.roles) - 1  # exclude @everyone

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
@commands.has_permissions(administrator=True)
async def updatestats(ctx):
    await update_server_stats(ctx.guild)
    await ctx.send("✅ Server stats updated!", delete_after=3)


@bot.command()
@commands.has_permissions(administrator=True)
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
@commands.has_permissions(administrator=True)
async def refreshroles(ctx):
    guild = ctx.guild
    state = load_state()
    decorative_roles = state.get("decorative_roles", [])
    color_roles = state.get("color_roles", [])

    if not decorative_roles and not color_roles:
        await ctx.send("❌ No saved role data found! Run `!setup` and `!confirm` first.")
        return

    # Find the roles channel
    roles_channel = discord.utils.get(guild.text_channels, name="get-your-roles")
    if not roles_channel:
        await ctx.send("❌ Couldn't find #get-your-roles channel!")
        return

    # Delete old messages
    await roles_channel.purge(limit=10)

    # Repost with fresh views
    identity_view = RoleView(decorative_roles, [])
    await roles_channel.send(
        content=(
            "**🎭 Identity Roles**\n\n"
            "Pick what describes you!\n"
            "Click to get or remove a role!"
        ),
        view=identity_view
    )

    color_view = RoleView([], color_roles)
    await roles_channel.send(
        content=(
            "**🎨 Color Roles**\n\n"
            "Pick your color! Choosing a new one removes the old one automatically!"
        ),
        view=color_view
    )

    await ctx.send("✅ Role buttons refreshed!", delete_after=3)


bot.run(os.getenv("DISCORD_TOKEN"))