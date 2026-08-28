# Backups land here

Written by `scripts/backup.sh` via the `./backups` bind mount on the `db`
service. Kept in the repository (empty) so the directory exists with the right
owner before Docker can create it as root.

**Nothing in here is in version control** — see `.gitignore`. Dumps contain
client personal data.

## Ownership

`pg_dump` runs inside the container, so dumps land on the host owned by the
container's postgres uid, not by you. The systemd timer runs as root and can
prune them; a manual `./scripts/backup.sh` as an ordinary user may not be able
to delete old files. Use `sudo` for manual runs, or leave pruning to the timer.

## These dumps are on the same disk as the database

One failed volume loses both. Off-box copy is not yet configured — see
`docs/PRE-LAUNCH.md`. Do not describe the portal as backed up until it is.
