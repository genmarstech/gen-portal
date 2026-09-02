WHAT IT IS
One container, /opt/internals-tm, Next.js on 3020 behind Caddy at
ops.genmars.co.ke. It holds no data of its own: every screen is Django through
a /api/* rewrite to the api container.

It joins gen-portal's EDGE network only. It cannot reach Postgres or Redis, and
that is on purpose — it is the largest attack surface in the stack.

WHAT TO CHECK FIRST
  cd /opt/internals-tm
  docker compose ps
  docker compose logs --tail 100
  curl -s -o /dev/null -w '%{http_code}\\n' https://ops.genmars.co.ke/

If ops is up but every screen is empty or erroring, the fault is almost
certainly the api container, not this. Check /opt/gen-portal first.

RESTART
  docker compose restart

DEPLOY
  git pull --ff-only origin main
  docker compose build
  docker compose up -d

IF `api` DOES NOT RESOLVE
This container joins a network created by gen-portal's compose project. If
gen-portal was recreated with different networks, this loses the name and every
API call fails. Fix: bring gen-portal up first, then `docker compose up -d`
here. The network is genmars-portal_edge, named explicitly in compose.yaml.

NOTHING HERE IS URGENT ON ITS OWN
No client can see this. An outage stops the company working and stops no client
from being served — SEV-2, not SEV-1, unless it is hiding something worse.
