# Isolated analysis execution

Generated Python and analysis JSON are untrusted. Both execution pipelines now
require Python 3.11+ on the host and Docker running Linux containers. They never
execute generated Python in the host process and never fall back to doing so.

Build the reviewed local image once from the repository root:

```sh
docker build -t tablepilot-sandbox:local framework/execution/sandbox
```

Run the existing execution scripts with their existing arguments. The host only
needs the Python standard library for execution; scientific dependencies live in
the image. Do not add API keys or private files to the image or Dockerfile.

Each query runs in a new non-root container with no network, no host mounts, a
read-only root filesystem, dropped capabilities, no privilege escalation, bounded
temporary filesystems, memory, CPU, process count, file sizes, output, and a host
deadline. The host forcibly removes the named container at completion or timeout.
Docker must run on a trusted local daemon, preferably rootless. Container isolation
is a security boundary, not a guarantee against kernel/container-runtime defects;
keep Docker and the image patched. Do not run this against an untrusted remote daemon.

Only the selected CSV bytes and generated source enter the container, over stdin.
Host filenames are never interpolated into source. AST constant replacement maps
`table_name.csv` / `table_<number>.csv` to `/work/input.csv`, output CSV literals to
`/work/output.csv`, and `plt.show()` to `/work/output.png` via `plt.savefig()`.
The host accepts only bounded CSV/PNG artifact bytes under fixed query names.
It never extracts an archive or follows a container-supplied output pathname.
Existing output files are not overwritten. Other arbitrary generated paths may
fail inside the container rather than gaining host access.

Limits per query: 120 seconds by default (maximum 600), 512 MiB memory, one CPU,
64 processes, 16 MiB input CSV, 256 KiB source, 1 MiB captured text, and 8 MiB per
artifact. The 128 MiB work filesystem and 32 MiB temporary filesystem are ephemeral.
Warnings are captured with stdout. Text artifacts can contain untrusted content;
do not interpret them as commands, and treat generated CSV formulas as untrusted
when opening files in spreadsheet software.

Tests from the repository root:

```sh
python -m unittest discover -s tests -v
```

The unit tests mock Docker. Container tests require the image and an available
Linux Docker daemon, and must be explicitly enabled with `TABLEPILOT_TEST_DOCKER=1`.
These tests run adversarial code only in the restricted container, never locally.

This change addresses host code execution and filename-to-source injection in
the initial execution pipeline, and applies the same boundary to revision.
Submit the patched commit and test evidence for security revalidation before
closing associated findings.