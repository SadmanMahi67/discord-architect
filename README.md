# Discord Architect

An AI-powered Discord bot that scaffolds and manages Discord servers. It uses Groq (LLaMA 3.3 70B) to generate server structures from a plain-English description, and includes moderation, leveling, ticketing, giveaways, auto-mod, and more.

## Features

- **AI Server Builder** — Describe your server and the bot generates roles, categories, and channels with permissions. Undo the last build with `!undo`, or wipe everything with `!nuke`.
- **Moderation** — `!kick`, `!ban`, `!timeout`, `!untimeout`, `!warn`, `!promote`, `!demote`, `!addrole`, `!removerole`. All actions are logged to `#mod-logs`.
- **Auto-Mod** — Configurable spam, caps, link, mass-mention, and slur detection with warning thresholds and auto-timeout.
- **Level System** — XP tracking per server with rank card image generation (`!rank`).
- **Ticket System** — Button-based ticket creation with transcript logging on close.
- **Giveaways** — `!giveaway`, `!gend`, `!greroll` with persistent entries stored in MongoDB.
- **Server Stats** — Auto-updating voice channels displaying member/online/bot/channel/role counts (refreshed every 10 minutes).
- **Edit Menu** — `!edit` opens a UI to add, rename, or delete channels, categories, and roles without rebuilding.
- **Fun Commands** — `!coinflip`, `!pick`, `!poll`, `!quote`, `!topic`.

## Prerequisites

- Python 3.10+
- A [Discord application & bot token](https://discord.com/developers/applications)
- A [MongoDB](https://www.mongodb.com/atlas) database (free tier works)
- A [Groq](https://console.groq.com) API key (free tier works)

## Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/your-username/discord-architect.git
   cd discord-architect
   ```

2. **Create and activate a virtual environment**

   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**

   Copy the example file and fill in your credentials:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`:

   ```
   DISCORD_TOKEN=your_discord_bot_token
   MONGODB_URL=your_mongodb_connection_string
   GROQ_API_KEY=your_groq_api_key
   ```

5. **Invite the bot to your server**

   In the Discord Developer Portal, generate an OAuth2 URL with the `bot` scope and the following permissions:
   - Manage Roles
   - Manage Channels
   - Kick Members / Ban Members
   - Moderate Members (Timeout)
   - Send Messages, Embed Links, Attach Files
   - Read Message History
   - Connect (for stats voice channels)

6. **Run the bot**
   ```bash
   python bot.py
   ```

## Deployment (Heroku / Railway / Render)

The included `Procfile` targets worker dynos:

```
worker: python bot.py
```

Set the three environment variables (`DISCORD_TOKEN`, `MONGODB_URL`, `GROQ_API_KEY`) in your platform's dashboard instead of using a `.env` file.

## Environment Variables

| Variable        | Description                      |
| --------------- | -------------------------------- |
| `DISCORD_TOKEN` | Your Discord bot token           |
| `MONGODB_URL`   | MongoDB connection string        |
| `GROQ_API_KEY`  | Groq API key for LLaMA inference |

## License

MIT
