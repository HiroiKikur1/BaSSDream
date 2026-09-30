import paths
import os
from PIL import Image, ImageDraw

COVERS_DIR = paths.cache(r"covers")
LOGO_DIR = os.path.join(paths.ASSETS, r"band_logos")
os.makedirs(COVERS_DIR, exist_ok=True)

# accent colour + official logo file per band (logo None: accent-only card)
BAND_THEMES = {
    "roselia": {"accent": (136, 70, 220), "logo": "roselia"},
    "morfonica": {"accent": (56, 160, 230), "logo": "morfonica"},
    "ave mujica": {"accent": (200, 30, 60), "logo": "avemujica", "dark": True},
    "avemujica": {"accent": (200, 30, 60), "logo": "avemujica", "dark": True},
    "mygo": {"accent": (40, 150, 200), "logo": "mygo"},
    "poppin": {"accent": (255, 59, 114), "logo": "popipa"},
    "raise a suilen": {"accent": (30, 170, 140), "logo": "ras"},
    "afterglow": {"accent": (230, 60, 60), "logo": "afterglow"},
    "pastel": {"accent": (255, 120, 170), "logo": "pasupare"},
    "ハロー、ハッピーワールド": {"accent": (255, 190, 40), "logo": "hhw"},
    "millsage": {"accent": (120, 120, 140), "logo": "millsage"},
    "一家dumb rock": {"accent": (240, 130, 30), "logo": "ikka"},
    "leo/need": {"accent": (68, 85, 221), "logo": None},
    "more more jump": {"accent": (136, 221, 68), "logo": None},
    "vivid bad squad": {"accent": (238, 17, 102), "logo": None},
    "25時": {"accent": (136, 68, 170), "logo": None},
}


def get_band_theme(artist: str, folder: str):
    combined = f"{artist} {folder}".lower()
    for key, val in BAND_THEMES.items():
        if key in combined:
            return val
    return {"accent": (150, 140, 165), "logo": None}


def generate_procedural_jacket(song_id: str, title: str, artist: str, folder: str) -> str:
    """Fallback jacket when no verified album art exists: official band logo on a plain card, no text
    (title and artist are shown right under the cover)."""
    if "古典" in (folder or "") or "bach" in (artist or "").lower():
        from classical_cover import apply_to
        apply_to([song_id])
        return os.path.join(COVERS_DIR, f"{song_id}.jpg")
    out_path = os.path.join(COVERS_DIR, f"{song_id}.jpg")
    if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
        return out_path
    render_fallback_jacket(artist, folder).save(out_path, "JPEG", quality=92)
    return out_path


def render_fallback_jacket(artist: str, folder: str) -> Image.Image:
    size = 600
    theme = get_band_theme(artist, folder)
    accent = theme["accent"]
    dark = theme.get("dark", False)
    top, bottom = ((34, 26, 32), (14, 10, 14)) if dark else ((255, 255, 255), (238, 234, 242))
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        k = y / (size - 1)
        d.line([(0, y), (size, y)], fill=tuple(int(a * (1 - k) + b * k) for a, b in zip(top, bottom)))

    # diagonal accent bands, low opacity
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i, x in enumerate(range(-size, size * 2, 90)):
        ld.polygon([(x, size), (x + 36, size), (x + 36 + size * 0.6, 0), (x + size * 0.6, 0)],
                   fill=accent + ((34 if i % 2 else 18),))
    ld.rectangle([0, size - 14, size, size], fill=accent + (255,))
    img = Image.alpha_composite(img.convert("RGBA"), layer)

    logo_path = os.path.join(LOGO_DIR, f"{theme['logo']}.png") if theme.get("logo") else ""
    if logo_path and os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        w = 440
        logo = logo.resize((w, int(logo.height * w / logo.width)), Image.LANCZOS)
        img.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2 - 10))
    return img.convert("RGB")
