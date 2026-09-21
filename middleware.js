export const config = {
  // Everything gets the login gate EXCEPT the Telegram bot webhook —
  // Telegram can't do an interactive login, so that route is protected
  // instead by its own secret-token check (see api/telegram-bot.js).
  matcher: ["/((?!api/telegram-bot).*)"],
};

const COOKIE_NAME = "dash_session";
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 180; // 180 days

function b64urlFromBytes(bytes) {
  let str = "";
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmac(data, secret) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(data));
  return b64urlFromBytes(new Uint8Array(sig));
}

async function makeSessionCookieValue(secret) {
  const expiresAt = Math.floor(Date.now() / 1000) + COOKIE_MAX_AGE_SECONDS;
  const payload = String(expiresAt);
  const sig = await hmac(payload, secret);
  return `${payload}.${sig}`;
}

async function isValidSessionCookie(value, secret) {
  if (!value) return false;
  const [payload, sig] = value.split(".");
  if (!payload || !sig) return false;
  const expected = await hmac(payload, secret);
  if (expected !== sig) return false;
  const expiresAt = Number(payload);
  if (!Number.isFinite(expiresAt)) return false;
  return Date.now() / 1000 < expiresAt;
}

function getCookie(request, name) {
  const header = request.headers.get("cookie");
  if (!header) return null;
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return rest.join("=");
  }
  return null;
}

function loginPage(errorText) {
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Мій стан — вхід</title>
<style>
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:#181B17;color:#EDEAE0;font-family:-apple-system,BlinkMacSystemFont,sans-serif;}
  form{background:#20241F;border:1px solid #343A31;border-radius:14px;padding:2rem 1.75rem;
    width:100%;max-width:320px;box-sizing:border-box;}
  h1{font-size:1.25rem;margin:0 0 1.25rem;}
  label{display:block;font-size:.85rem;color:#A9AF9F;margin:0 0 .3rem;}
  input{width:100%;box-sizing:border-box;padding:.6rem .7rem;margin-bottom:1rem;
    background:#262B23;border:1px solid #343A31;border-radius:8px;color:#EDEAE0;font-size:1rem;}
  button{width:100%;padding:.7rem;background:#7FB89E;color:#12201A;border:none;border-radius:8px;
    font-size:1rem;font-weight:600;cursor:pointer;}
  .err{color:#E0B15C;font-size:.85rem;margin:0 0 1rem;}
</style></head><body>
<form method="POST">
  <h1>Мій стан</h1>
  ${errorText ? `<p class="err">${errorText}</p>` : ""}
  <label for="u">Логін</label>
  <input id="u" name="username" autocomplete="username" required>
  <label for="p">Пароль</label>
  <input id="p" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Увійти</button>
</form>
</body></html>`;
}

export default async function middleware(request) {
  const secret = process.env.AUTH_SESSION_SECRET;
  const cookieValue = getCookie(request, COOKIE_NAME);

  if (secret && (await isValidSessionCookie(cookieValue, secret))) {
    return; // already logged in, let the request through
  }

  if (request.method === "POST") {
    const formData = await request.formData();
    const user = formData.get("username");
    const pass = formData.get("password");

    if (
      user === process.env.DASHBOARD_USER &&
      pass === process.env.DASHBOARD_PASS &&
      secret
    ) {
      const cookieVal = await makeSessionCookieValue(secret);
      return new Response(null, {
        status: 303,
        headers: {
          Location: request.url,
          "Set-Cookie": `${COOKIE_NAME}=${cookieVal}; Path=/; Max-Age=${COOKIE_MAX_AGE_SECONDS}; HttpOnly; Secure; SameSite=Lax`,
        },
      });
    }

    return new Response(loginPage("Невірний логін або пароль."), {
      status: 401,
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }

  return new Response(loginPage(), {
    status: 401,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}
