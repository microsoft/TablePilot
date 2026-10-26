import base64
import contextlib
import io
import json
import os
from pathlib import Path
import resource
import stat
import sys
import traceback


MAX_OUTPUT = 1024 * 1024
MAX_ARTIFACT = 8 * 1024 * 1024


class LimitedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.bytes_written = 0

    def write(self, text):
        self.bytes_written += len(text.encode("utf-8"))
        if self.bytes_written > MAX_OUTPUT:
            raise RuntimeError("Generated output exceeded the limit.")
        return super().write(text)


def read_artifact(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as artifact:
        metadata = os.fstat(artifact.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ARTIFACT:
            raise RuntimeError("Invalid or oversized artifact.")
        content = artifact.read(MAX_ARTIFACT + 1)
        if len(content) > MAX_ARTIFACT:
            raise RuntimeError("Artifact exceeded the limit.")
        return base64.b64encode(content).decode("ascii")


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
    request = json.loads(sys.stdin.buffer.read(24 * 1024 * 1024 + 1))
    csv_bytes = base64.b64decode(request["csv"], validate=True)
    if len(csv_bytes) > 16 * 1024 * 1024:
        raise RuntimeError("CSV exceeded the input limit.")
    Path("/work/input.csv").write_bytes(csv_bytes)
    output = LimitedOutput()
    result = {"stdout": "", "artifacts": {}}
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            namespace = {"__name__": "__main__"}
            exec(compile(request["code"], "<generated-analysis>", "exec"), namespace, namespace)
        for kind in ("csv", "png"):
            artifact = read_artifact("/work/output." + kind)
            if artifact is not None:
                result["artifacts"][kind] = artifact
    except BaseException:
        result["error"] = traceback.format_exc(limit=8)[-4096:]
    result["stdout"] = output.getvalue()
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()