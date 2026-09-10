import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "activity.db")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Paris")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
RANKING_CHANNEL_ID = int(os.getenv("RANKING_CHANNEL_ID", "0") or 0)

MESSAGE_POINTS = max(0, int(os.getenv("MESSAGE_POINTS", "1")))
MESSAGE_COOLDOWN_SECONDS = max(0, int(os.getenv("MESSAGE_COOLDOWN_SECONDS", "60")))
MESSAGE_MIN_CHARS = max(0, int(os.getenv("MESSAGE_MIN_CHARS", "5")))

IGNORED_TEXT_CHANNEL_IDS = {
    int(x) for x in os.getenv("IGNORED_TEXT_CHANNEL_IDS", "").split(",") if x.strip().isdigit()
}
IGNORED_VOICE_CHANNEL_IDS = {
    int(x) for x in os.getenv("IGNORED_VOICE_CHANNEL_IDS", "").split(",") if x.strip().isdigit()
}

TZ = ZoneInfo(TIMEZONE)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
last_message_score_at: dict[tuple[int, int], datetime] = {}


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialise les tables nécessaires.

    Les noms historiques `weekly_scores` et `announced_weeks` sont conservés pour
    rester compatibles avec la base existante. À partir de cette version,
    `week_key` contient une clé mensuelle au format YYYY-MM pour les nouvelles lignes.
    Les anciennes lignes hebdomadaires restent intactes dans la base.
    """
    with connect_db() as conn:
        conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS weekly_scores (
            guild_id INTEGER NOT NULL,
            week_key TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            text_points INTEGER NOT NULL DEFAULT 0,
            voice_points INTEGER NOT NULL DEFAULT 0,
            text_messages INTEGER NOT NULL DEFAULT 0,
            voice_minutes INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, week_key, user_id)
        );
        CREATE TABLE IF NOT EXISTS announced_weeks (
            guild_id INTEGER NOT NULL,
            week_key TEXT NOT NULL,
            announced_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, week_key)
        );
        CREATE TABLE IF NOT EXISTS user_activity (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_activity TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        """)


