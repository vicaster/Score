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

# Chargement des variables d'environnement depuis un fichier `.env`
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
# Chemin vers la base de données SQLite
DB_PATH = os.getenv("DB_PATH", "activity.db")
# Fuseau horaire utilisé pour calculer la semaine
TIMEZONE = os.getenv("TIMEZONE", "Europe/Paris")
# Optionnel: restreindre le bot à une seule guild (serveur)
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
# Channel où poster automatiquement le classement hebdomadaire
RANKING_CHANNEL_ID = int(os.getenv("RANKING_CHANNEL_ID", "0") or 0)

# ----- Paramètres de comptage pour le vocal -----
# La boucle vocale tourne toutes les minutes et applique les règles :
# - si >=2 personnes : 1 point toutes les 5 minutes
# - si seul dans le canal : 1 point toutes les 10 minutes

# ----- Paramètres pour les messages texte -----
MESSAGE_POINTS = max(0, int(os.getenv("MESSAGE_POINTS", "1")))
MESSAGE_COOLDOWN_SECONDS = max(0, int(os.getenv("MESSAGE_COOLDOWN_SECONDS", "60")))
MESSAGE_MIN_CHARS = max(0, int(os.getenv("MESSAGE_MIN_CHARS", "5")))

# Channels à ignorer (liste CSV dans .env)
IGNORED_TEXT_CHANNEL_IDS = {int(x) for x in os.getenv("IGNORED_TEXT_CHANNEL_IDS", "").split(",") if x.strip().isdigit()}
IGNORED_VOICE_CHANNEL_IDS = {int(x) for x in os.getenv("IGNORED_VOICE_CHANNEL_IDS", "").split(",") if x.strip().isdigit()}

# Objet ZoneInfo pour la timezone
TZ = ZoneInfo(TIMEZONE)

# ----- Intents et initialisation du bot -----
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.voice_states = True

# Instance du bot (on garde un préfixe pour compatibilité mais les commandes sont en slash)
bot = commands.Bot(command_prefix="!", intents=intents)
# Dictionnaire local pour mémoriser le cooldown des messages: (guild_id, user_id) -> datetime
last_message_score_at: dict[tuple[int, int], datetime] = {}


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialise la base SQLite et crée les tables nécessaires.

    - `weekly_scores` stocke les points texte/vocal par semaine et par utilisateur.
    - `announced_weeks` enregistre les semaines déjà annoncées automatiquement.
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


def current_week(dt=None):
    # Retourne une clé de semaine au format YYYY-Www basée sur ISO calendar
    dt = dt or datetime.now(TZ)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def previous_week():
    # Clé de la semaine précédente (utile pour annoncer le classement de la semaine passée)
    return current_week(datetime.now(TZ) - timedelta(days=7))


def add_text(guild_id, user_id):
    with connect_db() as conn:
        conn.execute("""
        INSERT INTO weekly_scores(guild_id, week_key, user_id, text_points, text_messages)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(guild_id, week_key, user_id) DO UPDATE SET
          text_points = text_points + excluded.text_points,
          text_messages = text_messages + 1
        """, (guild_id, current_week(), user_id, MESSAGE_POINTS))
    # Enregistrer l'activité utilisateur (message)
    record_activity(guild_id, user_id)


# NB: la fonction `add_voice` originale a été remplacée par `_update_voice_tick`
# qui gère le tick d'une minute et les règles de scoring (seul vs plusieurs).


