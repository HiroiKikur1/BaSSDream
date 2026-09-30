import paths
import os
import re
import json
import base64
import struct
import hashlib
import requests
from typing import Optional, Dict, Any, List
from Crypto.Cipher import AES

COVERS_DIR = paths.cache(r"covers")
AUDIO_DIR = paths.cache(r"audio")

os.makedirs(COVERS_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://music.163.com/",
    "Cookie": "os=pc; osver=Microsoft-Windows-10-Professional-build-19045-64bit; appver=2.9.7.1998; channel=netease;"
}

session = requests.Session()
session.headers.update(HEADERS)

def search_song_candidates(keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Searches NetEase Cloud Music for multiple candidate audio tracks."""
    results = []
    try:
        data = {'s': keyword, 'type': 1, 'limit': limit, 'offset': 0}
        res = session.post('https://music.163.com/api/cloudsearch/pc', data=data, timeout=8)
        if res.status_code == 200:
            data = res.json()
            songs = data.get("result", {}).get("songs", [])
            for s in songs:
                s_id = s.get("id")
                cover = s.get("al", {}).get("picUrl", "")
                dur = round(s.get("dt", 0) / 1000.0, 1)
                results.append({
                    "audio_id": s_id,
                    "netease_id": s_id,
                    "title": s.get("name", keyword),
                    "artist": s.get("ar", [{}])[0].get("name", ""),
                    "album": s.get("al", {}).get("name", ""),
                    "duration": dur,
                    "cover_url": cover
                })
    except Exception as e:
        print(f"Error searching NetEase candidates for {keyword}: {e}")
    return results

def search_song_info(keyword: str) -> Optional[Dict[str, Any]]:
    """Searches NetEase Cloud Music for title, artist, duration and HD album cover, prioritizing full-length tracks."""
    candidates = search_song_candidates(keyword, limit=8)
    if not candidates:
        return None

    # Filter candidates: prioritize full-length tracks (duration >= 60s)
    kw_lower = keyword.lower()
    is_short_requested = "short" in kw_lower or "tv" in kw_lower

    if not is_short_requested:
        full_candidates = [c for c in candidates if c.get("duration", 0) >= 60.0]
        if full_candidates:
            # If any full candidate has an official album name (not empty), prefer it
            album_candidates = [c for c in full_candidates if c.get("album")]
            return album_candidates[0] if album_candidates else full_candidates[0]

    return candidates[0]

def download_cover_image(song_id: str, cover_url: str) -> str:
    """Downloads and saves album art locally to cache directory."""
    if not cover_url:
        return ""
    local_path = os.path.join(COVERS_DIR, f"{song_id}.jpg")
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
        return local_path

    try:
        r = requests.get(cover_url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            with open(local_path, "wb") as f:
                f.write(r.content)
            return local_path
    except Exception as e:
        print(f"Error downloading cover: {e}")
    return ""

def decrypt_ncm(ncm_path: str, output_dir: str = AUDIO_DIR) -> Dict[str, Any]:
    """Decrypts NetEase Cloud Music .ncm encrypted files into standard MP3/FLAC."""
    core_key = bytes.fromhex('6856705b73736e41786b615741686563')
    meta_key = bytes.fromhex('2331346c6a6b5f215c5d2630553c2728')

    with open(ncm_path, 'rb') as f:
        header = f.read(8)
        if header != b'CTENFDAM':
            raise ValueError("Not a valid NCM file header")

        f.seek(2, 1) # gap
        key_len = struct.unpack('<I', f.read(4))[0]
        key_data = bytearray(f.read(key_len))
        for i in range(len(key_data)):
            key_data[i] ^= 0x64

        cryptor = AES.new(core_key, AES.MODE_ECB)
        key_data = cryptor.decrypt(bytes(key_data))
        key_data = key_data[17:]
        key_data = key_data[:-key_data[-1]] # unpad

        # Key box
        key_box = bytearray(range(256))
        c = 0
        last_byte = 0
        key_offset = 0
        for i in range(256):
            swap = key_box[i]
            c = (swap + last_byte + key_data[key_offset % len(key_data)]) & 0xff
            key_offset += 1
            key_box[i] = key_box[c]
            key_box[c] = swap
            last_byte = c

        # Meta data
        meta_len = struct.unpack('<I', f.read(4))[0]
        meta_data = {}
        format_ext = "mp3"
        if meta_len > 0:
            meta_raw = bytearray(f.read(meta_len))
            for i in range(len(meta_raw)):
                meta_raw[i] ^= 0x63
            meta_raw = base64.b64decode(meta_raw[22:])
            cryptor_meta = AES.new(meta_key, AES.MODE_ECB)
            meta_dec = cryptor_meta.decrypt(meta_raw)
            meta_dec = meta_dec[:-meta_dec[-1]]
            try:
                meta_json = meta_dec.decode('utf-8')[6:]
                meta_data = json.loads(meta_json)
                format_ext = meta_data.get("format", "mp3")
            except:
                pass

        f.seek(5, 1) # gap
        crc32 = struct.unpack('<I', f.read(4))[0]
        f.seek(crc32, 1) # image gap

        # Image cover
        image_len = struct.unpack('<I', f.read(4))[0]
        image_bytes = f.read(image_len) if image_len > 0 else b""

        # Audio data
        base_name = os.path.splitext(os.path.basename(ncm_path))[0]
        out_filename = f"{base_name}.{format_ext}"
        out_path = os.path.join(output_dir, out_filename)

        with open(out_path, 'wb') as out_f:
            while True:
                chunk = bytearray(f.read(0x8000))
                if not chunk:
                    break
                for i in range(len(chunk)):
                    j = (i + 1) & 0xff
                    chunk[i] ^= key_box[(key_box[j] + key_box[(key_box[j] + j) & 0xff]) & 0xff]
                out_f.write(chunk)

    return {
        "output_path": out_path,
        "format": format_ext,
        "meta": meta_data,
        "has_cover": len(image_bytes) > 0
    }

if __name__ == "__main__":
    res = search_song_info("Roselia BLACK SHOUT")
    print("Search Roselia:", res)
