import ast
import base64
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid


IMAGE = "tablepilot-sandbox:local"
MAX_CODE_BYTES = 256 * 1024
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


class SandboxError(RuntimeError):
    pass


class _Paths(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            if node.value == "table_name.csv" or re.fullmatch(r"table_\d+\.csv", node.value):
                return ast.copy_location(ast.Constant("/work/input.csv"), node)
            if "output" in node.value.lower() and node.value.lower().endswith(".csv"):
                return ast.copy_location(ast.Constant("/work/output.csv"), node)
        return node

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "show"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "plt"):
            node.func.attr = "savefig"
            node.args = [ast.Constant("/work/output.png")]
            node.keywords = []
        return node


def prepare_code(code):
    if not isinstance(code, str) or len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise SandboxError("Generated code exceeds the source size limit.")
    try:
        tree = _Paths().visit(ast.parse(code))
        return ast.unparse(ast.fix_missing_locations(tree))
    except (SyntaxError, ValueError, RecursionError) as error:
        raise SandboxError("Generated code is not supported Python source.") from error


def _docker():
    executable = shutil.which("docker")
    if executable is None:
        raise SandboxError("Docker is required; host execution is disabled. See execution/sandbox/README.md.")
    return executable


def _stop(executable, name, process):
    try:
        removal = subprocess.run([executable, "rm", "--force", name], capture_output=True, timeout=10, check=False)
        if removal.returncode and b"No such container" not in removal.stderr:
            raise SandboxError("Unable to confirm sandbox cleanup: " + name)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SandboxError("Unable to confirm sandbox cleanup: " + name) from error
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def execute_code_with_timeout(code, csv_path, timeout=120):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise SandboxError("Timeout must be between 0 and 600 seconds.")
    executable = _docker()
    prepared = prepare_code(code)
    with Path(csv_path).open("rb") as input_file:
        csv_bytes = input_file.read(MAX_INPUT_BYTES + 1)
    if len(csv_bytes) > MAX_INPUT_BYTES:
        raise SandboxError("CSV exceeds the sandbox input size limit.")
    payload = json.dumps({"code": prepared, "csv": base64.b64encode(csv_bytes).decode("ascii")}).encode("utf-8")
    name = "tablepilot-" + uuid.uuid4().hex
    command = [
        executable, "create", "--pull=never", "--name", name, "--interactive",
        "--network=none", "--read-only", "--user=65532:65532", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--memory=512m", "--memory-swap=512m",
        "--cpus=1", "--pids-limit=64", "--ulimit=nofile=256:256",
        "--ulimit=fsize=16777216:16777216", "--log-driver=none",
        "--tmpfs=/work:rw,noexec,nosuid,nodev,size=134217728,mode=1777",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=33554432,mode=1777", IMAGE,
    ]
    try:
        creation = subprocess.run(command, capture_output=True, timeout=min(timeout, 30), check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SandboxError("Sandbox creation failed; no generated code was started.") from error
    if creation.returncode:
        raise SandboxError("Sandbox creation failed. Check Docker and the local image; host execution is disabled.")
    with tempfile.TemporaryFile() as request, tempfile.TemporaryFile() as response, tempfile.TemporaryFile() as errors:
        request.write(payload)
        request.seek(0)
        try:
            process = subprocess.Popen([executable, "start", "--attach", "--interactive", name], stdin=request, stdout=response, stderr=errors)
        except OSError as error:
            removal = subprocess.run([executable, "rm", "--force", name], capture_output=True, timeout=10, check=False)
            if removal.returncode:
                raise SandboxError("Unable to confirm sandbox cleanup: " + name) from error
            raise SandboxError("Unable to start Docker; host execution is disabled.") from error
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if os.fstat(response.fileno()).st_size + os.fstat(errors.fileno()).st_size > MAX_RESPONSE_BYTES:
                    raise SandboxError("Sandbox output exceeded the size limit.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Sandbox execution exceeded its deadline.")
                try:
                    process.wait(timeout=min(remaining, 0.05))
                except subprocess.TimeoutExpired:
                    continue
            response.seek(0)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES or os.fstat(errors.fileno()).st_size > MAX_RESPONSE_BYTES:
                raise SandboxError("Sandbox output exceeded the size limit.")
            if process.returncode:
                raise SandboxError("Sandbox failed. Check Docker availability and build the sandbox image; no host fallback is allowed.")
            return _decode_response(raw)
        finally:
            _stop(executable, name, process)


def _decode_response(raw):
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get("stdout"), str):
            raise ValueError("Invalid sandbox response")
        if len(result["stdout"].encode("utf-8")) > 1024 * 1024:
            raise ValueError("Output too large")
        if result.get("error"):
            raise SandboxError(str(result["error"])[:4096])
        artifacts = result.get("artifacts", {})
        if not isinstance(artifacts, dict) or set(artifacts) - {"csv", "png"}:
            raise ValueError("Invalid artifact names")
        decoded = {}
        for kind, encoded in artifacts.items():
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_ARTIFACT_BYTES + 2) // 3):
                raise ValueError("Artifact too large")
            content = base64.b64decode(encoded, validate=True)
            if len(content) > MAX_ARTIFACT_BYTES:
                raise ValueError("Artifact too large")
            if kind == "png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid PNG")
            decoded[kind] = content
        return {"stdout": result["stdout"], "artifacts": decoded}
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise SandboxError("Invalid or oversized sandbox response.") from error


def run_analysis(code, csv_path, output_dir, output_name, timeout=120):
    if not re.fullmatch(r"query\d+", output_name):
        raise SandboxError("Invalid output name.")
    result = execute_code_with_timeout(code, csv_path, timeout)
    output_dir = Path(output_dir)
    for extension, content in result["artifacts"].items():
        destination = output_dir / (output_name + "." + extension)
        with destination.open("xb") as output_file:
            output_file.write(content)
    if not result["artifacts"] or result["stdout"]:
        with (output_dir / (output_name + ".txt")).open("x", encoding="utf-8") as output_file:
            output_file.write(result["stdout"])