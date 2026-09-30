import os
import zipfile
import json
import re
import copy
import uuid
import xml.etree.ElementTree as ET

def unroll_gp_repeats(gp_path: str, output_path: str = None) -> bool:
    from gp_guard import is_protected
    if (not output_path or os.path.abspath(output_path) == os.path.abspath(gp_path)) and is_protected(gp_path):
        print(f"[gp_guard] refused to modify protected score: {gp_path}")
        return False
    """
    Unrolls/expands repeated sections (MasterBars with Repeat start/end/count) into linear measures.
    Eliminates notation loops so that Guitar Pro 8's linear audio backing track plays in 1:1 synchronization
    without jumping back or repeating sections.
    """
    if output_path is None:
        output_path = gp_path

    if not os.path.exists(gp_path):
        return False

    try:
        with zipfile.ZipFile(gp_path, 'r') as z:
            entries = {item.filename: z.read(item.filename) for item in z.infolist()}

        if "Content/score.gpif" not in entries:
            return False

        gpif_str = entries["Content/score.gpif"].decode('utf-8', errors='ignore')
        if "<Repeat " not in gpif_str and "<Repeat>" not in gpif_str:
            return False

        root = ET.fromstring(gpif_str)
        master_bars_parent = root.find('.//MasterBars')
        bars_parent = root.find('.//Bars')

        if master_bars_parent is None or bars_parent is None:
            return False

        all_master_bars = master_bars_parent.findall('MasterBar')
        all_bars = {b.get('id'): b for b in bars_parent.findall('Bar')}

        max_bar_id = 0
        for bid in all_bars.keys():
            try:
                val = int(bid)
                if val > max_bar_id:
                    max_bar_id = val
            except Exception:
                pass

        new_bar_counter = max_bar_id + 1

        unrolled_mb_list = []
        i = 0
        repeat_start_idx = 0
        while i < len(all_master_bars):
            mb = all_master_bars[i]
            rep = mb.find('Repeat')

            if rep is not None and rep.get('start') == 'true':
                repeat_start_idx = len(unrolled_mb_list)

            unrolled_mb_list.append((mb, False))

            if rep is not None and rep.get('end') == 'true':
                try:
                    count = int(rep.get('count', '2'))
                except Exception:
                    count = 2

                if count < 1: count = 2
                block_to_clone = unrolled_mb_list[repeat_start_idx:]

                for pass_num in range(count - 1):
                    for src_mb, _ in block_to_clone:
                        unrolled_mb_list.append((src_mb, True))

                repeat_start_idx = len(unrolled_mb_list)

            i += 1

        master_bars_parent.clear()

        for mb_elem, is_clone in unrolled_mb_list:
            new_mb = copy.deepcopy(mb_elem)
            for r in new_mb.findall('Repeat'):
                new_mb.remove(r)
            for a in new_mb.findall('Alternative'):
                new_mb.remove(a)

            if is_clone:
                bar_ids_str = new_mb.findtext('Bars', '')
                old_bids = bar_ids_str.split()
                new_bids = []
                for obid in old_bids:
                    if obid in all_bars:
                        old_bar_elem = all_bars[obid]
                        new_bar_elem = copy.deepcopy(old_bar_elem)
                        new_id_str = str(new_bar_counter)
                        new_bar_counter += 1
                        new_bar_elem.set('id', new_id_str)
                        bars_parent.append(new_bar_elem)
                        all_bars[new_id_str] = new_bar_elem
                        new_bids.append(new_id_str)
                    else:
                        new_bids.append(obid)

                bars_node = new_mb.find('Bars')
                if bars_node is not None:
                    bars_node.text = " ".join(new_bids)

            master_bars_parent.append(new_mb)

        entries["Content/score.gpif"] = ET.tostring(root, encoding='utf-8')

        with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for name, data in entries.items():
                z.writestr(name, data)

        return True
    except Exception as e:
        print(f"Error unrolling repeats: {e}")
        return False

