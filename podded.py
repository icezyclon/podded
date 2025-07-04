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
# Also, all global config variables are of types: Path, list[str], str, bool, int - no other types are allowed

__version__ = "1.2"

import ast
import difflib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

_CDIR = Path(__file__).parent.resolve().as_posix()

# === START SELF-MODIFYING CONFIG ===
# NOTE: These main variables WILL be preserved on 'update' and cleared with 'clear'
LOCK = False
BUILD = ""
COMMAND = []
# === END SELF-MODIFYING CONFIG ===

# === START REGULAR CONFIG ===
# NOTE: These variables will NOT be preserved on 'update' and NOT modified by 'clear'
# WARN: On modifications with 'edit' only hardcoded values will be written back, use with care or edit manually!
TAG = Path(__file__).name.split(".", 1)[0].replace(" ", "_")
PODMAN = Path("podman")  # path to podman executable (is not resolved before executing)
BUILD_COMMAND = ["build", "--tag", TAG]  # + <temp-Containerfile-dir>
RUN_COMMAND = ["run", "-dit", "--name", TAG]  # + COMMAND
RUN_IT_COMMAND = ["run", "-it", "--name", TAG]  # + COMMAND
STOP_COMMAND = ["stop"]  # + [-t <time>] + TAG
ATTACH_COMMAND = ["attach"]  # + TAG
EXEC_COMMAND = ["exec", "-it"]  # + TAG + <cli-args> OR 'sh'
PODLET_COMMAND = ["podlet", "-i", "-a", "podman"]  # + RUN_COMMAND + PODLET_OPTIONS + COMMAND
PODLET_OPTIONS = []
# fallback to ghcr.io/containers/podlet container if podlet is not locally installed
# (this command also demonstrates some sensible options for security and container privileges)
PODLET_FALLBACK = [
    "run",
    "--rm",
    "--userns=keep-id",
    "--network=none",  # use 'host' for normal internet access (default)
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--read-only",
    "--tmpfs=/tmp",
    # (-v and -w for correct resolution of absolute paths (-a option))
    f"-v={_CDIR}:{_CDIR}:ro",
    f"-w={_CDIR}",
    "ghcr.io/containers/podlet:latest",
]  # + PODLET_COMMAND[1:] + RUN_COMMAND + PODLET_OPTIONS + COMMAND
QUADLET_DIR = Path.home() / ".config" / "containers" / "systemd"
QUADLET_TEMPLATE = """
[Service]
Restart=always
RestartSec=10

[Timer]
OnStartupSec=60
"""
UPDATE_REPO = "https://raw.githubusercontent.com/icezyclon/podded/main/podded.py"
# === END REGULAR CONFIG ===


class ArgumentError(Exception):
    pass


class Locked(Exception):
    pass


class NotProvided(Exception):
    pass


def _format_assignment(varname: str, value) -> str:
    MAX_LINE_WIDTH = 100
    if isinstance(value, str):
        value = value.strip()
        single_line = f"{varname} = {repr(value)}"
        if "\n" in value or len(single_line) > MAX_LINE_WIDTH:
            escaped = value.replace('"""', '\\"\\"\\"')
            return f'{varname} = """\n{escaped}\n"""'
        else:
            return f"""{varname} = {repr(value).replace("'", '"')}"""
    elif isinstance(value, list) and all(isinstance(x, str) for x in value):
        if len(f"{varname} = {repr(value)}") <= MAX_LINE_WIDTH:
            return f"""{varname} = [{", ".join(f'"{s}"' for s in value)}]"""
        else:
            lines = [f"{varname} = ["] + [f'    "{item}",' for item in value] + ["]"]
            return "\n".join(lines)
    elif isinstance(value, Path):
        return f'{varname} = Path("{value.as_posix()}")'
    elif isinstance(value, (int, bool)):
        return f"{varname} = {repr(value)}"
    else:
        raise TypeError(f"Unsupported formatting for value type: {type(value).__name__}")


def _modify_variable(src: str, varname: str, new_value, *, plevel: int = 1) -> str:
    # Scan only top-level (global) assignments
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id == varname:
                    target_node = node
                    break
        elif isinstance(node, ast.AnnAssign):
            # annotations are NOT preserved
            if isinstance(node.target, ast.Name):
                if node.target.id == varname:
                    target_node = node
                    break
    else:
        raise ValueError(f"Variable '{varname}' not found.")

    lines = src.splitlines()
    new_code = _format_assignment(varname, new_value)
    start_line = target_node.lineno - 1
    end_line = target_node.end_lineno
    new_code_lines = new_code.splitlines()
    new_lines = lines[:start_line] + new_code_lines + lines[end_line:]
    if plevel:
        print(f"'{varname}' modified")
    if plevel > 1:
        print("- ", end="")
        print("\n- ".join(lines[start_line:end_line]).rstrip())
        print("+ ", end="")
        print("\n+ ".join(new_code_lines).rstrip())
        print("")
    return "\n".join(new_lines) + "\n"


