# Bot Discord — classement d'activité

## Ce qu'il fait
- Points vocaux :
  - Si au moins 2 personnes dans le même canal vocal/stage : **+1 point par minute**.
  - Si une seule personne dans le canal : **+1 point toutes les 5 minutes** (les minutes sont comptées).
  - Le salon AFK est ignoré.
- Points texte : **1 point** par message admissible (par défaut), avec cooldown par utilisateur.
- Classement hebdomadaire stocké dans une base SQLite (`activity.db` par défaut).
- Publication automatique du classement de la semaine précédente si `RANKING_CHANNEL_ID` est renseigné.

## Commandes
- `/classement` — affiche le classement de la semaine en cours.
- `/points [membre]` — affiche les points d'un membre (ou les tiens si non précisé).
- `/top_vocal` — top vocal de la semaine.
- `/top_messages` — top messages de la semaine.
- `/admin_reset` — remet les scores de la semaine à zéro (administrateur requis).

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
- 1 point par minute si >=2 personnes
- 1 point toutes les 5 minutes si seul

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
