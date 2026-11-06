# Security

RaceGreen runs model-generated commands in Git worktrees. A worktree is
version-control isolation, not a security sandbox: commands execute with the
current user's permissions and may access the network unless an external
sandbox policy prevents it.

Use RaceGreen only with trusted repositories by default. For untrusted code,
run it inside an appropriately restricted container or virtual machine with
separate credentials, a read-only host filesystem where possible, and
network access disabled.

RaceGreen does not persist provider credentials in events, SQLite, reports, or
artifacts. Run directories and databases may contain repository and model
output, so protect them with normal filesystem permissions and clean them with
`racegreen clean`.

On WSL2, keep the SQLite database, WAL files, and run artifacts on the Linux
filesystem (for example under `/home/<user>/.racegreen`), not under
`/mnt/c`. This is an operational reliability requirement for SQLite WAL, not
a security boundary.

## Local dashboard

`racegreen dashboard` is a read-only viewer for persisted events and
projections. It binds to loopback by default and refuses non-loopback bind
addresses. Requests must carry a loopback `Host`; if an `Origin` header is
present it must also be loopback and use the dashboard port. Write methods are
rejected. Responses set a restrictive Content Security Policy,
`X-Frame-Options: DENY`, `nosniff`, and related headers, and the browser UI
does not execute commands or load remote resources.

The dashboard serves candidate patch text only after resolving the recorded
path inside the configured artifacts root for that run. Path traversal,
symlink escapes, missing files, and patches larger than 2 MiB are rejected.
The endpoint is not an integration mechanism: apply or branch operations remain
explicit CLI actions with a fresh target-state check.

The dashboard is not a security boundary against other processes running as the
same user. A local process can read the database or artifacts directly, and the
dashboard exposes local database and artifacts-root paths through its health
endpoint. Run it only on a trusted machine, keep the artifacts and database
directories permission-protected, and do not expose the port through a proxy or
remote tunnel. Stop the dashboard when it is not needed.

The UI marks whether a winner has recorded full-suite confirmation evidence.
That marker is a presentation of persisted state, not independent
re-verification; consult the event trace for the authoritative verification
record.

Report suspected vulnerabilities privately through the repository's configured
security contact. Do not publish credentials, tokens, private repository data,
or exploit details in public issues.
