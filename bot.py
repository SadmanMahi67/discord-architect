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

# ── The system prompt we send to the LLM ──────────────────────────────────────
SYSTEM_PROMPT = """
You are a Discord server architect. Convert the user's request into a JSON server template.

Return ONLY valid JSON, no explanation, no markdown, no code blocks. Just raw JSON.

Use this exact structure:
{
  "server_name": "Server Name Here",
  "roles": [
    {"name": "Role Name", "color": "0xHEXCOLOR", "mentionable": true}
  ],
  "categories": [
    {
      "name": "CATEGORY NAME",
      "channels": [
        {"name": "channel-name", "type": "text", "topic": "Channel description"},
        {"name": "Voice Room", "type": "voice"}
      ]
    }
  ],
  "roles_channel": "get-your-roles"
}

Rules:
- Channel names must be lowercase with hyphens, no spaces
- Include 2-4 categories based on the theme
- Include 3-5 channels per category
- Always include a General category
- Always include at least one voice channel per category
- Pick role colors that match the theme
- roles_channel is always "get-your-roles"
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

class RoleButton(discord.ui.Button):
    def __init__(self, role_name: str, role_id: int):
        super().__init__(
            label=role_name,
            style=discord.ButtonStyle.primary,
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

        if role in member.roles:
            await member.remove_roles(role)
            await interaction.response.send_message(
                f"✅ Removed **{role.name}** from you!", ephemeral=True
            )
        else:
            await member.add_roles(role)
            await interaction.response.send_message(
                f"🎉 You now have **{role.name}**!", ephemeral=True
            )

class RoleView(discord.ui.View):
    def __init__(self, roles: list):
        super().__init__(timeout=None)
        for role in roles:
            self.add_item(RoleButton(role["name"], role["id"]))

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    bot.add_view(RoleView([]))

@bot.command()
async def hello(ctx):
    await ctx.send("Hey! I'm Architect AI. Ready to build your server 🏗️")

@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! Latency: {round(bot.latency * 1000)}ms")

@bot.command()
async def setup(ctx, *, user_input: str):
    # Tell the user we're thinking
    thinking_msg = await ctx.send("🧠 Thinking up your server layout...")

    try:
        # Send to Groq
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input}
            ]
        )
        raw_text = response.choices[0].message.content

        # Parse the JSON
        server_template = extract_json(raw_text)

        if not server_template:
            await thinking_msg.edit(content="❌ Couldn't parse the AI response. Try again!")
            return

        # Pretty print the JSON so user can see the plan
        pretty = json.dumps(server_template, indent=2)

        embed = discord.Embed(
            title="🏗️ Server Plan Ready!",
            description=f"```json\n{pretty[:1800]}\n```",
            color=discord.Color.blue()
        )
        embed.add_field(name="Server Name", value=server_template.get("server_name", "Unknown"), inline=True)
        embed.add_field(name="Roles", value=str(len(server_template.get("roles", []))), inline=True)
        embed.add_field(name="Categories", value=str(len(server_template.get("categories", []))), inline=True)
        embed.set_footer(text="Type !confirm to build it • !cancel to scrap it")
        await thinking_msg.edit(content=None, embed=embed)

        # Save the template temporarily so !confirm can use it
        bot.pending_template = server_template
        bot.pending_ctx = ctx

    except Exception as e:
        await thinking_msg.edit(content=f"❌ Error: {str(e)}")

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

        # Step 1 — Create Roles
        await progress_msg.edit(content="🎨 Creating roles...")
        for role_data in template.get("roles", []):
            color_value = int(role_data.get("color", "0x3498db"), 16)
            role = await guild.create_role(
                name=role_data["name"],
                color=discord.Color(color_value),
                mentionable=role_data.get("mentionable", True)
            )
            created["roles"].append(role.id)
            await asyncio.sleep(0.5)

        await progress_msg.edit(content="📁 Creating categories and channels...")

        # Step 2 — Create Categories and Channels
        for category_data in template.get("categories", []):
            category = await guild.create_category(category_data["name"])
            created["categories"].append(category.id)

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

        # Step 3 — Create get-your-roles channel
        await progress_msg.edit(content="🎭 Setting up roles channel...")
        roles_channel = await guild.create_text_channel(
            name=template.get("roles_channel", "get-your-roles")
        )
        created["channels"].append(roles_channel.id)

        # Build roles list with actual IDs from the guild
        created_roles = []
        for role_data in template.get("roles", []):
            role = discord.utils.get(guild.roles, name=role_data["name"])
            if role:
                created_roles.append({"name": role.name, "id": role.id})

        view = RoleView(created_roles)
        await roles_channel.send(
            content="**🎭 Get Your Roles!**\n\nClick a button to get or remove a role!",
            view=view
        )

        # Step 4 — Save state for undo
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
        embed.set_footer(text="Type !undo to revert everything")
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

@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="🏗️ Architect AI — Commands",
        description="I build Discord servers from a single sentence!",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="!setup <description>",
        value="Describe your server and I'll generate a plan\nExample: `!setup make a gaming server for 5 friends`",
        inline=False
    )
    embed.add_field(
        name="!confirm",
        value="Build the server from the generated plan",
        inline=False
    )
    embed.add_field(
        name="!cancel",
        value="Scrap the current plan without building",
        inline=False
    )
    embed.add_field(
        name="!undo",
        value="Delete everything the bot just built and revert to blank",
        inline=False
    )
    embed.set_footer(text="Architect AI • Built with discord.py + Groq")
    await ctx.send(embed=embed)

bot.run(os.getenv("DISCORD_TOKEN"))