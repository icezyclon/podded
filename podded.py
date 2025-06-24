#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 icezyclon

"""
podded - Saves your podman build instructions, run commands and systemd (quadlet) service files - all in a single script.

podded solves the inconvenience of having to manage run command, Containerfile and quadlet file in a single self-modifying script:
* Stores the Containerfile inside the script
* Saves and reuses your podman run command after the first use
* Automatically generates and manages quadlet files for systemd

For more details see: https://github.com/icezyclon/podded
"""

# NOTE:
# To allow for this in a single file, this script can self-modify within "# === SECTION START/END ===" marked sections.
# To prevent self-modification, set LOCK = True or use the 'lock' command.
# You may still/also change the marked sections by hand, but take care to NOT remove these comments.

__version__ = "0.17"

import difflib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# === LOCK START ===
LOCK: bool = False
# === LOCK END ===
# === CONTAINERFILE START ===
CONTAINERFILE: str = """
"""
# === CONTAINERFILE END ===
# === COMMAND START ===
COMMAND: list[str] = []
# === COMMAND END ===

# the following config will not be changed via commands and may be edited by hand:
TAG: str = Path(__file__).name.split(".", 1)[0].replace(" ", "_")
BUILD_COMMAND = ["podman", "build", "-t", TAG]  # + <temp-Containerfile-dir>
RUN_COMMAND = ["podman", "run", "-d", "--name", TAG]  # + COMMAND
RUN_IT_COMMAND = ["podman", "run", "-it", "--name", TAG]  # + COMMAND
STOP_COMMAND = ["podman", "stop", TAG]
PODLET_COMMAND = ["podlet", "-i", "-a"] + RUN_COMMAND + []  # + COMMAND
# fallback to ghcr.io/containers/podlet container if podlet is not locally installed
# (-v and -w for correct resolution of absolute paths (-a option))
PODLET_FALLBACK = [
    "podman",
    "run",
    "--rm",
    "-v",
    f"{(_cdir := str(Path(__file__).parent.resolve()))}:{_cdir}:ro",
    "-w",
    _cdir,
    "ghcr.io/containers/podlet",
]  # + PODLET_COMMAND
QUADLET_DIR = Path.home() / ".config" / "containers" / "systemd"
QUADLET_TEMPLATE: str = """
[Service]
Restart=always
RestartSec=10

[Timer]
OnStartupSec=60
"""
UPDATE_REPO: str = "https://raw.githubusercontent.com/icezyclon/podded/main/podded.py"


class ArgumentError(Exception):
    pass


class Locked(Exception):
    pass


class NotProvided(Exception):
    pass


def _modify(
    content: list[str],
    *,
    lock: bool | None = None,
    container: str | None = None,
    command: list[str] | None = None,
    print_diff: bool = True,
) -> dict[str, bool | str | list[str]]:
    MARKER_START = "# === {} START ==="
    MARKER_END = "# === {} END ==="
    variables: dict[str, bool | str | list[str]] = {}

    def find_replace(var: str, new_block: list[str]) -> None:
        starti = content.index(MARKER_START.format(var)) + 1
        endi = content.index(MARKER_END.format(var), starti)
        old_block = content[starti:endi]
        if old_block != new_block:
            content[starti:endi] = new_block
            if print_diff:
                print(f"{var} block changed:")
                print("- ", end="")
                print("\n- ".join(old_block).rstrip())
                print("+ ", end="")
                print("\n+ ".join(new_block).rstrip())
                print("")
        elif print_diff:
            print(f"{var} unchanged")

    if lock is not None:
        var = "LOCK"
        assert isinstance(lock, bool), f"{var} must be of type bool, was {type(lock)}"
        find_replace(var, [f"{var}: bool = {bool(lock)}"])
        variables[var] = lock

    if container is not None:
        var = "CONTAINERFILE"
        assert isinstance(container, str), f"{var} must be of type str, was {type(container)}"
        new_container = (
            [f'{var}: str = """']
            + list(map(lambda s: repr(s)[1:-1], container.splitlines()))
            + ['"""']
        )
        find_replace(var, new_container)
        variables[var] = "\n".join(new_container[1:-1])

    if command is not None:
        var = "COMMAND"
        assert isinstance(command, list) and all(isinstance(el, str) for el in command), (
            f"{var} must be of type list[str], was {type(command)} with all str: {all(isinstance(el, str) for el in command)}"
        )
        # [f"{var}: list[str] = {command}"]
        if command:
            new_command = [f"{var}: list[str] = ["] + list(f"    {el!r}," for el in command) + ["]"]
        else:
            new_command = [f"{var}: list[str] = []"]
        find_replace(var, new_command)
        variables[var] = command
    return variables