def current_month(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(TZ)
    return dt.strftime("%Y-%m")


def previous_month(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(TZ)
    first_day = dt.replace(day=1)
    return current_month(first_day - timedelta(days=1))


def month_label(month_key: str) -> str:
    try:
        year, month = month_key.split("-")
        names = [
            "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre",
        ]
        return f"{names[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return month_key


def add_text(guild_id: int, user_id: int):
    with connect_db() as conn:
        conn.execute("""
        INSERT INTO weekly_scores(guild_id, week_key, user_id, text_points, text_messages)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(guild_id, week_key, user_id) DO UPDATE SET
          text_points = text_points + excluded.text_points,
          text_messages = text_messages + 1
        """, (guild_id, current_month(), user_id, MESSAGE_POINTS))
    record_activity(guild_id, user_id)


def _update_voice_tick(guild_id: int, user_id: int, human_count: int):
    """Ajoute une minute vocale et applique le barème de points."""
    period = current_month()
    record_activity(guild_id, user_id)

    with connect_db() as conn:
        row = conn.execute(
            "SELECT voice_minutes, voice_points FROM weekly_scores WHERE guild_id=? AND week_key=? AND user_id=?",
            (guild_id, period, user_id),
        ).fetchone()

        old_minutes = row["voice_minutes"] if row else 0
        old_points = row["voice_points"] if row else 0
        new_minutes = old_minutes + 1
        new_points = old_points

        if human_count >= 2:
            if new_minutes % 5 == 0:
                new_points += 1
        else:
            if new_minutes % 10 == 0:
                new_points += 1

        conn.execute("""
        INSERT INTO weekly_scores(guild_id, week_key, user_id, voice_points, voice_minutes)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, week_key, user_id) DO UPDATE SET
          voice_points = ?,
          voice_minutes = ?
        """, (guild_id, period, user_id, new_points, new_minutes, new_points, new_minutes))


def record_activity(guild_id: int, user_id: int, when: Optional[datetime] = None):
    when = when or datetime.now(TZ)
    with connect_db() as conn:
        conn.execute("""
        INSERT INTO user_activity(guild_id, user_id, last_activity)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
          last_activity = excluded.last_activity
        """, (guild_id, user_id, when.isoformat()))


def get_score(guild_id: int, user_id: int, month: Optional[str] = None):
    with connect_db() as conn:
        return conn.execute("""
        SELECT * FROM weekly_scores
        WHERE guild_id=? AND week_key=? AND user_id=?
        """, (guild_id, month or current_month(), user_id)).fetchone()


def get_top(guild_id: int, month: Optional[str] = None, limit: int = 50):
    with connect_db() as conn:
        return conn.execute("""
        SELECT user_id, text_points, voice_points, text_messages, voice_minutes,
               (text_points + voice_points) AS total_points
        FROM weekly_scores
        WHERE guild_id=? AND week_key=?
        ORDER BY total_points DESC, voice_points DESC, text_points DESC
        LIMIT ?
        """, (guild_id, month or current_month(), limit)).fetchall()


def is_announced(guild_id: int, month: str) -> bool:
    with connect_db() as conn:
        return conn.execute(
            "SELECT 1 FROM announced_weeks WHERE guild_id=? AND week_key=?",
            (guild_id, month),
        ).fetchone() is not None


def mark_announced(guild_id: int, month: str):
    with connect_db() as conn:
        conn.execute("""
        INSERT OR IGNORE INTO announced_weeks(guild_id, week_key, announced_at)
        VALUES (?, ?, ?)
        """, (guild_id, month, datetime.now(TZ).isoformat()))


async def name_for(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    if member:
        return member.display_name

    try:
        member = await guild.fetch_member(user_id)
        return member.display_name
    except (discord.NotFound, discord.HTTPException):
        pass

    try:
        user = await bot.fetch_user(user_id)
        return getattr(user, "display_name", user.name)
    except discord.HTTPException:
        return f"Utilisateur {user_id}"


async def leaderboard_embed(guild: discord.Guild, month: str, title: str):
    rows = get_top(guild.id, month, limit=50)
    embed = discord.Embed(
        title=title,
        description=f"Mois de **{month_label(month)}**",
        colour=discord.Colour.blurple(),
    )

    if not rows:
        embed.add_field(name="Classement", value="Aucun point enregistré.", inline=False)
        return embed

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows, 1):
        name = discord.utils.escape_markdown(await name_for(guild, row["user_id"]))
        prefix = medals[i - 1] if i <= 3 else f"**{i}.**"
        lines.append(
            f"{prefix} **{name}** — **{row['total_points']} pts** "
            f"(🎙️ {row['voice_points']} · 💬 {row['text_points']})"
        )

    chunk = []
    chunk_len = 0
    part = 1
    for line in lines:
        extra = len(line) + (1 if chunk else 0)
        if chunk and chunk_len + extra > 1000:
            embed.add_field(
                name="Top 50" if part == 1 else f"Top 50 — suite {part}",
                value="\n".join(chunk),
                inline=False,
            )
            part += 1
            chunk = []
            chunk_len = 0
        chunk.append(line)
        chunk_len += extra

    if chunk:
        embed.add_field(
            name="Top 50" if part == 1 else f"Top 50 — suite {part}",
            value="\n".join(chunk),
            inline=False,
        )

    return embed


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if GUILD_ID and message.guild.id != GUILD_ID:
        return
    if message.channel.id in IGNORED_TEXT_CHANNEL_IDS:
        return

    content = (message.content or "").strip()
    if len(content) < MESSAGE_MIN_CHARS:
        return

    now = datetime.now(TZ)
    key = (message.guild.id, message.author.id)
    last = last_message_score_at.get(key)
    if last and (now - last).total_seconds() < MESSAGE_COOLDOWN_SECONDS:
        return

    last_message_score_at[key] = now
    add_text(message.guild.id, message.author.id)
    print(
        f"[TEXT] +{MESSAGE_POINTS} point(s) | guild={message.guild.id} "
        f"user={message.author.id} channel={message.channel.id}",
        flush=True,
    )


@tasks.loop(minutes=1)
async def voice_loop():
    for guild in bot.guilds:
        if GUILD_ID and guild.id != GUILD_ID:
            continue

        channels = list(guild.voice_channels) + list(guild.stage_channels)
        for channel in channels:
            if channel.id in IGNORED_VOICE_CHANNEL_IDS:
                continue
            if guild.afk_channel and channel.id == guild.afk_channel.id:
                continue

            humans = [m for m in channel.members if not m.bot]
            if not humans:
                continue

            for member in humans:
                _update_voice_tick(guild.id, member.id, len(humans))


@voice_loop.before_loop
async def before_voice_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=10)
async def monthly_loop():
    """Annonce une seule fois le classement du mois précédent."""
    if not RANKING_CHANNEL_ID:
        return

    month = previous_month()
    for guild in bot.guilds:
        if GUILD_ID and guild.id != GUILD_ID:
            continue
        if is_announced(guild.id, month):
            continue
        if not get_top(guild.id, month):
            continue

        channel = guild.get_channel(RANKING_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                embed=await leaderboard_embed(guild, month, "🏆 Classement du mois")
            )
            mark_announced(guild.id, month)


@monthly_loop.before_loop
async def before_monthly_loop():
    await bot.wait_until_ready()


@bot.tree.command(name="classement", description="Affiche le classement du mois en cours.")
@app_commands.guild_only()
async def classement(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await leaderboard_embed(
        interaction.guild, current_month(), "🏆 Classement du mois"
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="points", description="Affiche tes points du mois ou ceux d'un membre.")
@app_commands.describe(membre="Membre à consulter")
@app_commands.guild_only()
async def points(interaction: discord.Interaction, membre: Optional[discord.Member] = None):
    target = membre or interaction.user
    await interaction.response.defer()
    row = get_score(interaction.guild.id, target.id)

    if not row:
        await interaction.followup.send(
            f"**{target.display_name}** n'a encore aucun point ce mois-ci.",
            ephemeral=True,
        )
        return

    total = row["text_points"] + row["voice_points"]
    embed = discord.Embed(
        title=f"📊 Points de {target.display_name}", colour=discord.Colour.green()
    )
    embed.description = f"Mois de **{month_label(current_month())}**"
    embed.add_field(name="Total", value=f"**{total} pts**", inline=False)
    embed.add_field(
        name="🎙️ Vocal",
        value=f"{row['voice_points']} pts\n{row['voice_minutes']} min",
        inline=True,
    )
    embed.add_field(
        name="💬 Écrit",
        value=f"{row['text_points']} pts\n{row['text_messages']} messages comptabilisés",
        inline=True,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="top_vocal", description="Affiche le top vocal du mois.")
@app_commands.guild_only()
async def top_vocal(interaction: discord.Interaction):
    await interaction.response.defer()
    with connect_db() as conn:
        rows = conn.execute("""
        SELECT user_id, voice_points, voice_minutes FROM weekly_scores
        WHERE guild_id=? AND week_key=?
        ORDER BY voice_points DESC, voice_minutes DESC LIMIT 50
        """, (interaction.guild.id, current_month())).fetchall()

    lines = []
    for i, row in enumerate(rows, 1):
        name = discord.utils.escape_markdown(await name_for(interaction.guild, row["user_id"]))
        lines.append(f"**{i}. {name}** — {row['voice_points']} pts ({row['voice_minutes']} min)")

    await interaction.followup.send(
        embed=discord.Embed(
            title="🎙️ Top vocal du mois",
            description="\n".join(lines) or "Aucun point.",
            colour=discord.Colour.orange(),
        )
    )


@bot.tree.command(name="top_messages", description="Affiche le top messages du mois.")
@app_commands.guild_only()
async def top_messages(interaction: discord.Interaction):
    await interaction.response.defer()
    with connect_db() as conn:
        rows = conn.execute("""
        SELECT user_id, text_points, text_messages FROM weekly_scores
        WHERE guild_id=? AND week_key=?
        ORDER BY text_points DESC, text_messages DESC LIMIT 50
        """, (interaction.guild.id, current_month())).fetchall()

    lines = []
    for i, row in enumerate(rows, 1):
        name = discord.utils.escape_markdown(await name_for(interaction.guild, row["user_id"]))
        lines.append(f"**{i}. {name}** — {row['text_points']} pts ({row['text_messages']} messages)")

    await interaction.followup.send(
        embed=discord.Embed(
            title="💬 Top messages du mois",
            description="\n".join(lines) or "Aucun point.",
            colour=discord.Colour.teal(),
        )
    )


@bot.tree.command(name="admin_reset", description="Remet à zéro les scores du mois en cours.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def admin_reset(interaction: discord.Interaction):
    with connect_db() as conn:
        conn.execute(
            "DELETE FROM weekly_scores WHERE guild_id=? AND week_key=?",
            (interaction.guild.id, current_month()),
        )
    await interaction.response.send_message(
        "Scores du mois remis à zéro.", ephemeral=True
    )


@bot.tree.command(name="purge", description="Liste les membres inactifs depuis X jours (admin).")
@app_commands.describe(
    days="Nombre de jours d'inactivité minimum",
    limit="Nombre maximum d'entrées à afficher",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def purge(
    interaction: discord.Interaction,
    days: Optional[int] = 30,
    limit: Optional[int] = 50,
):
    days = max(1, days or 30)
    limit = min(50, max(1, limit or 50))
    await interaction.response.defer()

    cutoff = datetime.now(TZ) - timedelta(days=days)

    with connect_db() as conn:
        rows = conn.execute(
            "SELECT user_id, last_activity FROM user_activity WHERE guild_id=?",
            (interaction.guild.id,),
        ).fetchall()
    known = {r["user_id"]: r["last_activity"] for r in rows}

    try:
        members = [m async for m in interaction.guild.fetch_members(limit=None)]
    except Exception:
        members = list(interaction.guild.members)

    candidates = []
    for member in members:
        if member.bot:
            continue

        last_iso = known.get(member.id)
        if last_iso:
            try:
                last_dt = datetime.fromisoformat(last_iso)
            except ValueError:
                continue
            if last_dt <= cutoff:
                candidates.append((member, last_dt))
        elif member.joined_at:
            joined = member.joined_at.astimezone(TZ)
            if joined <= cutoff:
                candidates.append((member, None))

    if not candidates:
        await interaction.followup.send(
            f"Aucun membre inactif depuis {days} jours.", ephemeral=True
        )
        return

    def sort_key(item):
        _, last = item
        return (0 if last is None else 1, last or datetime.min.replace(tzinfo=TZ))

    candidates.sort(key=sort_key)
    lines = []
    for i, (member, last) in enumerate(candidates[:limit], 1):
        name = discord.utils.escape_markdown(member.display_name or member.name)
        last_str = (
            "Jamais enregistré"
            if last is None
            else last.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
        )
        lines.append(f"**{i}. {name}** — dernier actif : {last_str}")

    embed = discord.Embed(
        title=f"🧹 Inactifs (>={days}j)",
        description="\n".join(lines),
        colour=discord.Colour.dark_grey(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.event
async def setup_hook():
    init_db()
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()

    voice_loop.start()
    monthly_loop.start()


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN manquant dans le fichier .env")
    bot.run(TOKEN)
