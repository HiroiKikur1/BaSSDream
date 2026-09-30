import os
import math
import tempfile
from typing import Optional, List, Dict, Any
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_NAME = "Helvetica"
CHINESE_FONT_PATHS = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

for p in CHINESE_FONT_PATHS:
    if os.path.exists(p):
        try:
            pdfmetrics.registerFont(TTFont("CJKFont", p))
            FONT_NAME = "CJKFont"
            break
        except Exception:
            pass

def render_musicxml_with_verovio(musicxml_content: str, output_pdf_path: str) -> bool:
    """
    Renders MusicXML content into publication-grade vector PDF using Verovio and svglib.
    Produces dual-stave standard notation + tablature with real rhythm stems/beams/flags/clefs/keysig.
    """
    try:
        import verovio
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPDF

        tk = verovio.toolkit()
        tk.setOptions({
            'pageWidth': 2100,
            'pageHeight': 2970,
            'scale': 35,
            'header': 'none',
            'footer': 'none',
            'spacingStaff': 9,
            'spacingSystem': 14
        })

        if not tk.loadData(musicxml_content):
            return False

        page_count = tk.getPageCount()
        if page_count < 1:
            return False

        c = canvas.Canvas(output_pdf_path, pagesize=A4)
        for page_num in range(1, page_count + 1):
            svg_data = tk.renderToSVG(page_num)
            with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as tf:
                tf.write(svg_data)
                tmp_svg = tf.name
            try:
                drawing = svg2rlg(tmp_svg)
                renderPDF.draw(drawing, c, 0, 0)
                c.showPage()
            finally:
                if os.path.exists(tmp_svg):
                    try:
                        os.unlink(tmp_svg)
                    except Exception:
                        pass

        c.save()
        return os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 1024
    except Exception as e:
        print(f"Error rendering with Verovio: {e}")
        return False