def _modify(src: str, *, plevel: int = 1, **kwargs) -> tuple[str, dict]:
    kwargs = {k.upper(): v for k, v in kwargs.items()}
    updates = {}
    for varname, newval in kwargs.items():
        if newval is None:
            if plevel:
                print(f"'{varname}' unchanged")
            continue
        newsrc = _modify_variable(src, varname, newval, plevel=plevel)
        if src != newsrc:
            updates[varname] = newval
            src = newsrc
    return src, updates


def _self_modify(**kwargs) -> None:
    kwargs = {k.upper(): v for k, v in kwargs.items()}
    content = Path(__file__).read_text()
    for varname, newval in dict(kwargs).items():
        if varname not in globals().keys():
            raise KeyError(f"Unknown variable: {varname}")
        curval = globals()[varname]
        if curval.strip() == newval.strip() if isinstance(curval, str) else curval == newval:
            kwargs[varname] = None
    newsrc, newvars = _modify(content, plevel=2, **kwargs)
    Path(__file__).write_text(newsrc)
    globals().update(newvars)


def _input_via_editor(content: str):
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile("w+t", delete=False) as f:
        f.write(content)
        f.close()
        try:
            subprocess.run(shlex.split(editor) + [f.name], check=True)
        except subprocess.CalledProcessError as e:
            raise IOError("{} exited with code {}.".format(editor, e.returncode))
        with open(f.name) as g:
            return g.read().strip()


def _var_to_user_str(var_key: str) -> str:
    value = globals()[var_key]
    if isinstance(value, Path):
        return value.as_posix()
    elif isinstance(value, list):
        return shlex.join(value)
    elif isinstance(value, bool):
        return str(value).lower()
    elif isinstance(value, str):
        return value.strip()
    else:
        raise TypeError(f"Unknown var type for user str output: {type(value)}")


def _user_str_to_var(var_key: str, value: str):
    _curval = globals()[var_key]
    value = value.strip()
    if isinstance(_curval, Path):
        return Path(value)
    elif isinstance(_curval, list):
        return shlex.split(value)
    elif isinstance(_curval, bool):
        return value.lower() == "true"
    elif isinstance(_curval, str):
        return value
    else:
        raise TypeError(f"Unknown var type for internal var: {type(_curval)}")


def _edit(var_key: str):
    return _user_str_to_var(var_key, _input_via_editor(_var_to_user_str(var_key)))


def build_cmd():
    if not BUILD.strip():
        raise NotProvided(
            "The Containerfile has not been provided"
            + ("" if LOCK else " yet, use 'build PATH' to save one")
        )

    tmpdir_obj = tempfile.TemporaryDirectory(prefix="podded_")
    tmpdir = Path(tmpdir_obj.name)
    (tmpdir / "Containerfile").write_text(BUILD)
    excmd = [(str(PODMAN))] + BUILD_COMMAND + [str(tmpdir)]
    print(shlex.join(excmd))
    subprocess.run(excmd, check=True)


