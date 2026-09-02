WHO IS CALLED, AND IN WHAT ORDER

The company is small enough that this is short, and small enough that writing
it down matters more, not less — there is no second person who happens to know.

  SEV-1  system down, or data at risk
         Edwin, immediately, by phone. Not email: the whole point of a SEV-1 is
         that the usual channels may be the thing that is broken.
         Raise the incident in Operations BEFORE fixing, so the start time is
         recorded rather than reconstructed afterwards.

  SEV-2  major function broken, workaround exists
         Edwin, same working day. Raise it in Operations.

  SEV-3  minor or cosmetic
         Raise it in Operations. No call.

WHAT TO DO FIRST, WHATEVER IT IS
  1. Write down the time it started. Not when you noticed — when it began. That
     gap is the only measure of whether monitoring works, and it is
     unrecoverable an hour later.
  2. Stop the bleeding before finding the cause. Mitigated is a real state and
     the board has it.
  3. Say something to affected clients before they ask. Charter 05 §III.

CLIENT-FACING OR NOT
If a client can see it, it is at least SEV-2 regardless of how small it looks
from here. If client DATA is at risk it is SEV-1 regardless of how few rows.

AFTER
Every SEV-1 gets a written post-mortem — what happened, why, what prevents
recurrence — and the system refuses to close one without it. That is not
bureaucracy; genmars.co.ke publishes the promise, and the refusal is what keeps
it true on a busy week.

Post-mortems are blameless. The model has nowhere to record who caused
something, deliberately: a record that assigns blame is a record people write
carefully rather than honestly.

WHERE THINGS ARE
  Operations → Settings → Incidents      raise, mitigate, close, post-mortem
  Operations → Settings → Systems        health, owners, what each outage costs
  Operations → Settings → Log            who did what, append-only
  /opt/gen-portal/docs/DEPLOYMENT.md     deploy, rollback, restore procedures
