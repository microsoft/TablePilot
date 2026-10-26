import ast
import base64
import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "framework" / "execution"))
import sandbox
import code_exec_error
import code_exec_rivision


class ExecutionSecurityTests(unittest.TestCase):
    def test_paths_are_constant_ast_values(self):
        code = 'import pandas as pd\nfile = "table_name.csv"\ndf = pd.read_csv("table_12.csv")\ndf.to_csv("my_output.csv")\nplt.show()'
        prepared = sandbox.prepare_code(code)
        constants = [node.value for node in ast.walk(ast.parse(prepared)) if isinstance(node, ast.Constant)]
        self.assertIn("/work/input.csv", constants)
        self.assertIn("/work/output.csv", constants)
        self.assertIn("/work/output.png", constants)
        self.assertNotIn("table_name.csv", constants)

    def test_source_limits(self):
        with self.assertRaises(sandbox.SandboxError):
            sandbox.prepare_code(" " * (sandbox.MAX_CODE_BYTES + 1))
        with self.assertRaises(sandbox.SandboxError):
            sandbox.prepare_code("not valid python !")

    def test_no_docker_fails_closed(self):
        with patch.object(sandbox.shutil, "which", return_value=None), patch.object(sandbox.subprocess, "Popen") as process:
            with self.assertRaisesRegex(sandbox.SandboxError, "host execution is disabled"):
                sandbox.execute_code_with_timeout('print("never on host")', "unused.csv")
            process.assert_not_called()

    def test_response_does_not_control_host_paths(self):
        payload = {"stdout": "", "artifacts": {"../../escape": ""}}
        with self.assertRaises(sandbox.SandboxError):
            sandbox._decode_response(json.dumps(payload).encode())

    def test_decode_csv(self):
        payload = {"stdout": "ok", "artifacts": {"csv": base64.b64encode(b"total\n3\n").decode()}}
        result = sandbox._decode_response(json.dumps(payload).encode())
        self.assertEqual(result["artifacts"]["csv"], b"total\n3\n")

    def test_reject_invalid_response_and_png(self):
        for raw in (b"not JSON", b"[]", b'{"stdout": 1}', b'{"stdout":"", "artifacts":{"png":"eA=="}}'):
            with self.subTest(raw=raw), self.assertRaises(sandbox.SandboxError):
                sandbox._decode_response(raw)

    def test_timeout_removes_container_and_kills_client(self):
        class Process:
            killed = False
            def poll(self):
                return -9 if self.killed else None
            def kill(self):
                self.killed = True
            def wait(self, timeout=None):
                return -9

        process = Process()
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "input.csv"
            csv_path.write_text("value\n1\n", encoding="utf-8")
            with patch.object(sandbox.shutil, "which", return_value="docker"), patch.object(sandbox.subprocess, "Popen", return_value=process), patch.object(sandbox.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as remove, patch.object(sandbox.time, "monotonic", side_effect=[0, 2]):
                with self.assertRaises(TimeoutError):
                    sandbox.execute_code_with_timeout("while True: pass", csv_path, timeout=1)
                self.assertEqual(remove.call_args_list[0].args[0][1], "create")
                self.assertEqual(remove.call_args.args[0][:3], ["docker", "rm", "--force"])
                self.assertTrue(process.killed)

    def test_execution_scripts_have_no_host_execution(self):
        for module in (code_exec_error, code_exec_rivision):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            forbidden = [node.func.id for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                         and node.func.id in {"exec", "eval", "compile"}]
            self.assertEqual(forbidden, [])

    def test_both_pipelines_write_results_via_sandbox(self):
        import argparse
        for revision in (False, True):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for folder in ("input", "csv", "tables", "output"):
                    (root / folder).mkdir()
                table_name = "table_1'; injected = True; '"
                (root / "csv" / (table_name + ".csv")).write_text("value\n1\n", encoding="utf-8")
                record = {"code": 'raise RuntimeError("must not run on host")', "optimized_code": 'raise RuntimeError("must not run on host")'}
                if revision:
                    (root / "input" / table_name).mkdir()
                    (root / "input" / table_name / "query1_error.json").write_text(json.dumps(record), encoding="utf-8")
                else:
                    (root / "input" / (table_name + "_modeling.json")).write_text(json.dumps([record]), encoding="utf-8")
                args = argparse.Namespace(exec_dir=str(root / "input"), csv_dir=str(root / "csv"), table_txt_dir=str(root / "tables"), table_format_dir=str(root / "tables"), output_base_dir=str(root / "output"))
                with patch.object(sandbox, "execute_code_with_timeout", return_value={"stdout": "sandbox result", "artifacts": {}}) as execute:
                    if revision:
                        code_exec_rivision.main(args)
                    else:
                        arguments = ["script", "--exec_dir", args.exec_dir, "--csv_dir", args.csv_dir, "--table_txt_dir", args.table_txt_dir, "--output_base_dir", args.output_base_dir]
                        with patch.object(sys, "argv", arguments):
                            code_exec_error.main()
                    execute.assert_called_once()
                self.assertEqual((root / "output" / table_name / "query1.txt").read_text(encoding="utf-8"), "sandbox result")

    def test_filename_is_never_spliced_into_source(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "table_1'; injected = True; '.csv"
            csv_path.write_text("value\n1\n", encoding="utf-8")
            seen = {}

            class Process:
                returncode = 0
                def poll(self):
                    return 0
                def wait(self, timeout=None):
                    return 0

            def start(command, stdin, stdout, stderr):
                seen["start_command"] = command
                seen["request"] = json.loads(stdin.read())
                stdout.write(b'{"stdout":"1", "artifacts":{}}')
                return Process()

            with patch.object(sandbox.shutil, "which", return_value="docker"), patch.object(sandbox.subprocess, "Popen", side_effect=start), patch.object(sandbox.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as docker_run:
                result = sandbox.execute_code_with_timeout('print("table_name.csv")', csv_path)
                seen["command"] = docker_run.call_args_list[0].args[0]
            self.assertEqual(result["stdout"], "1")
            self.assertNotIn(str(csv_path), json.dumps(seen))
            self.assertIn("--network=none", seen["command"])
            self.assertIn("--read-only", seen["command"])
            self.assertFalse(any("mount" in argument or argument == "--volume" for argument in seen["command"]))
            self.assertEqual(seen["command"][1], "create")
            self.assertEqual(seen["start_command"][1:4], ["start", "--attach", "--interactive"])

    def test_image_or_daemon_failure_never_starts_code(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "input.csv"
            csv_path.write_text("value\n1\n", encoding="utf-8")
            for failure in (subprocess.CompletedProcess([], 1, b"", b"missing image"), subprocess.TimeoutExpired("docker create", 1)):
                with self.subTest(failure=failure), patch.object(sandbox.shutil, "which", return_value="docker"), patch.object(sandbox.subprocess, "Popen") as start, patch.object(sandbox.subprocess, "run") as create:
                    if isinstance(failure, Exception):
                        create.side_effect = failure
                    else:
                        create.return_value = failure
                    with self.assertRaises(sandbox.SandboxError):
                        sandbox.execute_code_with_timeout("raise AssertionError('must not run')", csv_path)
                    start.assert_not_called()

    def test_start_failure_removes_created_container(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "input.csv"
            csv_path.write_text("value\n1\n", encoding="utf-8")
            with patch.object(sandbox.shutil, "which", return_value="docker"), patch.object(sandbox.subprocess, "Popen", side_effect=OSError("failed")), patch.object(sandbox.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as commands:
                with self.assertRaises(sandbox.SandboxError):
                    sandbox.execute_code_with_timeout("print(1)", csv_path)
                self.assertEqual(commands.call_args_list[0].args[0][1], "create")
                self.assertEqual(commands.call_args_list[1].args[0][1:3], ["rm", "--force"])

    def test_cleanup_failure_is_not_reported_as_success(self):
        class Process:
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0

        with patch.object(sandbox.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"daemon unavailable")):
            with self.assertRaisesRegex(sandbox.SandboxError, "Unable to confirm sandbox cleanup"):
                sandbox._stop("docker", "tablepilot-test", Process())


@unittest.skipUnless(os.environ.get("TABLEPILOT_TEST_DOCKER") == "1", "Explicitly enable tests against the built Linux Docker image.")
class ContainerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.csv_path = Path(self.temporary.name) / "input'; malicious = True; '.csv"
        self.csv_path.write_text("value\n1\n2\n", encoding="utf-8")

    def test_csv_and_text(self):
        code = 'import pandas as pd\ntable = pd.read_csv("table_name.csv")\nprint(table["value"].sum())\ntable.to_csv("output.csv", index=False)'
        result = sandbox.execute_code_with_timeout(code, self.csv_path, timeout=30)
        self.assertEqual(result["stdout"].strip(), "3")
        self.assertIn(b"value\n1\n2\n", result["artifacts"]["csv"].replace(b"\r\n", b"\n"))

    def test_plot(self):
        result = sandbox.execute_code_with_timeout('import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4])\nplt.show()', self.csv_path, timeout=30)
        self.assertTrue(result["artifacts"]["png"].startswith(b"\x89PNG\r\n\x1a\n"))

    def test_host_credentials_network_and_read_only_root(self):
        code = '''import os
import socket
import subprocess
import sys
assert os.getuid() == 65532
assert "TABLEPILOT_HOST_SECRET" not in os.environ
try:
    open("/opt/tablepilot/host-write", "w")
except OSError:
    pass
else:
    raise AssertionError("Root filesystem is writable")
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError("Network access is enabled")
child = subprocess.check_output([sys.executable, "-c", "import os; print(os.getuid()); print(os.environ.get('TABLEPILOT_HOST_SECRET', 'absent'))"], text=True)
assert child.strip().splitlines() == ["65532", "absent"]
print("isolated")'''
        with patch.dict(os.environ, {"TABLEPILOT_HOST_SECRET": "host-only-sentinel"}):
            result = sandbox.execute_code_with_timeout(code, self.csv_path, timeout=30)
        self.assertEqual(result["stdout"].strip(), "isolated")

    def test_infinite_loop_is_removed(self):
        with self.assertRaises(TimeoutError):
            sandbox.execute_code_with_timeout('import subprocess\nimport sys\nsubprocess.Popen([sys.executable, "-c", "while True: pass"])\nwhile True: pass', self.csv_path, timeout=3)
        completed = subprocess.run([sandbox._docker(), "ps", "--all", "--filter", "name=tablepilot-", "--format", "{{.Names}}"], capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(completed.stdout.strip(), "")

    def test_output_limit(self):
        with self.assertRaises(sandbox.SandboxError):
            sandbox.execute_code_with_timeout('print("x" * (1024 * 1024 + 1))', self.csv_path, timeout=30)

    def test_generated_code_cannot_modify_host_state(self):
        host_file = Path(self.temporary.name) / "host-only-sentinel.txt"
        host_file.write_text("unchanged", encoding="utf-8")
        code = '''import builtins
import os
from pathlib import Path
builtins._tablepilot_security_marker = "changed"
os.environ["TABLEPILOT_HOST_STATE"] = "changed"
host_path = Path(HOST_PATH)
assert not host_path.exists()
try:
    host_path.write_text("changed")
except OSError:
    pass
assert not Path("/var/run/docker.sock").exists()
status = Path("/proc/self/status").read_text()
assert "NoNewPrivs:\t1" in status
capability = next(line.split()[1] for line in status.splitlines() if line.startswith("CapEff:"))
assert int(capability, 16) == 0
print("host unchanged")'''.replace("HOST_PATH", repr(str(host_file)))
        with patch.object(builtins, "_tablepilot_security_marker", "unchanged", create=True), patch.dict(os.environ, {"TABLEPILOT_HOST_STATE": "unchanged"}):
            result = sandbox.execute_code_with_timeout(code, self.csv_path, timeout=30)
            self.assertEqual(builtins._tablepilot_security_marker, "unchanged")
            self.assertEqual(os.environ["TABLEPILOT_HOST_STATE"], "unchanged")
        self.assertEqual(host_file.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual(result["stdout"].strip(), "host unchanged")

    def test_both_real_pipelines_with_injection_filename(self):
        root = Path(self.temporary.name)
        table_name = "table_1');print('PATH_INJECTION');#"
        code = 'import pandas as pd\ntable = pd.read_csv("table_name.csv")\nprint(table["value"].sum())'
        for revision in (False, True):
            with self.subTest(revision=revision):
                directory = root / ("revision" if revision else "initial")
                for folder in ("input", "csv", "tables", "output"):
                    (directory / folder).mkdir(parents=True)
                (directory / "csv" / (table_name + ".csv")).write_text("value\n1\n2\n", encoding="utf-8")
                arguments = [sys.executable]
                if revision:
                    (directory / "input" / table_name).mkdir()
                    (directory / "input" / table_name / "query1_error.json").write_text(json.dumps({"optimized_code": code}), encoding="utf-8")
                    arguments.extend([code_exec_rivision.__file__, "--table_format_dir", str(directory / "tables")])
                else:
                    (directory / "input" / (table_name + "_modeling.json")).write_text(json.dumps([{"code": code}]), encoding="utf-8")
                    arguments.extend([code_exec_error.__file__, "--table_txt_dir", str(directory / "tables")])
                arguments.extend(["--exec_dir", str(directory / "input"), "--csv_dir", str(directory / "csv"), "--output_base_dir", str(directory / "output")])
                subprocess.run(arguments, capture_output=True, text=True, timeout=45, check=True)
                result = (directory / "output" / table_name / "query1.txt").read_text(encoding="utf-8")
                self.assertEqual(result.strip(), "3")


if __name__ == "__main__":
    unittest.main()