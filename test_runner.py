import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

PODDED_ORIG = Path("podded.py")
assert PODDED_ORIG.is_file()
RE_TEMP = re.compile(r"/tmp/[-_a-zA-Z0-9]+")


def run_command(cmd, cwd, env=None, stdin_data=None):
    assert (cwd / PODDED_ORIG.name).is_file(), f"Cannot find {PODDED_ORIG} in {cwd}"
    result = subprocess.run(
        [sys.executable] + cmd,
        capture_output=True,
        text=True,
        input=stdin_data,
        cwd=cwd,
        env=env,
    )
    return result


def read_file(path: Path, default=""):
    return path.read_text() if path.exists() else default


def write_file(path: Path, content: str):
    path.write_text(content)


def get_file_diffs(before_dir: Path, after_dir: Path) -> str:
    diffs = []
    before_files = {p.relative_to(before_dir): p for p in before_dir.rglob("*") if p.is_file()}
    after_files = {p.relative_to(after_dir): p for p in after_dir.rglob("*") if p.is_file()}
    all_keys = before_files.keys() | after_files.keys()
    for rel_path in sorted(all_keys):
        before_file = before_files.get(rel_path)
        after_file = after_files.get(rel_path)

        if before_file and not after_file:
            diffs.append(f"- {rel_path} (removed)")
        elif after_file and not before_file:
            diffs.append(f"+ {rel_path} (added)")
        else:
            before_text = read_file(before_file)
            after_text = read_file(after_file)
            if before_text != after_text:
                diff = difflib.unified_diff(
                    before_text.splitlines(),
                    after_text.splitlines(),
                    fromfile=str(rel_path),
                    tofile=str(rel_path),
                    lineterm="",
                    n=0,
                )
                lines = list(diff)
                normalized = ["@@ @@" if line.startswith("@@") else line for line in lines]
                diffs.extend(normalized)

    return "\n".join(diffs)


def prepare_podded(tmp_dir: Path):
    # newpodded = tmp_dir / PODDED_ORIG.name
    # shutil.copy(PODDED_ORIG, newpodded)
    content = PODDED_ORIG.read_text().splitlines()
    index = content.index("import subprocess")
    # TODO: but allow "podman ... podlet"
    newcode = [
        "_real_sp_run = subprocess.run",
        "def _proxy_sp_run(cmd, *args, **kwargs):",
        "    class Proxy:",
        "        stdout = '<proxy-stdout>'",
        "    if cmd[0] in ['podman', 'systemctl']:",
        "        print(f'% {cmd}')",
        "        return Proxy()",
        "    else:",
        "        return _real_sp_run(cmd, *args, **kwargs)",
        "subprocess.run = _proxy_sp_run",
    ]
    content = content[: index + 1] + newcode + content[index + 1 :]

    (tmp_dir / PODDED_ORIG.name).write_text("\n".join(content))


def prepare_tempdir(before_dir: Path) -> Path:
    # if before_dir.name == "before":
    #     tmp = before_dir.parent / "temp_before"
    # else:
    #     tmp = before_dir.parent / "temp_after"
    # tmp.mkdir()
    if before_dir.name == "before":
        tmp = Path("/tmp/podded_test_temp_before")
    else:
        tmp = Path("/tmp/podded_test_temp_after")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    # tmp = Path(tempfile.mkdtemp(prefix="podded_test_"))
    if before_dir.is_dir():
        shutil.copytree(before_dir, tmp, dirs_exist_ok=True)
    return tmp


def execute_cmds(cmds: list[str], cwd: Path, stdin: str, env_override: dict[str, str]):
    env = os.environ.copy()
    env.update(env_override)

    full_stdout = ""
    full_stderr = ""
    returncodes: list[int] = []

    for cmd in cmds:
        result = run_command(shlex.split(cmd), cwd=cwd, env=env, stdin_data=stdin)
        full_stdout += f"$ {cmd}\n{result.stdout}"
        full_stderr += f"$ {cmd}\n{result.stderr}"
        returncodes.append(result.returncode)

    full_stdout = re.sub(RE_TEMP, "/tmp/<tempdir>", full_stdout)
    full_stderr = re.sub(RE_TEMP, "/tmp/<tempdir>", full_stderr)

    return returncodes, full_stdout, full_stderr


def record(test_dir: Path):
    tmp_before = prepare_tempdir(test_dir / "before")
    prepare_podded(tmp_before)
    tmp_after = prepare_tempdir(tmp_before)

    cmds = [line for line in read_file(test_dir / "cmds.txt").splitlines() if line.strip()]
    assert cmds, "No commands were provided"
    stdin_data = read_file(test_dir / "stdin.txt")
    env_override = json.loads(read_file(test_dir / "env.json", "{}"))

    returncodes, full_stdout, full_stderr = execute_cmds(cmds, tmp_after, stdin_data, env_override)

    file_diff = get_file_diffs(tmp_before, tmp_after)

    write_file(test_dir / "stdout.txt", full_stdout)
    write_file(test_dir / "stderr.txt", full_stderr)
    write_file(test_dir / "returncodes.txt", "\n".join(map(str, returncodes)))
    write_file(test_dir / "file.diff", file_diff)
    assert all(0 <= code < 9 for code in returncodes), (
        f"Unexpected returncode: {returncodes} in {test_dir}"
    )


def playback(test_dir: Path) -> bool:
    tmp_before = prepare_tempdir(test_dir / "before")
    prepare_podded(tmp_before)
    tmp_after = prepare_tempdir(tmp_before)

    cmds = [line for line in read_file(test_dir / "cmds.txt").splitlines() if line.strip()]
    expected_stdout = read_file(test_dir / "stdout.txt")
    expected_stderr = read_file(test_dir / "stderr.txt")
    expected_returncodes = [
        int(line.strip()) for line in read_file(test_dir / "returncodes.txt").splitlines()
    ]
    expected_diff = read_file(test_dir / "file.diff")
    stdin_data = read_file(test_dir / "stdin.txt")
    env_override = json.loads(read_file(test_dir / "env.json", "{}"))

    returncodes, full_stdout, full_stderr = execute_cmds(cmds, tmp_after, stdin_data, env_override)

    actual_diff = get_file_diffs(tmp_before, tmp_after)

    failed = False

    if returncodes != expected_returncodes:
        print(f"[FAIL] return code mismatch: expected {expected_returncodes}, got {returncodes}")
        failed = True

    if full_stdout != expected_stdout:
        print("[FAIL] stdout mismatch:")
        print_diff(expected_stdout, full_stdout)
        failed = True

    if full_stderr != expected_stderr:
        print("[FAIL] stderr mismatch:")
        print_diff(expected_stderr, full_stderr)
        failed = True

    if actual_diff != expected_diff:
        print("[FAIL] file diff mismatch:")
        print_diff(expected_diff, actual_diff)
        failed = True

    if failed:
        print(f"[FAIL] {test_dir.name}")
    return failed


def print_diff(expected, actual):
    diff = difflib.unified_diff(
        expected.splitlines(),
        actual.splitlines(),
        fromfile="expected",
        tofile="actual",
        lineterm="",
    )
    print("\n".join(diff))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"record", "test"}:
        print("Usage: python test_runner.py [record|test]")
        return

    mode = sys.argv[1]
    tests_dir = Path("tests")
    test_dirs = sorted([p for p in tests_dir.iterdir() if p.is_dir()])

    failed = 0
    for test_dir in test_dirs:
        print(f"==> {mode.upper()} {test_dir.name}")
        if mode == "record":
            record(test_dir)
        else:
            failed += playback(test_dir)
    return failed


if __name__ == "__main__":
    exit(main())