def inject_backing_track_to_gp(gp_path: str, audio_path: str) -> bool:
    from gp_guard import is_protected
    if is_protected(gp_path):
        print(f"[gp_guard] refused to modify protected score: {gp_path}")
        return False
    """
    Injects or updates a backing track audio reference inside a Guitar Pro 7/8 (.gp) file.
    Configures <BackingTrack>, <Assets>, and meta.json so GP8 loads the audio on opening.
    Automatically unrolls any repeat sections so the notation matches the linear audio 1:1.
    """
    if not os.path.exists(gp_path) or not audio_path or not os.path.exists(audio_path):
        return False
    if not gp_path.lower().endswith('.gp'):
        return False

    # Never unroll repeat marks: preserve authentic musical notation as requested
    abs_audio = os.path.abspath(audio_path).replace("\\", "/")

    entries = {}
    with zipfile.ZipFile(gp_path, "r") as z:
        for item in z.infolist():
            # Exclude old/stale audio assets to avoid zip bloat
            if not item.filename.startswith("Content/Assets/"):
                entries[item.filename] = z.read(item.filename)

    # 1. Embed raw audio bytes into Content/Assets/<uuid>.<ext>
    ext = os.path.splitext(audio_path)[1].lower()
    if not ext:
        ext = ".mp3"
    asset_uuid = str(uuid.uuid4())
    asset_rel_path = f"Content/Assets/{asset_uuid}{ext}"

    with open(audio_path, "rb") as af:
        audio_bytes = af.read()
    entries[asset_rel_path] = audio_bytes
    entries["Content/Assets/"] = b""

    # 2. Update meta.json
    meta = {}
    if "meta.json" in entries:
        try:
            meta = json.loads(entries["meta.json"].decode("utf-8"))
        except Exception:
            pass
    meta["hasAudio"] = True
    meta["version"] = meta.get("version", "1.0.0")
    entries["meta.json"] = json.dumps(meta, indent=4).encode("utf-8")

    # 3. Update Content/score.gpif
    if "Content/score.gpif" in entries:
        gpif = entries["Content/score.gpif"].decode("utf-8", errors="ignore")

        assets_block = f"""<Assets>
<Asset id="0">
<OriginalFilePath><![CDATA[{abs_audio}]]></OriginalFilePath>
<OriginalFileSha1>{asset_uuid}</OriginalFileSha1>
<EmbeddedFilePath>{asset_rel_path}</EmbeddedFilePath>
</Asset>
</Assets>"""

        if "<Assets>" in gpif:
            gpif = re.sub(r"<Assets>.*?</Assets>", assets_block, gpif, flags=re.DOTALL)
        elif "<ScoreViews>" in gpif:
            gpif = gpif.replace("<ScoreViews>", f"{assets_block}\n<ScoreViews>")
        else:
            gpif = gpif.replace("</GPIF>", f"{assets_block}\n</GPIF>")

        # Inject or update BackingTrack block
        backing_block = """<BackingTrack>
<IconId>21</IconId>
<Color>0 0 0</Color>
<Name><![CDATA[Audio Track]]></Name>
<ShortName><![CDATA[a.track]]></ShortName>
<PlaybackState>Default</PlaybackState>
<ChannelStrip>
<Parameters>0.500000 0.500000 0.500000 0.500000 0.500000 0.500000 0.500000 0.500000 0.500000 0.000000 0.500000 0.500000 0.680000 0.500000 0.500000 0.500000</Parameters>
</ChannelStrip>
<Enabled>true</Enabled>
<Source>Local</Source>
<AssetId>0</AssetId>
<YouTubeVideoUrl />
<Filter>6</Filter>
<FramesPerPixel>228</FramesPerPixel>
<FramePadding>0</FramePadding>
<Semitones>0</Semitones>
<Cents>0</Cents>
</BackingTrack>"""

        if "<BackingTrack>" in gpif:
            gpif = re.sub(r"<BackingTrack>.*?</BackingTrack>", backing_block, gpif, flags=re.DOTALL)
        elif "</MasterTrack>" in gpif:
            gpif = gpif.replace("</MasterTrack>", f"</MasterTrack>\n{backing_block}")
        elif "<Tracks>" in gpif:
            gpif = gpif.replace("<Tracks>", f"{backing_block}\n<Tracks>")

        entries["Content/score.gpif"] = gpif.encode("utf-8")

    # Write back
    with zipfile.ZipFile(gp_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)

    return True
