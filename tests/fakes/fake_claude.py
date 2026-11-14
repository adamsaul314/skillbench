"""Stand-in for the `claude` CLI in skillbench's own tests.

Appends how it was invoked to a record file (one JSON line per invocation), replays a canned
stream-json file to stdout byte-for-byte, then optionally sleeps (to simulate a hung agent) and
exits with a chosen code. FAKE_CLAUDE_STREAM may list several files separated by os.pathsep:
invocation N replays file N, and the last file repeats for any further invocations.
"""

import json
import os
import sys
import time

record_path = os.environ.get("FAKE_CLAUDE_RECORD")
previous_invocations = 0
if record_path:
    if os.path.exists(record_path):
        with open(record_path, encoding="utf-8") as record_file:
            previous_invocations = sum(1 for _ in record_file)
    with open(record_path, "a", encoding="utf-8") as record_file:
        record_file.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")

stream_paths = os.environ["FAKE_CLAUDE_STREAM"].split(os.pathsep)
stream_path = stream_paths[min(previous_invocations, len(stream_paths) - 1)]
with open(stream_path, "rb") as stream_file:
    sys.stdout.buffer.write(stream_file.read())
sys.stdout.buffer.flush()

time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP_S", "0")))
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
