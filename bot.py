import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
from groq import Groq
import json
import re
import asyncio
from discord.ui import Button, View

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
            custom_id=f"template_{key}"
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
        super().__init__(timeout=60)
        for key, data in SERVER_TEMPLATES.items():
            self.add_item(TemplateButton(key, data))

class RoleButton(discord.ui.Button):
    def __init__(self, role_name: str, role_id: int, style=discord.ButtonStyle.primary):
        super().__init__(
            label=role_name,
            style=style,
            custom_id=f"role_{role_id}"
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        role = guild.get_role(self.role_id)
        member = interaction.user

        if role is None:
            await interaction.response.send_message("❌ Role not found!", ephemeral=True)
            return

        # Detect if this is a color role by checking its name starts with a pastel emoji
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
                # Remove all other color roles first so only one color shows
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

class RoleView(discord.ui.View):
    def __init__(self, roles: list, color_roles: list = []):
        super().__init__(timeout=None)
        for role in roles:
            self.add_item(RoleButton(role["name"], role["id"], discord.ButtonStyle.primary))
        for role in color_roles:
            self.add_item(RoleButton(role["name"], role["id"], discord.ButtonStyle.secondary))

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    bot.add_view(RoleView([]))
    # Load saved state so member role survives restarts
    state = load_state()
    if "member_role_id" in state:
        bot.member_role_id = state["member_role_id"]
        print(f"✅ Loaded member role ID: {bot.member_role_id}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="!guide for commands"
        )
    )

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

        view = RoleView(decorative_roles, color_roles)
        await roles_channel.send(
            content=(
                "**🎭 Get Your Roles!**\n\n"
                "🔵 **Identity Roles** — Pick what describes you!\n"
                "⚪ **Color Roles** — Pick your color! (one at a time)\n\n"
                "Click any button to get or remove a role!"
            ),
            view=view
        )

        # Step 5 — Save state for undo
        bot.last_build = created
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


bot.run(os.getenv("DISCORD_TOKEN"))