// Telegram webhook: Viktor messages the bot ("склади план на завтра"), and
// this generates a fresh training-plan prescription from his own
// Garmin-derived training log + recovery data (the same data/*.json files
// that feed the "Мій стан" dashboard, bundled straight from this repo) via
// one Anthropic API call, then replies in the same Telegram chat.
//
// Required Vercel environment variables (you set these yourself, in the
// Vercel dashboard or via `vercel env add` — never share these with Claude):
//   TELEGRAM_BOT_TOKEN        - from @BotFather
//   TELEGRAM_CHAT_ID          - your own Telegram chat id (see README)
//   TELEGRAM_WEBHOOK_SECRET   - any random string you pick; must match the
//                               secret_token you pass to setWebhook
//   ANTHROPIC_API_KEY         - from console.anthropic.com
//   ANTHROPIC_MODEL           - optional, defaults to a Claude Sonnet model id
//
// Data freshness note: workout-log.json/recovery.json are require()'d at
// build time, so this bot sees exactly what's currently deployed — the same
// freshness as the Мій стан dashboard itself (both follow the nightly
// Garmin-sync -> auto-redeploy cycle).

const workoutLog = require('../data/workout-log.json');
const recovery = require('../data/recovery.json');

const DEFAULT_MODEL = 'claude-sonnet-4-5';

const KNEE_PRECAUTIONS =
  'Поточні застереження щодо правого коліна: не присідати, не стрибати, не повертатись на правій нозі, не більше 10 кг зовнішнього навантаження у вправах на праву ногу. Дозволено: ходьба по рівній біговій доріжці, велотренажер, підйоми на носки з вагою, розгинання ноги з легкою вагою, робота з резинкою/обтяжувачем на щиколотку.';

const SYSTEM_PROMPT = `Ти — персональний тренер Віктора. Твоя єдина задача в цьому запиті — скласти конкретну програму на наступне тренування, українською мовою, за нижченаведеними правилами. Назви вправ залишай англійською (так заведено).

РОТАЦІЯ: цикл A1 → B1 → A2 → B2 → знову A1, і так по колу. A1/A2 — груди/жим + безпечна робота на ноги. B1/B2 — спина/тяга + задня поверхня тіла. Визнач, яке з чотирьох тренувань логічно йде наступним, дивлячись на назви вправ та дати останніх сесій у наданому логу (сесії не позначені явно як A1/B1/A2/B2 — сам віднеси кожну до відповідного типу за складом вправ).

ПРАВИЛА ПРОГРЕСІЇ:
- Верх тіла: коли обидва робочі підходи вправи досягають верхньої межі діапазону повторів два тренування поспіль, додай 2.5-5% ваги наступного разу.
- Робота біля коліна (підйом на носки, розгинання ноги, вправи з резинкою): спочатку прогресуй повтори, потім вагу; тримай вагу не більше 10 кг на правій нозі.
- Перевірка відновлення: якщо HRV (середнє за останні 7 днів) падає більш ніж приблизно на 10 мс нижче звичного рівня, або сон/стрес погіршені кілька днів поспіль — тримай тренування легким замість прогресії ваги, і поясни це у відповіді.
- Довша, ніж зазвичай, пауза між силовими тренуваннями (наприклад, пропущений день) сама по собі НЕ привід знижувати інтенсивність — дивись саме на дані відновлення, а не на кількість днів.

${KNEE_PRECAUTIONS}

ФОРМАТ ВІДПОВІДІ: звичайний текст для Telegram (без markdown-розмітки, без зірочок), стисло:
1. Заголовок: тип тренування (наприклад "B1 — день тяги") і дата.
2. Пронумерований список вправ: назва, підходи×повтори, конкретна вага/ціль з коротким обґрунтуванням у дужках, якщо є зміна відносно попереднього разу.
3. Один короткий абзац "Чому саме так": гап з останнього тренування, HRV/сон/стрес, чи це звичайний прогресивний тиждень чи легший.

Не додавай нічого, крім самої програми — без привітань, без запитань, без зайвих приміток.`;

function recentStrengthSessions(log, days = 45, limit = 12) {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  return log
    .filter((e) => new Date(e.date) >= cutoff)
    .sort((a, b) => new Date(b.date) - new Date(a.date))
    .slice(0, limit);
}

function recentRecoveryEntries(rec, days = 14) {
  return rec
    .slice()
    .sort((a, b) => new Date(b.d) - new Date(a.d))
    .slice(0, days);
}

async function generatePlan(userText) {
  const sessions = recentStrengthSessions(workoutLog);
  const recentRec = recentRecoveryEntries(recovery);
  const today = new Date().toISOString().slice(0, 10);

  const dataBlock = JSON.stringify(
    {
      today,
      recent_strength_sessions: sessions,
      recent_recovery: recentRec,
    },
    null,
    2
  );

  const userMessage = `Повідомлення від Віктора: "${userText}"\n\nДані (JSON):\n${dataBlock}`;

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': process.env.ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: process.env.ANTHROPIC_MODEL || DEFAULT_MODEL,
      max_tokens: 1200,
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: userMessage }],
    }),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Anthropic API error ${response.status}: ${errText}`);
  }

  const data = await response.json();
  return data.content?.[0]?.text?.trim() || 'Не вдалося згенерувати відповідь.';
}

async function sendTelegramMessage(chatId, text) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
  if (!response.ok) {
    console.error('Telegram sendMessage failed:', await response.text());
  }
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.status(200).send('Telegram training bot is running.');
    return;
  }

  const secretHeader = req.headers['x-telegram-bot-api-secret-token'];
  if (
    process.env.TELEGRAM_WEBHOOK_SECRET &&
    secretHeader !== process.env.TELEGRAM_WEBHOOK_SECRET
  ) {
    res.status(401).send('Unauthorized');
    return;
  }

  const update = req.body;
  const message = update && update.message;
  const chatId = message && message.chat && message.chat.id;
  const text = ((message && message.text) || '').trim();

  if (!chatId) {
    res.status(200).send('ok');
    return;
  }

  const allowedChatId = process.env.TELEGRAM_CHAT_ID;
  if (allowedChatId && String(chatId) !== String(allowedChatId)) {
    // TEMPORARY DIAGNOSTIC (Sep 17): log the mismatch so we can see the
    // actual incoming chat id vs. the configured one in Vercel logs.
    // Remove this console.log once chat_id matching is confirmed working.
    console.log(
      `chat_id mismatch: incoming=${chatId} (type ${typeof chatId}), configured TELEGRAM_CHAT_ID=${JSON.stringify(
        allowedChatId
      )}`
    );
    res.status(200).send('ok');
    return;
  }

  if (!text || text === '/start') {
    await sendTelegramMessage(
      chatId,
      'Напиши, наприклад: "склади план на завтра" — і я підготую тренування на основі твоїх останніх сесій Garmin і даних відновлення.'
    );
    res.status(200).send('ok');
    return;
  }

  try {
    const plan = await generatePlan(text);
    await sendTelegramMessage(chatId, plan);
  } catch (err) {
    console.error(err);
    await sendTelegramMessage(
      chatId,
      'Щось пішло не так під час генерації плану. Спробуй ще раз трохи пізніше.'
    );
  }

  res.status(200).send('ok');
};
