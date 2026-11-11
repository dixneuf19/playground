# vitry-auberge-bot

Telegram bot reminding the Vitry flatmates of their chores (bins, vegetables pickup, weekly cleaning).

Each reminder comes with a "✅ C'est fait" button. Clicking it, reacting to the message or replying to it marks the chore as done. Until then, the bot keeps nagging at the times defined for the chore.

The brown bin and glass also get a "🙅 Pas besoin" button, for the evenings when they are not worth taking out.

## Schedule

All chores live in [`vitry_auberge_bot/chores.py`](vitry_auberge_bot/chores.py). Edit the file and rebuild to change them.

| Chore | When | Reminders |
| --- | --- | --- |
| Poubelle marron | Mon, Fri 20:00 | 22:00, 00:00 |
| Poubelle jaune | Wed 20:00 | 22:00, 00:00 |
| Verre | Wed 20:00, even ISO weeks | 22:00, 00:00 |
| Légumes du Rungis à Bizet | Mon 17:00 | 19:00, 21:00 |
| Ménage hebdo | Sun 19:00 | none |

## La Fourche deliveries

Optional, enabled by `LAFOURCHE_EMAIL`. The bot reads the La Fourche orders of the flat's account at 9:00, 18:30 and on startup, and posts in the group:

- when a new order shows up, with its delivery slot,
- when the slot of a known order changes,
- at 19:00 the evening before the delivery, as a reminder.

Announced orders and sent reminders are kept in Postgres (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`), so a restart does not announce them twice. The table is created on startup.

The API client in [`vitry_auberge_bot/lafourche.py`](vitry_auberge_bot/lafourche.py) is still a stub: `LaFourcheClient.fetch_orders` raises until the API details are known.

## Setup

1. Create the bot with [@BotFather](https://t.me/BotFather) and get its token.
2. Add the bot to the group. Optionally make it **admin** so reacting to a reminder also counts: Telegram only sends reactions to bots that are admins.
3. Run it once with only the token, send `/chatid` in the group and set `CHAT_ID` to the answer (a negative number).

```bash
cp example.env .env  # fill BOT_TELEGRAM_TOKEN and CHAT_ID
uv sync
make dev
```

Tests of the Postgres store run only when `VITRY_TEST_DATABASE_URL` points to a database they may write to:

```bash
docker run -d --rm --name vab-test-db -e POSTGRES_PASSWORD=test -p 127.0.0.1:55433:5432 postgres:18-alpine
VITRY_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55433/postgres make test
```

## Commands

- `/prochains`: reminders of the next 7 days
- `/chatid`: id of the current chat
- `/help`

## Limitations

Chore state is kept in memory, so the bot must run as a single replica. After a restart in the middle of a reminder window, it resumes nagging with fresh messages, even if someone already acknowledged before the restart.
