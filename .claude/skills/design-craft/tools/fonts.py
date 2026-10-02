"""Fetch Google Fonts families as whole TTF files (usable by browsers and WPF alike).

usage: python fonts.py LIST.txt OUTDIR [--install]
       python fonts.py "Family Name:400;700" OUTDIR

LIST.txt: one family per line, optional weights after a colon, '#' comments:
    Shippori Mincho:500;700
    DotGothic16
--install also copies the files into the user font folder
(~/.local/share/fonts on Linux, then runs fc-cache; on Windows,
%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts), so mocks can name them directly.
Files that already exist are skipped. All Google Fonts are OFL/Apache; keep the licence in mind
before shipping one inside the app.
"""
import os, re, shutil, subprocess, sys, urllib.parse, urllib.request

API = "https://fonts.googleapis.com/css2?family={}"


def families(arg):
    if os.path.isfile(arg):
        for line in open(arg, encoding="utf-8"):
            line = line.split("#")[0].strip()
            if line: yield line
    else:
        yield arg


def fetch(spec, out):
    name, _, weights = spec.partition(":")
    q = urllib.parse.quote(name.strip()).replace("%20", "+")
    if weights: q += ":wght@" + weights.strip()
    # A plain user agent makes the API answer with full .ttf files, not woff2 subsets.
    css = urllib.request.urlopen(urllib.request.Request(API.format(q), headers={"User-Agent": "fonts.py"}), timeout=30).read().decode()
    got = []
    for block in re.findall(r"@font-face\s*{[^}]*}", css):
        w = re.search(r"font-weight:\s*(\d+)", block).group(1)
        st = re.search(r"font-style:\s*(\w+)", block).group(1)
        url = re.search(r"url\((\S+?)\)", block).group(1)
        fn = os.path.join(out, f"{name.strip().replace(' ', '')}-{w}{'i' if st == 'italic' else ''}.ttf")
        if not os.path.exists(fn):
            with urllib.request.urlopen(url, timeout=60) as r, open(fn, "wb") as f: shutil.copyfileobj(r, f)
        got.append(fn)
    return got


def install(paths):
    if os.name == "nt":
        dst = os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Windows", "Fonts")
    else:
        dst = os.path.expanduser("~/.local/share/fonts/design-lab")
    os.makedirs(dst, exist_ok=True)
    for p in paths: shutil.copy2(p, dst)
    if os.name != "nt" and shutil.which("fc-cache"): subprocess.run(["fc-cache", "-f", dst], check=False)
    if os.name == "nt":
        import winreg  # per-user registration so apps see them without admin rights
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts", 0, winreg.KEY_SET_VALUE) as k:
            for p in paths:
                winreg.SetValueEx(k, os.path.splitext(os.path.basename(p))[0] + " (TrueType)", 0, winreg.REG_SZ, os.path.join(dst, os.path.basename(p)))
    print(f"installed {len(paths)} files into {dst}")


def main():
    if len(sys.argv) < 3: sys.exit(__doc__)
    out = sys.argv[2]; os.makedirs(out, exist_ok=True)
    allf = []
    for spec in families(sys.argv[1]):
        try:
            f = fetch(spec, out); allf += f; print(f"{spec}: {len(f)} files")
        except Exception as e:
            print(f"{spec}: FAILED {e}")
    if "--install" in sys.argv and allf: install(allf)


if __name__ == "__main__":
    main()
