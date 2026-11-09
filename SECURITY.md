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

## External agents

`--external-agent` runs a user-supplied command once in each agent worktree.
The command is launched with an argument array, without a shell, and receives a
small environment allowlist. Credential-looking variables are not inherited by
default; add explicit names with `RACEGREEN_EXTERNAL_PASSTHROUGH`. Output is
bounded, the process group is terminated on timeout or cancellation, and a
successful command is still only a candidate submission: it must pass the same
full-suite verification and arbitration as a built-in agent.

This adapter is not a sandbox. The external command runs with the current
user's permissions and may access the network. Use a container or VM for
untrusted agents.

Built-in agent tool commands use a separate minimal environment allowlist.
Provider credentials and unrelated host variables are not inherited by default.

## Docker verification sandbox

`--sandbox docker` mounts only the disposable verification checkout at
`/work`, defaults to `--network none`, uses a read-only root filesystem, and
provides a writable `/tmp`. The image is user-selected and must contain the test
runtime; the Docker daemon and image remain part of the trust boundary. The
sandbox applies to verification commands, not to built-in agent tool commands.
If network access is required for dependency installation, prefer a prepared
image instead of enabling the bridge network for untrusted tests.

## Configuration and credentials

Configuration files may contain provider URLs, model names, environment-variable
names, and limits. They must not contain credentials. RaceGreen recursively
rejects credential-looking keys in repository-local and explicit configuration
files, including nested objects and arrays.
The global configuration is read from the platform configuration directory or
`RACEGREEN_CONFIG`; `racegreen doctor --fix` creates a missing template without
overwriting an existing file. Set credentials only in the provider environment.

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

## Supply chain

CI runs the test matrix, coverage, secret scanning, and `pip-audit`; Dependabot
opens grouped updates for Python dependencies and GitHub Actions. Release
artifacts are built from a tagged commit and published to PyPI with trusted
publishing. Do not disable these checks to make a benchmark appear faster.

Report suspected vulnerabilities privately through the repository's configured
security contact. Do not publish credentials, tokens, private repository data,
or exploit details in public issues.
