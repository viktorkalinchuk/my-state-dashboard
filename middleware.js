export const config = {
  matcher: "/:path*",
};

export default function middleware(request) {
  const auth = request.headers.get("authorization");

  if (auth) {
    const [scheme, encoded] = auth.split(" ");
    if (scheme === "Basic" && encoded) {
      const [user, pass] = atob(encoded).split(":");
      if (user === process.env.DASHBOARD_USER && pass === process.env.DASHBOARD_PASS) {
        return;
      }
    }
  }

  return new Response("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Мій стан"' },
  });
}