def run_cmd(interactive: bool):
    if not COMMAND:  # and len(options) == 0:
        raise NotProvided(
            "The podman-command has not been provided"
            + ("" if LOCK else " yet, use 'run COMMAND' to save one")
        )
    excmd = [(str(PODMAN))] + (RUN_IT_COMMAND if interactive else RUN_COMMAND) + COMMAND
    print(shlex.join(excmd))
    subprocess.run(excmd, cwd=_CDIR, check=True)


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
            "  build               Build the last saved Contrainerfile",
            "  run[-it]            Run the last saved command [in interactive mode]",
            "  all[-it]            Build saved Containerfile and run saved command [in interactive mode]",
            f"  stop [TIME]         Stop the running container with name '{TAG}' [waiting at most TIME seconds]",
            f"  attach              Attach to running container with name '{TAG}'",
            f"  exec [*COMMAND]     Exec sh [or COMMAND if given] in the running container with name '{TAG}'",
            f"  new PATH/NAME       Create a new cleared copy of {Path(__file__).name} at PATH/NAME (copy + clear)",
            f"  copy PATH/NAME      Create a new unlocked copy of {Path(__file__).name} at PATH/NAME",
            "  print               Print all printable variables/keys to stdout",
            "  print cmd/build/VAR Print the saved run command(cmd), Containerfile(build) or global VAR to stdout",
            "  version [diff]      Print the version of this script to stdout [checking against the newest version online]",
            "",
            "Systemd commands:",
            f"  status              Show status of systemd service ({TAG})",
            "  enable              Generate, enable and start systemd service for user",
            "  disable             Disable and stop systemd service",
            "  quadlet             Generate the quadlet (.container file) and print it to stdout",
            "",
            "Self-Modifying Commands:" + (" (LOCKED)" if LOCK else ""),
            "  command COMMAND*    Save COMMAND in this script to run later",
            "  run[-it] COMMAND*   Save COMMAND in this script and run it [in interactive mode]",
            "  build-copy PATH     Save the *content* of Containerfile/Dockerfile at PATH",
            "  build PATH          Save the *content* of Containerfile/Dockerfile at PATH and build it",
            "  edit [cmd/build]    Edit the command or build file in EDITOR respectively, saving it back once done",
            # "  edit GLOBAL_VAR     Edit a global var, saving it back once done (will be removed by 'update')",
            "  lock [force]        Lock the build and run commands and prevent further self-modification",
            "  clear [build/run]   Clear saved Containerfile, run-command or both",
            "  update-from-repo    Download and update this script with the most up-to-date podded.py from Github",
            sep="\n",
        )
    elif cmd in ["all", "allit", "all-it"]:
        build_cmd()
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
            return build_cmd()
        if LOCK:
            raise Locked
        if len(options) != 1:
            raise ArgumentError(f"Expected 1 argument PATH, got {len(options)}")
        path = Path(options[0]).resolve()
        if not path.exists() or not path.is_file():
            raise ArgumentError(f"PATH '{path.as_posix()}' does not exist or is not a file")
        _self_modify(build=path.read_text())
        if cmd == "build":
            return build_cmd()
    elif cmd in ["stop"]:
        if len(options) == 1 and options[0].isdigit():
            options = ["--time", options[0]]
        elif len(options) != 0:
            raise ArgumentError(f"Expected at most 1 argument TIME:int, got {len(options)}")
        excmd = [(str(PODMAN))] + STOP_COMMAND + options + [TAG]
        print(shlex.join(excmd))
        subprocess.run(excmd)
    elif cmd in ["attach"]:
        excmd = [(str(PODMAN))] + ATTACH_COMMAND + [TAG]
        print('INFO: default detach sequence is: "ctrl-p,ctrl-q"')
        print(shlex.join(excmd))
        subprocess.run(excmd)
    elif cmd in ["exec"]:
        if len(options) == 0:
            options = ["sh"]
        excmd = [(str(PODMAN))] + EXEC_COMMAND + [TAG] + options
        print(shlex.join(excmd))
        subprocess.run(excmd)
    elif cmd in ["enable", "disable", "status", "quadlet"]:
        if len(options) != 0:
            raise ArgumentError(f"Expected 0 arguments, got {len(options)}")
        path = QUADLET_DIR / (TAG + ".container")
        if cmd in ["enable", "quadlet"]:
            finalcmd = shlex.join(
                excmd := (
                    PODLET_COMMAND
                    if shutil.which(PODLET_COMMAND[0]) is not None
                    else ([(str(PODMAN))] + PODLET_FALLBACK + PODLET_COMMAND[1:])
                    + RUN_COMMAND
                    + PODLET_OPTIONS
                    + COMMAND
                )
            )
            print(("# " if cmd == "quadlet" else "") + finalcmd)
            gen = subprocess.run(excmd, cwd=_CDIR, check=True, text=True, capture_output=True)

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
                f"Writing quadlet to '{path.as_posix()}'"
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
                    f"File {path.name} not currently at '{QUADLET_DIR.as_posix()}', nothing to disable"
                )
            print(shlex.join(excmd := ["systemctl", "--user", "stop", TAG]))
            subprocess.run(excmd, check=True)
            print(f"Deleted '{path.as_posix()}'")
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
        if not BUILD.strip():
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
            _self_modify(build="", command=[])
        elif len(options) == 1 and options[0] in ["build"]:
            _self_modify(build="")
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
            raise ArgumentError(f"PATH '{path.as_posix()}' does already exist")
        if not path.parent.is_dir():
            raise ArgumentError(f"'{path.parent.as_posix()}' does not exist or is not a directory")
        src = Path(__file__).read_text()
        if cmd == "copy":
            src, _ = _modify(src, plevel=0, lock=False)
            msg = f"Writing unlocked copy to '{path.as_posix()}', you may want to lock it or clear it before use"
        else:
            src, _ = _modify(src, plevel=0, lock=False, build="", command=[])
            msg = f"Writing cleared copy to '{path.as_posix()}', you may want to add Containerfile and commands"
        print(msg)
        path.write_text(src)
        path.chmod(0o755)
    elif cmd in ["update", "update-from-repo"]:
        if LOCK:
            print(
                f"WARNING: Updating would self-modify this script, which is currently LOCKED. Please open '{Path(__file__).as_posix()}' and set LOCK = False to proceed."
            )
            raise Locked
        if len(options) == 1:
            raise ArgumentError(f"Expected 0 arguments, got {len(options)}")
        print(
            "WARNING: While your BUILD and COMMAND will be kept, any other modifications will be lost!"
        )
        inp = input("Do you wish to proceed and update this file? [Y/n]: ").strip().lower()
        if inp in ["n", "no", "abort", "exit"]:
            raise ArgumentError("Aborted")
        with urllib.request.urlopen(UPDATE_REPO, timeout=10) as response:
            new = response.read().decode()
        oldv = __version__
        new, _ = _modify(
            new,
            plevel=0,
            lock=LOCK,
            build=BUILD.strip(),
            command=COMMAND,
        )
        _v = "__version__ ="
        newv = next(
            (
                line.strip().removeprefix(_v).strip().strip("\"'")
                for line in new.split("\n")
                if line.startswith(_v)
            ),
            "Cannot determine new version",
        )
        Path(__file__).write_text(new)
        print(f"Update successful: {oldv} -> {newv}")
    elif cmd in ["version", "--version", "-version"]:
        if len(options) == 0:
            print(f"podded - {__version__}")
        elif len(options) == 1 and options[0] in [
            "diff",
            "--diff",
            "showdiff",
        ]:
            with urllib.request.urlopen(UPDATE_REPO, timeout=10) as response:
                new = response.read().decode()
            _v = "__version__ ="
            newv = next(
                (
                    line.strip().removeprefix(_v).strip().strip("\"'")
                    for line in new.split("\n")
                    if line.startswith(_v)
                ),
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
                curr = Path(__file__).read_text()
                curr, _ = _modify(curr, plevel=0, lock=False, build="", command=[])
                new, _ = _modify(new, plevel=0, lock=False, build="", command=[])
                if curr == new:
                    print(f"podded - {__version__} - unchanged")
                else:
                    print(f"podded - {__version__} - MODIFIED")
                    if options[0] in ["showdiff"]:
                        diff = difflib.ndiff(curr.splitlines(), new.splitlines())
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
    elif cmd in ["print"]:
        gvars = filter(lambda s: s.isupper() and not s.startswith("_"), globals().keys())
        if len(options) == 0:
            return print(*gvars, sep="\n")
        elif len(options) == 1 and options[0].upper() in gvars:
            print(_var_to_user_str(options[0].upper()))
        else:
            raise ArgumentError(
                f"Expected at most 1 argument command/build/VAR, got {len(options)}"
            )
    elif cmd in ["edit"]:
        if LOCK:
            if len(options) == 1 and options[0].lower() == "lock":
                return _self_modify(lock=_edit("LOCK"))
            raise Locked
        gvars = filter(lambda s: s.isupper() and not s.startswith("_"), globals().keys())
        if len(options) == 0:
            return print(*gvars, sep="\n")
        elif len(options) == 1 and options[0].upper() in gvars:
            _self_modify(**{options[0].upper(): _edit(options[0].upper())})
        else:
            raise ArgumentError(
                f"Expected at most 1 argument command/build/VAR, got {len(options)}"
            )
    else:
        print("Use command 'help' for usage")
        raise ArgumentError(f"Unknown command: {cmd}")


def main(args: list[str]) -> int:
    try:
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
        print(f"SUBPROCESS exited with {e.returncode}", file=sys.stderr)
        if e.stdout:
            print(f"STDOUT:\n{e.stdout if e.stdout else ''}", file=sys.stderr)
        if e.stderr:
            print(f"STDERR:\n{e.stderr if e.stderr else ''}", file=sys.stderr)
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
    return 0


if __name__ == "__main__":
    exit(main(sys.argv[1:]))
