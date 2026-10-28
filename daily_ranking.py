"""Planification et déduplication des publications quotidiennes de Score."""

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def daily_publication_context(now: datetime, timezone: str = "Europe/Paris"):
    """Retourne (jour terminé, mois à classer) pendant l'heure suivant minuit.

    Une vérification chaque minute permet une publication au premier passage
    après 00:00, sans déduire le jour à partir de l'heure UTC du VPS.
    """
    local = now.astimezone(ZoneInfo(timezone))
    if local.hour != 0:
        return None
    finished_day = local.date() - timedelta(days=1)
    return finished_day.isoformat(), finished_day.strftime("%Y-%m")


def claim_daily_publication(connection: sqlite3.Connection, guild_id: int, day: str) -> bool:
    """Réserve atomiquement un jour avant l'appel réseau à Discord."""
    cursor = connection.execute(
        "INSERT OR IGNORE INTO announced_days(guild_id, day_key, announced_at) "
        "VALUES (?, ?, ?)",
        (guild_id, day, datetime.now(ZoneInfo("Europe/Paris")).isoformat()),
    )
    connection.commit()
    return cursor.rowcount == 1


def release_daily_publication(connection: sqlite3.Connection, guild_id: int, day: str):
    """Permet un nouvel essai si Discord a refusé l'envoi."""
    connection.execute(
        "DELETE FROM announced_days WHERE guild_id=? AND day_key=?", (guild_id, day)
    )
    connection.commit()
