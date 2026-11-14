"""Stand-in for an agent CLI (`claude` or `copilot`) in skillbench's own tests.

Invoked as `fake_agent.py <agent-name> <args...>`; its settings come from FAKE_<AGENT>_* env vars.
Appends how it was invoked to a record file (one JSON line per invocation), replays a canned
event stream to stdout byte-for-byte, then optionally sleeps (to simulate a hung agent) and exits
with a chosen code. FAKE_<AGENT>_STREAM may list several files separated by os.pathsep:
invocation N replays file N, and the last file repeats for any further invocations.

`--version` answers without being recorded, so availability checks don't count as launches.
FAKE_<AGENT>_BROKEN makes every invocation fail, like a stub left behind by a bad install.
"""

import json
import os
import sys
import time

agent_name, *agent_args = sys.argv[1:]
env_prefix = f"FAKE_{agent_name.upper()}_"


def setting(name: str, default: str | None = None) -> str | None:
    return os.environ.get(env_prefix + name, default)


if setting("BROKEN"):
    print(f"{agent_name}: broken install", file=sys.stderr)
    sys.exit(1)
if agent_args == ["--version"]:
    print(f"fake {agent_name} 1.0.0")
    sys.exit(0)

record_path = setting("RECORD")
previous_invocations = 0
if record_path:
    if os.path.exists(record_path):
        with open(record_path, encoding="utf-8") as record_file:
            previous_invocations = sum(1 for _ in record_file)
    with open(record_path, "a", encoding="utf-8") as record_file:
        record_file.write(json.dumps({"argv": agent_args, "cwd": os.getcwd()}) + "\n")

stream_paths = setting("STREAM").split(os.pathsep)
stream_path = stream_paths[min(previous_invocations, len(stream_paths) - 1)]
with open(stream_path, "rb") as stream_file:
    sys.stdout.buffer.write(stream_file.read())
sys.stdout.buffer.flush()

time.sleep(float(setting("SLEEP_S", "0")))
sys.exit(int(setting("EXIT", "0")))
