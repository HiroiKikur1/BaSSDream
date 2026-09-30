"""Resident backend worker for the WPF client (src-native/Services/PyHost.cs).

Keeps one interpreter alive so numpy / librosa / torch are imported once instead of on every call. Each request
runs a backend script exactly as its command line would (``python <script> <args...>``) and returns what it
printed on stdout.

Protocol, one JSON object per line:
  request   {"id": 7, "script": "score_format.py", "args": ["open", "..."]}
  response  {"id": 7, "out": "<captured stdout>", "code": 0}
The first line written is {"ready": true}. Only the protocol goes to the real stdout: file descriptor 1 is pointed
at stderr so that child processes (ffmpeg) and native libraries can never write into the protocol stream.
"""
import contextlib
import io
import json
import os
import runpy
import sys
import traceback

BACKEND = os.path.dirname(os.path.abspath(__file__))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
ALLOWED = {"score_format.py", "eval_session.py", "performance_evaluator.py"}


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb", closefd=False), encoding="utf-8", line_buffering=True)

    def reply(obj):
        proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
        proto.flush()

    reply({"ready": True})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, script, args = req.get("id"), req.get("script", ""), [str(a) for a in req.get("args", [])]
        if script not in ALLOWED:
            reply({"id": rid, "out": "", "code": 2, "error": f"not allowed: {script}"})
            continue
        buf, code = io.StringIO(), 0
        argv = sys.argv
        sys.argv = [os.path.join(BACKEND, script)] + args
        try:
            with contextlib.redirect_stdout(buf):
                runpy.run_path(sys.argv[0], run_name="__main__")
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except BaseException:
            traceback.print_exc(file=sys.stderr)
            code = 1
        finally:
            sys.argv = argv
        reply({"id": rid, "out": buf.getvalue(), "code": code})


if __name__ == "__main__":
    main()
