import shutil
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: quiet_stdio.py <command> [args...]", file=sys.stderr)
        return 2

    command = shutil.which(sys.argv[1]) or sys.argv[1]
    args = [command, *sys.argv[2:]]

    process = subprocess.Popen(
        args,
        stdin=sys.stdin.buffer,
        stdout=sys.stdout.buffer,
        stderr=subprocess.DEVNULL,
    )
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