def generate_bass_tab_pdf(
    output_pdf_path: str,
    title: str,
    artist: str,
    franchise: str = "BanG Dream!",
    tempo: float = 120.0,
    is_5string: bool = False,
    tuning: str = "E A D G",
    gp_path: Optional[str] = None
) -> str:
    """
    Generates an authentic, high-quality multi-page Bass Tab PDF score.
    Extracts real notes, frets, strings, key signatures, and rhythm from .gp or .gp5 files.
    """
    dirname = os.path.dirname(output_pdf_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    target_gp = gp_path
    if not target_gp and dirname and os.path.exists(dirname):
        for f in os.listdir(dirname):
            if f.lower().endswith(('.gp', '.gp5', '.gpx')):
                target_gp = os.path.join(dirname, f)
                break

    # 1. Preferred Primary Pipeline: Verovio native notation + tablature engine
    if target_gp and os.path.exists(target_gp):
        try:
            from gp_to_musicxml import convert_gp_to_musicxml
            xml_str = convert_gp_to_musicxml(target_gp, title=title, artist=artist)
            if xml_str and render_musicxml_with_verovio(xml_str, output_pdf_path):
                return output_pdf_path
        except Exception as ex:
            print(f"Verovio pipeline error on {target_gp}: {ex}")

    # 2. Fallback Pipeline if no GP file or parsing fails
    _generate_fallback_pdf(output_pdf_path, title, artist, franchise, tempo, is_5string, tuning, target_gp)
    return output_pdf_path

def _generate_fallback_pdf(
    output_pdf_path: str,
    title: str,
    artist: str,
    franchise: str,
    tempo: float,
    is_5string: bool,
    tuning: str,
    target_gp: Optional[str]
):
    score_notes: List[Dict[str, Any]] = []
    score_tempo = tempo
    score_is_5 = is_5string
    score_tuning = tuning
    score_measures = 16

    if target_gp and os.path.exists(target_gp):
        try:
            from tab_scanner import parse_gp_file, parse_gp5_file
            data = {}
            if target_gp.lower().endswith('.gp'):
                data = parse_gp_file(target_gp)
            elif target_gp.lower().endswith('.gp5'):
                data = parse_gp5_file(target_gp)

            if data:
                score_notes = data.get("notes", [])
                if data.get("tempo"): score_tempo = float(data["tempo"])
                if "is_5string" in data: score_is_5 = bool(data["is_5string"])
                if data.get("tuning"): score_tuning = str(data["tuning"])
                if data.get("measures"): score_measures = int(data["measures"])
                if data.get("title") and not title: title = data["title"]
                if data.get("artist") and not artist: artist = data["artist"]
        except Exception:
            pass

    bpm = max(30.0, score_tempo)
    bar_duration = 4.0 * (60.0 / bpm)
    measures_notes: Dict[int, List[Any]] = {}

    for n in score_notes:
        ts = n.get("timestamp", 0.0)
        m_idx = int(ts / bar_duration)
        rel_t = (ts % bar_duration) / bar_duration
        if m_idx not in measures_notes:
            measures_notes[m_idx] = []
        measures_notes[m_idx].append((rel_t, n.get("string", 0), n.get("fret", 0)))

    max_measure_from_notes = max(measures_notes.keys()) + 1 if measures_notes else 0
    total_measures = max(score_measures, max_measure_from_notes, 16)

    c = canvas.Canvas(output_pdf_path, pagesize=A4)
    width, height = A4

    num_strings = 5 if score_is_5 else 4
    string_spacing = 11
    system_height = (num_strings - 1) * string_spacing
    margin_x = 40
    stave_width = width - 80
    bars_per_system = 4
    bar_width = stave_width / float(bars_per_system)

    total_systems = math.ceil(total_measures / float(bars_per_system))
    sys_per_first_page = 4
    sys_per_other_page = 5

    systems_done = 0
    page_num = 1

    while systems_done < total_systems:
        is_first_page = (page_num == 1)
        systems_this_page = sys_per_first_page if is_first_page else sys_per_other_page

        if is_first_page:
            c.setFillColor(colors.HexColor("#1b143f"))
            c.rect(0, height - 85, width, 85, fill=1, stroke=0)
            c.setFillColor(colors.HexColor("#e6005c"))
            c.rect(0, height - 87, width, 2, fill=1, stroke=0)

            c.setFont("Helvetica-Bold", 8)
            c.setFillColor(colors.HexColor("#ff66aa"))
            c.drawString(40, height - 25, "BANG DREAM! BASS STATION · OFFICIAL BASS SCORE")

            c.setFont(FONT_NAME, 18)
            c.setFillColor(colors.white)
            c.drawString(40, height - 52, f"{title}")

            c.setFont(FONT_NAME, 10)
            c.setFillColor(colors.HexColor("#cbd5e1"))
            c.drawString(40, height - 70, f"{artist}  ·  {franchise}")

            c.setFillColor(colors.HexColor("#f8fafc"))
            c.roundRect(40, height - 128, width - 80, 30, 5, fill=1, stroke=0)
            c.setFont(FONT_NAME, 9)
            c.setFillColor(colors.HexColor("#334155"))
            c.drawString(55, height - 110, f"Tempo: {int(bpm)} BPM")
            c.drawString(180, height - 110, "Time: 4/4")
            c.drawString(280, height - 110, f"Instrument: Electric Bass ({'5-String' if score_is_5 else '4-String'})")
            c.drawString(450, height - 110, f"Tuning: {score_tuning}")

            start_y = height - 175
            system_pitch = 92
        else:
            c.setFont(FONT_NAME, 9)
            c.setFillColor(colors.HexColor("#64748b"))
            c.drawString(margin_x, height - 35, f"{title} - {artist}")
            c.setStrokeColor(colors.HexColor("#e2e8f0"))
            c.setLineWidth(0.8)
            c.line(margin_x, height - 42, margin_x + stave_width, height - 42)

            start_y = height - 85
            system_pitch = 100

        for s_idx in range(systems_this_page):
            if systems_done >= total_systems:
                break

            y = start_y - (s_idx * system_pitch)

            c.setFont("Helvetica-Bold", 10)
            c.setFillColor(colors.HexColor("#e6005c"))
            c.drawString(margin_x - 24, y + (system_height / 2) - 4, "TAB")

            c.setStrokeColor(colors.HexColor("#64748b"))
            c.setLineWidth(0.8)
            for s in range(num_strings):
                sy = y + (num_strings - 1 - s) * string_spacing
                c.line(margin_x, sy, margin_x + stave_width, sy)

            c.setStrokeColor(colors.HexColor("#334155"))
            c.setLineWidth(1.2)
            for b in range(bars_per_system + 1):
                bx = margin_x + b * bar_width
                c.line(bx, y, bx, y + system_height)

            for b in range(bars_per_system):
                current_bar_num = systems_done * bars_per_system + b
                bx = margin_x + b * bar_width

                c.setFont("Helvetica", 7.5)
                c.setFillColor(colors.HexColor("#94a3b8"))
                c.drawString(bx + 4, y + system_height + 4, f"m.{current_bar_num + 1}")

                bar_notes = measures_notes.get(current_bar_num, [])
                for rel_t, string_idx, fret_num in bar_notes:
                    nx = bx + (rel_t * (bar_width - 16)) + 8
                    s_clamped = max(0, min(num_strings - 1, string_idx))
                    ny = y + (num_strings - 1 - s_clamped) * string_spacing

                    c.setFillColor(colors.white)
                    c.rect(nx - 4, ny - 4, 8, 8, fill=1, stroke=0)

                    c.setFont("Helvetica-Bold", 8)
                    c.setFillColor(colors.HexColor("#0f172a"))
                    c.drawCentredString(nx, ny - 3, str(fret_num))

        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#94a3b8"))
        c.drawRightString(width - margin_x, 25, f"Page {page_num}")

        c.showPage()
        page_num += 1
        systems_done += systems_this_page