def _update_voice_tick(guild_id: int, user_id: int, human_count: int):
    """Mise à jour atomique des minutes/points vocaux pour une personne sur un tick d'une minute.

    Règles :
    - Si `human_count` >= 2 : +1 minute et +1 point toutes les 5 minutes.
    - Si `human_count` == 1 : +1 minute et +1 point toutes les 10 minutes.
    """
    week = current_week()
    # Enregistrer l'activité utilisateur (présence vocale)
    record_activity(guild_id, user_id)
    with connect_db() as conn:
        row = conn.execute(
            "SELECT voice_minutes, voice_points FROM weekly_scores WHERE guild_id=? AND week_key=? AND user_id=?",
            (guild_id, week, user_id)
        ).fetchone()

        old_minutes = row["voice_minutes"] if row else 0
        old_points = row["voice_points"] if row else 0

        new_minutes = old_minutes + 1
        new_points = old_points

        if human_count >= 2:
            # Plusieurs personnes: 1 point toutes les 5 minutes
            if new_minutes % 5 == 0:
                new_points += 1
        else:
            # Seul dans le canal: 1 point toutes les 10 minutes
            if new_minutes % 10 == 0:
                new_points += 1

        # Upsert avec les totaux calculés
        conn.execute("""
        INSERT INTO weekly_scores(guild_id, week_key, user_id, voice_points, voice_minutes)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, week_key, user_id) DO UPDATE SET
          voice_points = ?,
          voice_minutes = ?
        """, (guild_id, week, user_id, new_points, new_minutes, new_points, new_minutes))


def record_activity(guild_id: int, user_id: int, when: datetime | None = None):
    """Enregistre le timestamp de la dernière activité d'un utilisateur dans une guild.

    `when` est en timezone `TZ` si fourni, sinon now(TZ).
    """
    when = when or datetime.now(TZ)
    with connect_db() as conn:
        conn.execute("""
        INSERT INTO user_activity(guild_id, user_id, last_activity)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
          last_activity = excluded.last_activity
        """, (guild_id, user_id, when.isoformat()))


def get_inactive_top(guild_id: int, days: Optional[int] = None, limit: int = 50):
    """Retourne les utilisateurs les plus inactifs (ordre ascendant par `last_activity`).

    Si `days` est renseigné, ne renvoie que ceux inactifs depuis au moins `days` jours.
    """
    with connect_db() as conn:
        if days is None:
            return conn.execute(
                "SELECT user_id, last_activity FROM user_activity WHERE guild_id=? ORDER BY last_activity ASC LIMIT ?",
                (guild_id, limit)
            ).fetchall()
        cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat()
        return conn.execute(
            "SELECT user_id, last_activity FROM user_activity WHERE guild_id=? AND last_activity<=? ORDER BY last_activity ASC LIMIT ?",
            (guild_id, cutoff, limit)
        ).fetchall()


def get_score(guild_id, user_id, week=None):
    with connect_db() as conn:
        return conn.execute("""
        SELECT * FROM weekly_scores
        WHERE guild_id=? AND week_key=? AND user_id=?
        """, (guild_id, week or current_week(), user_id)).fetchone()


def get_top(guild_id, week=None, limit=10):
    with connect_db() as conn:
        return conn.execute("""
        SELECT user_id, text_points, voice_points, text_messages, voice_minutes,
               (text_points + voice_points) AS total_points
        FROM weekly_scores
        WHERE guild_id=? AND week_key=?
        ORDER BY total_points DESC, voice_points DESC, text_points DESC
        LIMIT ?
        """, (guild_id, week or current_week(), limit)).fetchall()


def is_announced(guild_id, week):
    with connect_db() as conn:
        return conn.execute(
            "SELECT 1 FROM announced_weeks WHERE guild_id=? AND week_key=?",
            (guild_id, week)
        ).fetchone() is not None


def mark_announced(guild_id, week):
    with connect_db() as conn:
        conn.execute("""
        INSERT OR IGNORE INTO announced_weeks(guild_id, week_key, announced_at)
        VALUES (?, ?, ?)
        """, (guild_id, week, datetime.now(TZ).isoformat()))


async def name_for(guild, user_id):
    # Renvoie un nom lisible pour un user_id dans une guild (préférer le display_name si présent)
    # 1) Si le membre est en cache, utiliser son `display_name` (nickname sur la guild)
    member = guild.get_member(user_id)
    if member:
        return member.display_name

    # 2) Sinon, tenter de récupérer le membre via l'API (fetch_member)
    try:
        member = await guild.fetch_member(user_id)
        return member.display_name
    except discord.NotFound:
        pass
    except discord.HTTPException:
        pass

    # 3) En dernier recours, récupérer l'objet User global (pas de nickname de guild)
    try:
        user = await bot.fetch_user(user_id)
        # `User` n'a pas forcément `display_name` différent de `name`
        return getattr(user, "display_name", user.name)
    except discord.HTTPException:
        return f"Utilisateur {user_id}"


