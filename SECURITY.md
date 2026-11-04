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

Report suspected vulnerabilities privately through the repository's configured
security contact. Do not publish credentials, tokens, private repository data,
or exploit details in public issues.
