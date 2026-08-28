/**
 * Liveness probe for the container and for Caddy's upstream health check.
 *
 * Deliberately trivial: it answers for the Next process itself and nothing
 * else. It does NOT check the database or the API — a health endpoint that
 * fails when a dependency is down takes this container out of rotation for a
 * fault it cannot fix, turning one broken thing into two.
 */
export const dynamic = "force-dynamic";

export function GET() {
  return new Response("ok", {
    status: 200,
    headers: { "content-type": "text/plain", "cache-control": "no-store" },
  });
}
