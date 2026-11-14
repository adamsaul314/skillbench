"""Stand-in for the `claude` CLI in skillbench's own tests.

Records how it was invoked, replays a canned stream-json file to stdout byte-for-byte,
then optionally sleeps (to simulate a hung agent) and exits with a chosen code.
"""

import json
import os
import sys
import time

record_path = os.environ.get("FAKE_CLAUDE_RECORD")
if record_path:
    with open(record_path, "w", encoding="utf-8") as record_file:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, record_file)

with open(os.environ["FAKE_CLAUDE_STREAM"], "rb") as stream_file:
    sys.stdout.buffer.write(stream_file.read())
sys.stdout.buffer.flush()

time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP_S", "0")))
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
