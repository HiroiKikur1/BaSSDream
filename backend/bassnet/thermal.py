"""Keep our own GPU jobs cool (the user's machine; they asked not to run it hot).

guard() is called inside training / batch loops: every `every` seconds it reads the GPU temperature and power
(nvidia-smi); above MAX_TEMP it sleeps until the card has cooled to MAX_TEMP - HYST, and while the power draw is
above MAX_POWER it inserts short pauses so the average draw stays near the cap. It only slows our own process;
it does not touch any system setting. Limits can be changed with BASSNET_MAX_TEMP / BASSNET_MAX_POWER.
"""
import os
import subprocess
import time

MAX_TEMP = float(os.environ.get("BASSNET_MAX_TEMP", 83))     # degC (user: must not sit above 85 for 15 min)
MAX_POWER = float(os.environ.get("BASSNET_MAX_POWER", 180))  # W: the user's 180 W driver cap resets on reboot
HYST = 6.0
_last = [0.0]
_state = {"temp": 0.0, "power": 0.0}


def read():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,power.draw", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        t, p = [float(x) for x in out.split(",")]
        _state.update(temp=t, power=p)
    except Exception:
        pass
    return _state["temp"], _state["power"]


def guard(every=5.0):
    now = time.time()
    if now - _last[0] < every:
        return
    _last[0] = now
    t, p = read()
    if t >= MAX_TEMP:
        while t > MAX_TEMP - HYST:
            time.sleep(5)
            t, p = read()
    elif p > MAX_POWER:
        time.sleep(min(2.0, 0.5 * (p / MAX_POWER - 1.0) * every + 0.2))


def hook_module(module, every=3.0):
    """Check temperature / power before every forward pass of a torch module (long inference loops that we do
    not control, e.g. audio_separator's chunk loop)."""
    try:
        module.register_forward_pre_hook(lambda m, x: guard(every))
    except Exception:
        pass


def lower_priority():
    """Below-normal CPU priority and a few threads for this process, so the desktop and games stay responsive."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)       # BELOW_NORMAL_PRIORITY_CLASS
    except Exception:
        pass
    try:
        import torch
        torch.set_num_threads(4)
    except Exception:
        pass
