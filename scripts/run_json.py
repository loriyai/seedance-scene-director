"""Capture one already-authorized local CLI's JSON stdout without overwrites.

Usage: run_json.py --output NEW.json --log NEW.log.json [--input PATH] ...
                  -- EXECUTABLE [ARG ...]
No shell, retry, or authorization policy is supplied by this helper.
"""
import argparse
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys


def resolved(value):
    return Path(value).expanduser().resolve()


def identity(path):
    if os.name == "nt":
        value = str(path)
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        path = Path(value)
        # Win32 folds trailing dots/spaces. Treat an ADS input as protecting its
        # base file too, so a log cannot create that file before the CLI runs.
        path = Path(path.anchor, *(part.split(":", 1)[0].rstrip(" .")
                                  for part in path.parts[1:])).resolve()
    return os.path.normcase(str(path))


def validate_destination_name(value):
    if os.name != "nt":
        return
    path = Path(value).expanduser()
    if str(path).startswith("\\\\.\\"):
        raise ValueError(f"device paths are not output files: {path}")
    for part in path.parts:
        if part == path.anchor or part in {".", ".."}:
            continue
        if part.endswith((".", " ")) or ":" in part or PureWindowsPath(part).is_reserved():
            raise ValueError(f"ambiguous or reserved Windows output/log path: {path}")


def command_paths(command):
    """Protect direct argv paths, including --option=path; never parse code/JSON."""
    for argument in command:
        if argument.startswith("--") and "=" in argument:
            argument = argument.split("=", 1)[1]
        elif argument.startswith("-"):
            continue
        if not argument:
            continue
        try:
            yield resolved(argument)
        except (OSError, ValueError):
            # Non-path arguments (e.g. inline expressions) need no path identity.
            continue


def destinations(output_value, log_value, command, inputs):
    validate_destination_name(output_value)
    validate_destination_name(log_value)
    output, log = resolved(output_value), resolved(log_value)
    validate_destination_name(output)
    validate_destination_name(log)
    if identity(output) == identity(log):
        raise ValueError("output and log must be different paths")
    protected = {identity(path) for path in command_paths(command)}
    protected.update(identity(resolved(path)) for path in inputs)
    for label, original, path in (("output", output_value, output), ("log", log_value, log)):
        if identity(path) in protected:
            raise ValueError(f"{label} collides with a command/input path: {path}")
        # lexists also rejects a dangling symlink, before resolving its target.
        if os.path.lexists(Path(original).expanduser()) or os.path.lexists(path):
            raise ValueError(f"{label} already exists; choose a new path: {path}")
        if not path.parent.is_dir():
            raise ValueError(f"{label} parent directory does not exist: {path.parent}")
    return output, log


def invalid_constant(value):
    raise ValueError(f"not a JSON constant: {value}")


def write_log(stream, record):
    stream.seek(0)
    stream.truncate()
    json.dump(record, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
    stream.flush()


def write_new(path, payload):
    # Exclusive creation catches a path created after preflight, too.
    with path.open("xb") as stream:
        try:
            stream.write(payload)
        except BaseException:
            stream.close()
            path.unlink()
            raise


def run(output_value, log_value, command, inputs=()):
    output, log = destinations(output_value, log_value, command, inputs)
    record = {"status": "running", "argv": command, "output": str(output),
              "returncode": None, "stdout": "", "stderr": ""}
    # Reserve a new log before running anything; it is diagnostic, never a result.
    with log.open("x", encoding="utf-8", newline="\n") as stream:
        write_log(stream, record)
        output_created = False
        try:
            completed = subprocess.run(command, shell=False, capture_output=True,
                                       stdin=subprocess.DEVNULL)
            record.update(returncode=completed.returncode,
                          stdout=completed.stdout.decode("utf-8", errors="replace"),
                          stderr=completed.stderr.decode("utf-8", errors="replace"))
            if completed.stderr:
                sys.stderr.write(record["stderr"])
                if not record["stderr"].endswith("\n"):
                    sys.stderr.write("\n")
            # A failed command's stdout may itself be an error JSON or plain text.
            # Keep it in the log; do not replace the exit failure with a parse error.
            if completed.returncode != 0:
                raise ValueError(f"command exited with code {completed.returncode}")
            stdout = completed.stdout.decode("utf-8-sig")
            if not stdout.strip():
                raise ValueError("command stdout is empty; expected JSON")
            json.loads(stdout, parse_constant=invalid_constant)
            record["status"] = "validated"
            write_log(stream, record)
            write_new(output, completed.stdout)
            output_created = True
            record["status"] = "succeeded"
            write_log(stream, record)
        except (OSError, ValueError) as exc:
            if output_created:
                output.unlink()
            record.update(status="failed", error=str(exc))
            write_log(stream, record)
            print(f"run_json: {exc}; diagnostics: {log}", file=sys.stderr)
            return 1
    print(json.dumps({"status": "saved", "output": str(output), "log": str(log)},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new JSON result path; parent must exist")
    parser.add_argument("--log", required=True, help="new diagnostic JSON path; parent must exist")
    parser.add_argument("--input", action="append", default=[],
                        help="additional protected path, e.g. an input referenced inside JSON")
    if "--" not in argv:
        if argv == ["--help"] or argv == ["-h"]:
            parser.parse_args(argv)
        parser.error("provide a command argv after --")
    split = argv.index("--")
    args, command = parser.parse_args(argv[:split]), argv[split + 1:]
    if not command:
        parser.error("provide an executable after --")
    try:
        return run(args.output, args.log, command, args.input)
    except (OSError, ValueError) as exc:
        print(f"run_json: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
