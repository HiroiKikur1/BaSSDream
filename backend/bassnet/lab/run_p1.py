"""P1 orchestration: features -> train v1 -> eval ablations -> EM realign -> train v2 -> eval -> legacy baseline."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import paths  # noqa: E402
import os
import re
import subprocess
import sys
import time

PY = sys.executable
BACK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = paths.CACHE
LOG = os.path.join(CACHE, "p1_orchestrator.log")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def log(msg):
    with open(LOG, "a", encoding="utf8") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def run(args, out_name):
    log("RUN " + " ".join(args))
    with open(os.path.join(CACHE, out_name), "a", encoding="utf8") as f:
        r = subprocess.run([PY] + args, cwd=BACK, env=ENV, stdout=f, stderr=subprocess.STDOUT)
    log(f"DONE rc={r.returncode} {out_name}")
    return r.returncode


def separation_running():
    r = subprocess.run(["powershell", "-Command", "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\").CommandLine"],
                       capture_output=True, text=True, errors="ignore")
    return "build_stems" in r.stdout


def main():
    log("start")
    while separation_running():
        run(["bassnet/dataset.py"], "p1_feats.log")
        time.sleep(240)
    run(["bassnet/dataset.py"], "p1_feats.log")
    run(["-m", "bassnet.train", "--epochs", "50", "--out", paths.cache(r"bassnet\bassnet.pt")], "p1_train_v1.log")
    run(["-m", "bassnet.eval_e2e", "--no-consensus", "--tag", "_v1_base"], "p1_eval.log")
    run(["-m", "bassnet.eval_e2e", "--tag", "_v1_cons"], "p1_eval.log")
    run(["-m", "bassnet.eval_e2e", "--lm", "--tag", "_v1_cons_lm"], "p1_eval.log")
    run(["-m", "bassnet.lab.realign"], "p1_realign.log")
    run(["-m", "bassnet.train", "--epochs", "50", "--out", paths.cache(r"bassnet\bassnet_v2.pt")], "p1_train_v2.log")
    run(["-m", "bassnet.eval_e2e", "--no-consensus", "--tag", "_v12_base"], "p1_eval.log")
    run(["-m", "bassnet.eval_e2e", "--lm", "--tag", "_v12_cons_lm"], "p1_eval.log")
    run(["bassnet/lab/bench_legacy.py", "--test"], "p1_legacy.log")
    log("all done")


if __name__ == "__main__":
    main()