async def leaderboard_embed(guild, week, title):
    # Construit un embed Discord listant le top pour la semaine donnée
    rows = get_top(guild.id, week, limit=50)
    embed = discord.Embed(title=title, description=f"Semaine **{week}**", colour=discord.Colour.blurple())
    if not rows:
        embed.add_field(name="Classement", value="Aucun point enregistré.", inline=False)
        return embed

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows, 1):
        name = discord.utils.escape_markdown(await name_for(guild, row["user_id"]))
        prefix = medals[i-1] if i <= 3 else f"**{i}.**"
        lines.append(
            f"{prefix} **{name}** — **{row['total_points']} pts** "
            f"(🎙️ {row['voice_points']} · 💬 {row['text_points']})"
        )
    embed.add_field(name="Top 50", value="\n".join(lines), inline=False)
    return embed


@bot.event
async def on_message(message):
    # Gère les nouveaux messages texte:
    # - ignore les bots et les messages hors guild
    # - applique des filtres (guild ciblée, channels ignorés, longueur minimale)
    # - impose un cooldown par utilisateur pour éviter le spam de points
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


@tasks.loop(minutes=1)
async def voice_loop():
    # Boucle périodique qui parcourt les guilds et leurs canaux vocaux/stage
    # Pour chaque canal :
    # - ignore les channels configurés
    # - ignore le canal AFK
    # - compte les membres humains et si >= MIN_VOICE_PARTICIPANTS, ajoute des points
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
            human_count = len(humans)
            for member in humans:
                # Met à jour minutes/points selon le nombre de participants
                _update_voice_tick(guild.id, member.id, human_count)


@voice_loop.before_loop
async def before_voice_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=10)
async def weekly_loop():
    # Boucle toutes les 10 minutes qui vérifie si le classement de la semaine
    # précédente doit être annoncé dans `RANKING_CHANNEL_ID`.
    # Elle évite les doublons grâce à la table `announced_weeks`.
    if not RANKING_CHANNEL_ID:
        return

    week = previous_week()
    for guild in bot.guilds:
        if GUILD_ID and guild.id != GUILD_ID:
            continue
        if is_announced(guild.id, week):
            continue
        if not get_top(guild.id, week):
            continue

        channel = guild.get_channel(RANKING_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            await channel.send(embed=await leaderboard_embed(guild, week, "🏆 Classement de la semaine"))
            mark_announced(guild.id, week)


@weekly_loop.before_loop
async def before_weekly_loop():
    await bot.wait_until_ready()


# --- Commandes slash publiques (affichages de classement) ---
@bot.tree.command(name="classement", description="Affiche le classement de la semaine.")
@app_commands.guild_only()
async def classement(interaction: discord.Interaction):
    # Defer la réponse pour éviter l'erreur "Unknown interaction" si la construction
    # de l'embed prend du temps (fetch des membres, DB, etc.).
    await interaction.response.defer()
    embed = await leaderboard_embed(interaction.guild, current_week(), "🏆 Classement actuel")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="points", description="Affiche tes points ou ceux d'un membre.")
@app_commands.describe(membre="Membre à consulter")
@app_commands.guild_only()
async def points(interaction: discord.Interaction, membre: Optional[discord.Member] = None):
    # Affiche les points (texte + vocal) pour un membre (ou l'utilisateur appelant)
    target = membre or interaction.user
    await interaction.response.defer()
    row = get_score(interaction.guild.id, target.id)
    if not row:
        await interaction.followup.send(
            f"**{target.display_name}** n'a encore aucun point cette semaine.", ephemeral=True
        )
        return

    total = row["text_points"] + row["voice_points"]
    embed = discord.Embed(title=f"📊 Points de {target.display_name}", colour=discord.Colour.green())
    embed.add_field(name="Total", value=f"**{total} pts**", inline=False)
    embed.add_field(name="🎙️ Vocal", value=f"{row['voice_points']} pts\n{row['voice_minutes']} min", inline=True)
    embed.add_field(name="💬 Écrit", value=f"{row['text_points']} pts\n{row['text_messages']} messages", inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="top_vocal", description="Affiche le top vocal de la semaine.")