def _read_self() -> list[str]:
    return Path(__file__).read_text().splitlines()


def _read_newest() -> list[str]:
    with urllib.request.urlopen(UPDATE_REPO, timeout=10) as response:
        return response.read().decode().rstrip().replace("\r", "").split("\n")


def _write_content(path: str | Path, content: list[str]):
    Path(path).write_text("\n".join(content) + "\n")


def _self_modify(
    *,
    lock: bool | None = None,
    container: str | None = None,
    command: list[str] | None = None,
) -> None:
    content = _read_self()
    new_variables = _modify(content, lock=lock, container=container, command=command)
    globals().update(new_variables)
    _write_content(__file__, content)


def _input_via_editor(content: str):
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile("w+t", delete=False) as f:
        f.write(content)
        f.close()
        try:
            subprocess.run([editor, f.name], check=True)
        except subprocess.CalledProcessError as e:
            raise IOError("{} exited with code {}.".format(editor, e.returncode))
        with open(f.name) as g:
            return g.read()


def _edit(var_key: str):
    value = globals()[var_key]
    if var_key == "CONTAINERFILE":
        value = value.lstrip()
    if isinstance(value, Path):
        new = Path(_input_via_editor(str(value)))
    elif isinstance(value, list):
        new = shlex.split(_input_via_editor(shlex.join(value)))
    elif isinstance(value, bool):
        new = _input_via_editor(str(value).lower()).lower().strip() == "true"
    elif isinstance(value, str):
        new = _input_via_editor(value)
    else:
        raise TypeError(f"Unknown type for editing: {type(value)}")
    if new == value:
        print(f"{var_key} unchanged")
    return new if new != value else None


def build_cmd(should_return: bool):
    if not CONTAINERFILE.strip():
        raise NotProvided(
            "The Containerfile has not been provided"
            + ("" if LOCK else " yet, use 'build PATH' to save one")
        )

    tmpdir_obj = tempfile.TemporaryDirectory(prefix="podded_")
    tmpdir = Path(tmpdir_obj.name)
    (tmpdir / "Containerfile").write_text(CONTAINERFILE)
    excmd = BUILD_COMMAND + [str(tmpdir)]
    print(shlex.join(excmd))
    if should_return:
        subprocess.run(excmd, check=True)
    else:
        os.execvp(excmd[0], excmd)


def run_cmd(interactive: bool):
    if not COMMAND:  # and len(options) == 0:
        raise NotProvided(
            "The podman-command has not been provided"
            + ("" if LOCK else " yet, use 'run COMMAND' to save one")
        )
    excmd = (RUN_IT_COMMAND if interactive else RUN_COMMAND) + COMMAND
    print(shlex.join(excmd))
    os.execvp(excmd[0], excmd)


