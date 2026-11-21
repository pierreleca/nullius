"""A desktop's reaper: its container removed once the process that started it is gone, however it went.

    reaper.py ENGINE NAME

The desktop module starts one with each desktop, before the engine's run, in a session of its own (a run that kills
the driver's process group does not kill it), and holds its stdin. It writes the container's id once the engine has
given it, and ``removed`` once the container is gone or was never this start's. When stdin ends with neither (the
driver ended without removing it, or was killed), the container is removed by its id, or by NAME when no id came, and
the removal checked: a start cut short may still be made by the engine, and is looked for a minute. Standard library
only.
"""
import subprocess
import sys
import time


def engine(*args):
    try:
        return subprocess.run([sys.argv[1], *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    except OSError:                                  # no engine to ask: nothing this reaper can do
        sys.exit(1)


def main():
    target = sys.argv[2]
    for line in sys.stdin:
        target = line.strip() or target
    if target == "removed":
        return 0
    pending = target == sys.argv[2]                  # no id came: the engine may still be making it
    end = time.monotonic() + (60 if pending else 10)
    while True:
        engine("rm", "-f", target)
        r = engine("container", "inspect", "--format", "{{.Id}}", target)
        gone = r is not None and r.returncode != 0 and b"no such" in (r.stderr or b"").lower()
        if gone and not pending or time.monotonic() > end:
            return 0 if gone else 1
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
