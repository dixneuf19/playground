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

## Setup

1. Create the bot with [@BotFather](https://t.me/BotFather) and get its token.
2. Add the bot to the group. Optionally make it **admin** so reacting to a reminder also counts: Telegram only sends reactions to bots that are admins.
3. Run it once with only the token, send `/chatid` in the group and set `CHAT_ID` to the answer (a negative number).

```bash
cp example.env .env  # fill BOT_TELEGRAM_TOKEN and CHAT_ID
uv sync
make dev
```

## Commands

- `/prochains`: reminders of the next 7 days
- `/chatid`: id of the current chat
- `/help`

## Limitations

State is kept in memory, so the bot must run as a single replica. After a restart in the middle of a reminder window, it resumes nagging with fresh messages, even if someone already acknowledged before the restart.
