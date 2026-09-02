# Runbooks

One file per registered system, plus `_incident-response.md`, which is appended
to every one of them.

## Why these are files and not only rows in the database

They are loaded into `System.runbook` so they show on the Systems screen in
Operations, which is where somebody will look first. That copy is a
convenience.

**These files are the source.** A runbook that exists only inside the portal is
useless at exactly the moment it is needed — the portal being down is the
SEV-1 the runbook was written for. As files they are in git, on GitHub, on the
founder's laptop, and on the server's filesystem whether or not Docker is
running.

Edit the file, then load it:

    python manage.py load_runbooks

## What belongs in one

What somebody who did not build the thing needs at 2am, in the order they need
it: the shape of the system, what to check first, how to restart it least
drastically, how to deploy and roll back, and what breaking it costs.

Not architecture essays. Not anything that will be wrong in a month. If a
command is in here it should be one you can paste.