@app_commands.guild_only()
async def top_vocal(interaction: discord.Interaction):
    await interaction.response.defer()
    with connect_db() as conn:
        rows = conn.execute("""
        SELECT user_id, voice_points, voice_minutes FROM weekly_scores
        WHERE guild_id=? AND week_key=?
        ORDER BY voice_points DESC, voice_minutes DESC LIMIT 10
        """, (interaction.guild.id, current_week())).fetchall()

    lines = []
    for i, row in enumerate(rows, 1):
        name = discord.utils.escape_markdown(await name_for(interaction.guild, row["user_id"]))
        lines.append(f"**{i}. {name}** — {row['voice_points']} pts ({row['voice_minutes']} min)")
    await interaction.followup.send(
        embed=discord.Embed(title="🎙️ Top vocal", description="\n".join(lines) or "Aucun point.", colour=discord.Colour.orange())
    )


@bot.tree.command(name="top_messages", description="Affiche le top messages de la semaine.")
@app_commands.guild_only()
async def top_messages(interaction: discord.Interaction):
    await interaction.response.defer()
    with connect_db() as conn:
        rows = conn.execute("""
        SELECT user_id, text_points, text_messages FROM weekly_scores
        WHERE guild_id=? AND week_key=?
        ORDER BY text_points DESC, text_messages DESC LIMIT 10
        """, (interaction.guild.id, current_week())).fetchall()

    lines = []
    for i, row in enumerate(rows, 1):
        name = discord.utils.escape_markdown(await name_for(interaction.guild, row["user_id"]))
        lines.append(f"**{i}. {name}** — {row['text_points']} pts ({row['text_messages']} messages)")
    await interaction.followup.send(
        embed=discord.Embed(title="💬 Top messages", description="\n".join(lines) or "Aucun point.", colour=discord.Colour.teal())
    )


@bot.tree.command(name="admin_reset", description="Remet à zéro les scores de cette semaine.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def admin_reset(interaction: discord.Interaction):
    with connect_db() as conn:
        conn.execute(
            "DELETE FROM weekly_scores WHERE guild_id=? AND week_key=?",
            (interaction.guild.id, current_week())
        )
    await interaction.response.send_message("Scores de la semaine remis à zéro.", ephemeral=True)


@bot.tree.command(name="purge", description="Liste les membres inactifs depuis X jours (admin).")
@app_commands.describe(days="Nombre de jours d'inactivité minimum", limit="Nombre maximum d'entrées à afficher")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def purge(interaction: discord.Interaction, days: Optional[int] = 30, limit: Optional[int] = 50):
    await interaction.response.defer()
    rows = get_inactive_top(interaction.guild.id, days=days, limit=limit)
    if not rows:
        await interaction.followup.send(f"Aucun membre inactif depuis {days} jours.", ephemeral=True)
        return

    lines = []
    for i, row in enumerate(rows, 1):
        uid = row["user_id"] if isinstance(row, dict) or hasattr(row, 'keys') else row[0]
        last = row["last_activity"] if isinstance(row, dict) or hasattr(row, 'keys') else row[1]
        try:
            dt = datetime.fromisoformat(last)
            last_str = dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_str = str(last)
        name = discord.utils.escape_markdown(await name_for(interaction.guild, uid))
        lines.append(f"**{i}. {name}** — dernier actif : {last_str}")

    embed = discord.Embed(title=f"🧹 Inactifs (>={days}j)", description="\n".join(lines), colour=discord.Colour.dark_grey())
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.event
async def setup_hook():
    # Initialisation au démarrage du bot:
    # - crée/valide la base de données
    # - sync des commandes slash (globales ou pour une guild spécifique)
    # - démarre les tâches périodiques (voice_loop, weekly_loop)
    init_db()
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
    voice_loop.start()
    weekly_loop.start()


if __name__ == "__main__":
    # Vérifie la présence du token et lance le bot
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN manquant dans le fichier .env")
    bot.run(TOKEN)
