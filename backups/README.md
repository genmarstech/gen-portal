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

## Permissions

Directory `700`, files `600`, set on every run by `scripts/backup.sh`.

They were `644` for the first weeks of the company — every client's contracts,
invoices, payment records and support threads readable by any process on a
shared host. Fixed, and fixed *in the script* rather than by hand, because a
permission corrected manually comes back wrong the next time the directory is
recreated.

The chmod happens inside the container. pg_dump runs there and the files land
owned by its uid, so a host-side chmod fails whenever the script is run by
anything but root.

## Encryption

Set `BACKUP_RECIPIENT` to a GPG key id and dumps are written as
`portal-<stamp>.dump.gpg`. Unset, they are written in clear and the script says
so, loudly, on every run.

**The key is asymmetric on purpose.** The server holds the PUBLIC half only, so
it can make a backup and cannot open one. Somebody who takes the host takes the
live database — which they already had — and not the archive of every earlier
state of it. A passphrase in `.env` beside the dumps would protect against a
stolen file and against nothing else.

**The private key is the whole thing.** Lose it and every encrypted dump is
permanently unreadable; there is no recovery, and no one to appeal to. It must
live somewhere the founder controls that is not this server, and it must be
backed up separately from the backups it opens.

`scripts/restore-test.sh` decrypts as part of the test, which is the only way
that failure is ever found: gpg will happily encrypt to a key whose private
half was lost months ago, and every dump since would be unreadable with nothing
saying so.

## These dumps are on the same disk as the database

One failed volume loses both. Off-box copy is not yet configured — see
`docs/PRE-LAUNCH.md`. Do not describe the portal as backed up until it is.
