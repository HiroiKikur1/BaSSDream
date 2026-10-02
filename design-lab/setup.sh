#!/usr/bin/env bash
# Design lab setup for Linux / the cloud container. Idempotent; safe to re-run.
# Installs: numpy + pillow (analysis tools), playwright-core (rendering), CJK fallback fonts,
# and the font library in fonts.txt (downloaded to design-lab/fonts, installed per user).
set -e
cd "$(dirname "$0")"
T=../.claude/skills/design-craft/tools

python3 -c "import numpy, PIL" 2>/dev/null || pip install -q -r requirements.txt
[ -d node_modules/playwright-core ] || npm install --silent --no-audit --no-fund
if ! fc-list :lang=zh family | grep -qi "Noto Sans CJK"; then
  (apt-get install -y -q fonts-noto-cjk >/dev/null 2>&1 || sudo apt-get install -y -q fonts-noto-cjk >/dev/null 2>&1) \
    || echo "warn: fonts-noto-cjk not installed; Chinese falls back to whatever the system has"
fi
python3 "$T/fonts.py" fonts.txt fonts --install | grep -v ": [0-9]* files" || true

# Chromium: the cloud image ships one under PLAYWRIGHT_BROWSERS_PATH; elsewhere use an installed Chrome/Edge.
echo "<p style=\"font:40px 'Shippori Mincho'\">鳴らす 贝斯 ♩=184</p>" > /tmp/design-lab-smoke.html
node "$T/render.cjs" /tmp/design-lab-smoke.html /tmp/design-lab-smoke.png --size 400x120 --wait 100 | grep -E "rendered|fonts drawn"
