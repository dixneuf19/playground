# sfr-mms-bot

When an MMS doesn't reach an SFR phone, SFR sends an SMS instead:

> Info SFR : 06XXXXXXXX vous a envoyé un MMS consultable sur www.vosmms.com avec votre numéro 06YYYYYYYY et le mot de passe )AbC1@xY9. Merci de votre confiance.

Forward or paste that SMS to this Telegram bot. It logs in on vosmms.com with your number and the password, downloads the parts of the MMS and sends them back. vosmms.com keeps an MMS for 14 days.

The bot only answers the Telegram user IDs in `TELEGRAM_ALLOWED_USERS`, ignores everyone else without replying, and refuses to start when the list is empty. It never logs the password or the message contents.

## Development

```bash
cp example.env .env   # fill in the token and your Telegram user ID
uv sync
make dev              # run the bot
make format           # format + lint fix
make check-format
make typecheck
make test
```
