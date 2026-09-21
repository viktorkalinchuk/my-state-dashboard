export const config = {
  matcher: "/:path*",
};

function decodeBase64(str) {
  if (typeof atob === "function") return atob(str);
  return Buffer.from(str, "base64").toString("utf-8");
}

export default function middleware(request) {
  const auth = request.headers.get("authorization");

  if (auth) {
    const [scheme, encoded] = auth.split(" ");
    if (scheme === "Basic" && encoded) {
      const [user, pass] = decodeBase64(encoded).split(":");
      if (user === process.env.DASHBOARD_USER && pass === process.env.DASHBOARD_PASS) {
        return;
      }
    }
  }

  return new Response("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Dashboard"' },
  });
}
