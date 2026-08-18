# Bot Discord — classement d'activité

## Ce qu'il fait
- Points vocaux :
  - Si au moins 2 personnes dans le même canal vocal/stage : **+1 point toutes les 5 minutes** (les minutes sont comptées).
  - Si une seule personne dans le canal : **+1 point toutes les 10 minutes** (les minutes sont comptées).
  - Le salon AFK est ignoré.
- Points texte : **1 point** par message admissible (par défaut), avec cooldown par utilisateur.
- Classement hebdomadaire stocké dans une base SQLite (`activity.db` par défaut).
- Publication automatique du classement de la semaine précédente si `RANKING_CHANNEL_ID` est renseigné.

## Commandes
- `/classement` — affiche le classement de la semaine en cours (top 50).
- `/points [membre]` — affiche les points d'un membre (ou les tiens si non précisé).
- `/top_vocal` — top vocal de la semaine.
- `/top_messages` — top messages de la semaine.
- `/admin_reset` — remet les scores de la semaine à zéro (administrateur requis).
 - `/top_vocal` — top vocal de la semaine (top 10).
 - `/top_messages` — top messages de la semaine (top 10).
 - `/admin_reset` — remet les scores de la semaine à zéro (administrateur requis).
 - `/purge [days=30] [limit=50]` — (admin) liste les membres inactifs depuis au moins `days` jours (max `limit` résultats).

## Installation (macOS)

Prérequis : Python 3.11+.

1. Installer Python si nécessaire (ex. Homebrew) :

```bash
brew install python@3.11
```

2. Dans le répertoire du projet, créer et activer un environnement virtuel :

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Installer les dépendances. Si vous n'utilisez pas directement la commande `pip`, utilisez le module `pip` via l'exécutable Python :

```bash
python -m pip install -r requirements.txt
```

4. Créez une application/générez un bot dans le Discord Developer Portal et, dans l'onglet **Bot**, activez **Message Content Intent** et donnez les permissions nécessaires.

5. Invitez le bot avec les scopes : `bot` et `applications.commands`.

Permissions minimales recommandées : View Channels, Send Messages, Embed Links, Read Message History, Connect, Speak.

## Configuration

Créez un fichier `.env` à la racine du projet (ou copiez `.env.example` si fourni) et renseignez au minimum :

```env
DISCORD_TOKEN=ton_token
GUILD_ID=                 # facultatif, pour restreindre le bot à un serveur
RANKING_CHANNEL_ID=       # facultatif, id du salon où poster automatiquement le classement
DB_PATH=activity.db       # facultatif, chemin vers la base SQLite
TIMEZONE=Europe/Paris     # facultatif, fuseau horaire utilisé pour calculs de semaines
```

Pour récupérer un ID Discord : activez le **Mode développeur** dans Discord, puis clic droit sur le serveur/salon/utilisateur > **Copier l'identifiant**.

## Lancement

Activez l'environnement virtuel puis lancez :

```bash
source .venv/bin/activate
python bot.py
```

Le bot créera la base SQLite (`activity.db` par défaut) et synchronisera les commandes slash au démarrage.

## Paramètres modifiables

Les paramètres texte sont lus depuis l'environnement :

- `MESSAGE_POINTS` (par défaut `1`)
- `MESSAGE_COOLDOWN_SECONDS` (par défaut `60`)
- `MESSAGE_MIN_CHARS` (par défaut `5`)

Les règles vocales sont actuellement codées dans `bot.py` :
- 1 point toutes les 5 minutes si >=2 personnes
- 1 point toutes les 10 minutes si seul

## Nouvelle fonctionnalité — suivi d'inactivité

- Le bot enregistre la `last_activity` des membres (message ou présence vocale) dans la base SQLite `user_activity`.
- Commande `/purge` (admin) : liste les membres inactifs depuis au moins `days` jours (par défaut 30), jusqu'à `limit` entrées (par défaut 50).
- Remarques :
  - Les timestamps d'activité commencent à être enregistrés après le redémarrage du bot contenant ces modifications ; l'historique antérieur n'est pas rétro‑enregistré automatiquement.
  - Pour des tests rapides, on peut modifier la table `user_activity` via `sqlite3` pour simuler des dates anciennes.
 - Le bot enregistre la `last_activity` des membres (message ou présence vocale) dans la base SQLite `user_activity`.
 - `last_activity` est mise à jour :
   - lors d'un message admissible (fonction `add_text()`),
   - à chaque tick vocal (fonction `_update_voice_tick()`).
 - La commande `/purge` :
   - exclut les bots,
   - inclut les membres sans activité enregistrée seulement si leur `joined_at` est antérieur au cutoff (pour ne pas lister les nouveaux arrivants),
   - liste en priorité les "jamais actifs" puis les membres triés par date d'activité (ascendant).
 - Remarques :
   - Les timestamps d'activité commencent à être enregistrés après le redémarrage du bot contenant ces modifications ; l'historique antérieur n'est pas rétro‑enregistré automatiquement.
   - Pour des tests rapides, on peut modifier la table `user_activity` via `sqlite3` pour simuler des dates anciennes.

## Exclure des salons

Pour ignorer des salons texte ou vocaux, définissez dans `.env` des listes d'IDs séparées par des virgules :

```env
IGNORED_TEXT_CHANNEL_IDS=123,456
IGNORED_VOICE_CHANNEL_IDS=789,101112
```

## Important

Le bot doit rester lancé continuellement pour comptabiliser l'activité. Un Raspberry Pi, NAS, mini‑PC, VPS ou conteneur Docker convient bien.

Documentation utile :
- Discord Gateway : https://discord.com/developers/docs/events/gateway
- discord.py : https://discordpy.readthedocs.io/