def main_(args: list[str]) -> None:
    if len(args) == 0:
        args = ["help"]

    cmd, options = args[0].lower(), args[1:]
    if cmd in ["help", "--help"]:
        print(
            __doc__.lstrip(),
            f"Version: {__version__}",
            "",
            "Commands:",
            "  help                See this help text",
            f"  build               Build the last saved Contrainerfile with '{' '.join(BUILD_COMMAND)}'",
            "  run[-it]            Run the last saved command [in interactive mode]",
            "  all[-it]            Build saved Containerfile and run saved command [in interactive mode]",
            f"  stop                Stop the running container with name '{TAG}'",
            f"  new PATH/NAME       Create a new cleared/reset copy of {Path(__file__).name} at PATH/NAME",
            f"  copy PATH/NAME      Create a new unlocked copy of {Path(__file__).name} at PATH/NAME",
            "  print (f/b/r)       Print the saved Container(f)ile, the (b)uild command or the (r)un command to stdout",
            "  version [diff]      Print the version of this script to stdout [checking against the newest version online]",
            "",
            "Systemd commands:",
            f"  status              Show status of systemd service ({TAG})",
            "  enable              Generate, enable and start systemd service for user",
            "  disable             Disable and stop systemd service",
            "  quadlet             Generate the quadlet (.container file) and print it to stdout",
            "",
            "Self-Modifying Commands:" + (" (LOCKED)" if LOCK else ""),
            f"  command COMMAND*    Save COMMAND of form '{' '.join(RUN_COMMAND)} COMMAND*'",
            f"  run[-it] COMMAND*   Save COMMAND of form '{' '.join(RUN_COMMAND)} COMMAND*' and run it [in interactive mode]",
            "  build-copy PATH     Save the *content* of Containerfile/Dockerfile at PATH",
            "  build PATH          Save the *content* of Containerfile/Dockerfile at PATH and build it",
            "  edit command/build  Edit the command or build file in EDITOR respectively, saving it back once done",
            # "  edit GLOBAL_VAR     Edit a global var, saving it back once done (will be removed by 'update')",
            "  lock [force]        Lock the build and run commands and prevent further self-modification",
            "  clear [build/run]   Clear saved Containerfile, run-command or both",
            "  update-from-repo    Download and update this script with the most up-to-date podded.py from Github",
            sep="\n",
        )
    elif cmd in ["all", "allit", "all-it"]:
        build_cmd(should_return=True)
        run_cmd(cmd != "all")
    elif cmd in ["run", "runit", "run-it"]:
        if len(options) != 0:
            if LOCK:
                raise Locked
            _self_modify(command=options)  # will modify global COMMAND as well
        run_cmd(cmd != "run")
    elif cmd in ["cmd", "command"]:
        if LOCK:
            raise Locked
        if len(options) == 0:
            raise ArgumentError(f"Expected at least 1 argument COMMAND*, got {len(options)}")
        _self_modify(command=options)
    elif cmd in ["build", "build-copy", "build-take", "build-keep"]:
        if len(options) == 0 and cmd == "build":
            return build_cmd(should_return=False)
        if LOCK:
            raise Locked
        if len(options) != 1:
            raise ArgumentError(f"Expected 1 argument PATH, got {len(options)}")
        path = Path(options[0]).resolve()
        if not path.exists() or not path.is_file():
            raise ArgumentError(f"PATH '{str(path)}' does not exist or is not a file")
        _self_modify(container=path.read_text())
        if cmd == "build":
            build_cmd(should_return=False)
    elif cmd in ["stop"]:
        print(shlex.join(STOP_COMMAND))
        os.execvp(STOP_COMMAND[0], STOP_COMMAND[0:])
    elif cmd in ["enable", "disable", "status", "quadlet"]:
        if len(options) != 0:
            raise ArgumentError(f"Expected 0 arguments, got {len(options)}")
        path = QUADLET_DIR / (TAG + ".container")
        if cmd in ["enable", "quadlet"]:
            finalcmd = shlex.join(
                excmd := (
                    (
                        PODLET_COMMAND
                        if shutil.which(PODLET_COMMAND[0]) is not None
                        else PODLET_FALLBACK + PODLET_COMMAND[1:]
                    )
                    + COMMAND
                )
            )
            print(("# " if cmd == "quadlet" else "") + finalcmd)
            gen = subprocess.run(excmd, check=True, text=True, capture_output=True)

            def _clean(s: str):
                return list(map(str.strip, s.strip().replace("\r", "").split("\n")))

            if QUADLET_TEMPLATE and QUADLET_TEMPLATE.lstrip():
                quadlet, template = (_clean(gen.stdout), _clean(QUADLET_TEMPLATE.lstrip()))
                tsec = [(i, v) for i, v in enumerate(template) if v.startswith("[")]
                tsec.append((len(template), None))
                if tsec and tsec[0][0] > 0:
                    tsec.insert(0, (0, None))
                for (i, v), (ie, ve) in zip(tsec, tsec[1:]):
                    if v in quadlet and (qi := quadlet.index(v)) > -1:
                        quadlet = quadlet[:qi] + template[i:ie] + quadlet[qi + 1 :]
                    else:
                        quadlet = template[i:ie] + quadlet
                quadlet = "\n".join(quadlet)
            else:
                quadlet = gen.stdout

            if cmd == "quadlet":
                return print(quadlet)

            if not QUADLET_DIR.exists():
                QUADLET_DIR.mkdir(parents=True)
            print(
                f"Writing quadlet to '{str(path)}'"
                + (" (overwriting existing quadlet)" if path.exists() else "")
            )
            path.write_text("# " + finalcmd + "\n" + quadlet)
            print(shlex.join(excmd := ["systemctl", "--user", "daemon-reload"]))
            subprocess.run(excmd, check=True)
            print(shlex.join(excmd := ["systemctl", "--user", "start", TAG]))
            subprocess.run(excmd, check=True)
            print(
                "INFO: Make sure to enable 'loginctl enable-linger' to start process on boot and not on session start"
            )
        elif cmd == "disable":
            if not path.exists():
                return print(
                    f"File {path.name} not currently at '{str(QUADLET_DIR)}', nothing to disable"
                )
            print(shlex.join(excmd := ["systemctl", "--user", "stop", TAG]))
            subprocess.run(excmd, check=True)
            print(f"Deleted '{str(path)}'")
            path.unlink()
            print(shlex.join(excmd := ["systemctl", "--user", "daemon-reload"]))
            subprocess.run(excmd, check=True)
        elif cmd == "status":
            print(shlex.join(excmd := ["systemctl", "--user", "status", TAG]))
            subprocess.run(excmd, check=True)
        else:
            assert False, f"Not implemented sub-command {cmd}"
    elif cmd in ["lock"]:
        if len(options) == 1 and options[0] in ["force", "--force", "-f"]:
            return _self_modify(lock=True)
        if len(options) != 0:
            raise ArgumentError(f"Expected at most 1 argument force, got {len(options)}")
        if LOCK:
            raise Locked
        if not COMMAND:
            raise NotProvided(
                "The podman-command has not been provided yet, use 'run COMMAND' to save one"
            )
        print(f"Are you sure you want to lock this script '{Path(__file__).name}'?")
        print(
            "Once locked the file cannot modify itself anymore and both saved Containerfile and podman-command are fixed"
        )
        if not CONTAINERFILE.strip():
            print(
                "WARNING: The Containerfile has not been provided yet (command 'build PATH')! Locking now will mean the 'build' and 'all' commands won't work!"
            )
        inp = input("Lock file? [y/N]: ").strip().lower()
        if inp not in ["y", "yes", "true"]:
            raise ArgumentError("Aborted")
        _self_modify(lock=True)
    elif cmd in ["clear"]:
        if LOCK:
            raise Locked
        if len(options) == 0:
            _self_modify(container="", command=[])
        elif len(options) == 1 and options[0] in ["build"]:
            _self_modify(container="")
        elif len(options) == 1 and options[0] in ["cmd", "command"]:
            _self_modify(command=[])
        elif len(options) == 1:
            raise ArgumentError(
                f"Unknown sub-command: {options[0]}, expected 'build', 'run' or none"
            )
        else:
            raise ArgumentError(f"Expected at most 1 argument [build/run], got {len(options)}")
    elif cmd in ["new", "copy"]:
        if len(options) != 1:
            raise ArgumentError(f"Expected 1 argument PATH, got {len(options)}")
        path = Path(options[0]).resolve()
        if path.exists():
            raise ArgumentError(f"PATH '{str(path)}' does already exist")
        content = _read_self()
        if cmd == "copy":
            _modify(content, lock=False, print_diff=False)
            msg = f"Writing unlocked copy to '{str(path)}', you may want to lock it or clear it before use"
        else:
            _modify(content, lock=False, container="", command=[], print_diff=False)
            msg = f"Writing cleared/reset copy to '{str(path)}', you may want to add Containerfile and commands"
        print(msg)
        _write_content(path, content)
        path.chmod(0o755)
    elif cmd in ["print"]:
        if len(options) != 1:
            raise ArgumentError(f"Expected 1 argument OPTION (f/b/r), got {len(options)}")
        elif options[0].lower() in [
            "f",
            "file",
            "containerfile",
            "dockerfile",
        ]:
            print(CONTAINERFILE.lstrip())
        elif options[0] in ["b", "build"]:
            print(shlex.join(BUILD_COMMAND) + " <temp-Containerfile-dir>")
        elif options[0] in ["r", "run", "cmd", "command"]:
            print(shlex.join(RUN_COMMAND + COMMAND))
        else:
            raise ArgumentError(f"Unknown OPTION {options[0]}, expected f/b/r")
    elif cmd in ["update", "update-from-repo"]:
        if LOCK:
            print(
                f"WARNING: Updating would self-modify this script, which is currently LOCKED. Please open '{str(Path(__file__))}' and set LOCK = False to proceed."
            )
            raise Locked
        if len(options) == 1:
            raise ArgumentError(f"Expected 0 arguments, got {len(options)}")
        print(
            "WARNING: While your CONTAINERFILE and COMMAND will be kept, any other modifications will be lost!"
        )
        inp = input("Do you wish to proceed and update this file? [Y/n]")
        if inp.lower() in ["n", "no", "abort", "exit"]:
            raise ArgumentError("Aborted")
        with urllib.request.urlopen(UPDATE_REPO, timeout=10) as response:
            new = response.read().decode().rstrip().replace("\r", "").split("\n")
        oldv = __version__
        _modify(
            new,
            lock=LOCK,
            container=CONTAINERFILE.lstrip(),
            command=COMMAND,
            print_diff=False,
        )
        _v = "__version__ ="
        newv = next(
            (line.removeprefix(_v) for line in new if line.startswith(_v)),
            "Cannot determine new version",
        )
        _write_content(__file__, new)
        print(f'Update successful: "{oldv}" -> {newv}')
    elif cmd in ["version", "--version", "-version"]:
        if len(options) == 0:
            print(f"podded - {__version__}")
        elif len(options) == 1 and options[0] in [
            "diff",
            "--diff",
            "showdiff",
        ]:
            new = _read_newest()
            _v = "__version__ ="
            newv = next(
                (line.removeprefix(_v).strip().strip("\"'") for line in new if line.startswith(_v)),
                None,
            )
            diff = []
            if newv is None:
                print(f"podded - {__version__} - ?")
                print("WARNING: Cannot determine newest version")
            elif __version__ != newv:
                print(f"podded - {__version__} - old version (newest is {newv})")
                print("INFO: Use command 'update-from-repo' to update to newest version")
            else:
                curr = _read_self()
                _modify(curr, lock=False, container="", command=[], print_diff=False)
                _modify(new, lock=False, container="", command=[], print_diff=False)
                if curr == new:
                    print(f"podded - {__version__} - unchanged")
                else:
                    print(f"podded - {__version__} - MODIFIED")
                    if options[0] in ["showdiff"]:
                        diff = difflib.ndiff(curr, new)
                        print(
                            "\n".join(
                                filter(
                                    lambda line: any(line.startswith(c) for c in "+?-"),
                                    diff,
                                )
                            ),
                            end="",
                        )
                    else:
                        print("INFO: Use 'showdiff' to show the changed lines")
        else:
            raise ArgumentError(f"Expected at most 1 argument diff, got {len(options)}")
    elif cmd in ["edit"]:
        if LOCK:
            if len(options) == 1 and options[0].lower() == "lock":
                return _self_modify(lock=_edit("LOCK"))
            raise Locked
        gvars = filter(
            lambda s: s.isupper() and s.lower() not in ["lock", "containerfile", "command"],
            globals().keys(),
        )
        if len(options) == 0:
            print(*("command", "build", *gvars), sep="\n")
        elif len(options) == 1 and options[0] in ["command", "cmd", "c"]:
            _self_modify(command=_edit("COMMAND"))
        elif len(options) == 1 and options[0] in ["build", "b", "file"]:
            _self_modify(container=_edit("CONTAINERFILE"))
        elif len(options) == 1 and options[0] in gvars:
            raise NotImplementedError
        else:
            raise ArgumentError(
                f"Expected at most 1 argument command/build/VAR, got {len(options)}"
            )
    else:
        print("Use command 'help' for usage")
        raise ArgumentError(f"Unknown command: {cmd}")


def main(args: list[str]) -> int:
    oldcwd = Path.cwd()
    try:
        os.chdir(Path(__file__).parent)
        main_(args)
    except ArgumentError as e:
        print(
            "INVALID ARGUMENT:",
            str(e.args[0]) if e.args else "Unknown",
            file=sys.stderr,
        )
        return 1
    except Locked:
        print("LOCKED: This script cannot modify itself", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        print(
            f"SUBPROCESS exited with {e.returncode}:",
            str(e.args[0]) if e.args else "Unknown",
            file=sys.stderr,
        )
        return 3
    except NotProvided as e:
        print(
            "MISSING:",
            str(e.args[0]) if e.args else "Unknown",
            file=sys.stderr,
        )
        return 4
    except Exception:
        import traceback

        traceback.print_exc()
        return 9
    finally:
        os.chdir(oldcwd)
    return 0


if __name__ == "__main__":
    exit(main(sys.argv[1:]))
