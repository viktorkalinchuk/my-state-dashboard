# Telegram-бот для тренувань — налаштування

Бот замінює сторінку "Повернення у форму": пишеш йому "склади план на завтра",
він читає твій тренувальний лог і дані відновлення (ті самі `data/*.json`,
що живлять дашборд "Мій стан") і за 5-10 секунд відповідає готовою програмою.

Все нижче — кроки, які робиш ти сам, у своєму браузері/терміналі. Жоден
токен чи пароль сюди не передається мені (Claude) і не повинен передаватись.

## 1. Створи бота через @BotFather

1. Відкрий Telegram, знайди `@BotFather`.
2. Напиши `/newbot`, дай боту ім'я (наприклад "Мій тренер") і юзернейм, що
   закінчується на `bot` (наприклад `viktor_training_bot`).
3. BotFather видасть токен виду `123456789:AAF...` — це `TELEGRAM_BOT_TOKEN`.
   Збережи його, нікому не показуй.

## 2. Дізнайся свій chat_id

1. Напиши своєму новому боту будь-яке повідомлення (наприклад "привіт") —
   він поки що нічого не відповість, це нормально.
2. У браузері відкрий:
   `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates`
   (підстав свій токен замість `<TELEGRAM_BOT_TOKEN>`).
3. У відповіді знайди `"chat":{"id":ЦИФРИ, ...}` — ці цифри й є твій
   `TELEGRAM_CHAT_ID`.

## 3. Створи Anthropic API-ключ

1. Зайди на https://console.anthropic.com → API Keys → Create Key.
2. Збережи ключ (`sk-ant-...`) — це `ANTHROPIC_API_KEY`. Це окремий,
   платний за токенами акаунт (не той, яким ти спілкуєшся зі мною) — на
   ньому буде списуватись невелика сума за кожен згенерований план
   (орієнтовно центи за виклик).

## 4. Придумай секрет для вебхука

Будь-який випадковий рядок, наприклад:
```bash
openssl rand -hex 24
```
Це буде `TELEGRAM_WEBHOOK_SECRET` — захищає вебхук від чужих запитів (сам
Basic Auth сайту цей маршрут не покриває, бо Telegram не вміє логінитись).

## 5. Додай усі змінні середовища у Vercel

У Vercel Dashboard → твій проєкт → Settings → Environment Variables додай:

| Назва | Значення |
|---|---|
| `TELEGRAM_BOT_TOKEN` | з кроку 1 |
| `TELEGRAM_CHAT_ID` | з кроку 2 |
| `TELEGRAM_WEBHOOK_SECRET` | з кроку 4 |
| `ANTHROPIC_API_KEY` | з кроку 3 |
| `ANTHROPIC_MODEL` | (необов'язково) конкретний ID моделі Claude Sonnet, якщо хочеш зафіксувати версію — інакше подивись актуальний ID на https://docs.claude.com/en/docs/about-claude/models і встав сюди |

Або через CLI:
```bash
cd ~/my-state-dashboard
vercel env add TELEGRAM_BOT_TOKEN production
vercel env add TELEGRAM_CHAT_ID production
vercel env add TELEGRAM_WEBHOOK_SECRET production
vercel env add ANTHROPIC_API_KEY production
```

## 6. Закомить і задеплой код бота

```bash
cd ~/my-state-dashboard
# розпакуй telegram-bot.zip сюди (замінить/додасть api/telegram-bot.js,
# vercel.json, middleware.js)
git add api/telegram-bot.js vercel.json middleware.js TELEGRAM_BOT_SETUP.md
git commit -m "feat: Telegram bot for on-demand training plans"
git push
```
Дочекайся, поки задеплоїться (перевір у Vercel Dashboard → Deployments).

## 7. Зареєструй вебхук у Telegram

Одна команда (підстав свої значення):
```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://my-health-dashboard-xi.vercel.app/api/telegram-bot",
    "secret_token": "<TELEGRAM_WEBHOOK_SECRET>"
  }'
```
Очікувана відповідь: `{"ok":true,"result":true,"description":"Webhook was set"}`.

## 8. Перевір

Напиши боту в Telegram: `склади план на завтра`. За кілька секунд має
прийти готова програма тренування.

Якщо тиша — перевір логи функції у Vercel Dashboard → твій проєкт →
Functions → `api/telegram-bot` → Logs, там буде видно помилку (неправильний
токен, ключ, чи що завгодно).

## Що робити далі

Це повністю замінює ручне оновлення `training.html`/артефакту Claude —
можеш просто писати боту напряму, коли потрібен новий план. Дашборд "Мій
стан" на Vercel лишається як був, з автоматичним підтягуванням даних Garmin.
