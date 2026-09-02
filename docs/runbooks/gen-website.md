WHAT IT IS
One container, /opt/gen-website, serving a STATIC export. No Node process, no
database, no runtime. Caddy on the host fronts it at genmars.co.ke.

Deploys differently from the other two: CI builds an image, tags it by commit
SHA, pushes to GHCR, and deploy/deploy.sh pulls the pinned tag. This host never
builds it.

WHAT TO CHECK FIRST
  curl -s -o /dev/null -w '%{http_code}\\n' https://genmars.co.ke/
  cd /opt/gen-website && docker compose ps
  docker compose logs --tail 50
  systemctl status caddy

DEPLOY AND ROLLBACK
  ./deploy/deploy.sh <commit-sha>

Rollback IS a deploy — pass the previous SHA. Tags are immutable per commit, so
a rollback always names a known artefact rather than whatever `latest` happens
to be. That is the whole reason it works this way.

WHEN GHCR IS UNREACHABLE
Seen once: `context deadline exceeded` pulling the image. It was transient and
a re-run worked. If it persists the site keeps serving the running container —
a failed deploy is not an outage here.

WHAT BREAKING THIS COSTS
No prospect can find or order anything. No existing client is affected: their
work is in the portal, which is a different system on a different container.
