import os
import re
import sys
import time
import hashlib
import requests

sys.stdout.reconfigure(encoding='utf-8')

TABS_ROOT = r"E:\BassStation\tabs"
COVERS_DIR = r"E:\BassStation\cache\covers"
os.makedirs(COVERS_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Referer': 'https://music.163.com/',
    'Cookie': 'os=pc; osver=Microsoft-Windows-10-Professional-build-19045-64bit; appver=2.9.7.1998; channel=netease;'
})

KNOWN_ARTISTS = [
    'Ave Mujica', 'AveMujica', 'Roselia', 'MyGO!!!!!', 'MyGO', 'RAISE A SUILEN', 'RAS', 'Poppin\'Party',
    'Afterglow', 'Pastel＊Palettes', 'Pastel_Palettes', 'Morfonica', 'Leo/need', 'Leo_need',
    '25時、ナイトコードで。', 'Vivid BAD SQUAD', 'MORE MORE JUMP！', 'MORE MORE JUMP',
    'CRYCHIC', 'あたらよ', '中島由貴', 'ヨルシカ', 'ツユ', '愛美', '青木陽菜',
    '羊宮妃那', 'ナナヲアカリ', 'yonige', 'millsage', 'Conton Candy', 'Novelbright',
    'Aqours', 'Sugar Rush', 'Heaven Burns Red', 'TRUE', '優木せつ菜', '桐谷遥',
    '宵崎奏', '東雲繪名', '日野森志歩', '日野森雫', '初音ミク', '初音未來', '鏡音レン', 'KAITO', 'MEIKO'
]

def extract_metadata(folder: str):
    artist = ""
    for a in KNOWN_ARTISTS:
        if a.lower() in folder.lower():
            artist = a.replace('AveMujica', 'Ave Mujica').replace('Leo_need', 'Leo/need').replace('Pastel_Palettes', 'Pastel＊Palettes')
            break

    cleaned_f = folder.replace('Mas_uerade', 'Masquerade').replace('Re_uest', 'Request')

    m = re.search(r'^[『「]\s*(.*?)\s*[』」]', cleaned_f)
    if m:
        title = m.group(1)
    else:
        parts = re.split(r'\s+[_\/／]\s+', cleaned_f)
        title = parts[0].strip()

    title = re.sub(r'\b[45](?:弦|st\.?)\b|[45]弦', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\b(Live\s*ver\.?|LIVE\s*ver\.?|short\s*ver\.?|TVOP\s*size|TV\s*size|game\s*size|Studio\s*ver\.?|FULL|Full|Acoustic\s*Ver\.?|先行|現場版|短版改編|完整版改編|ED\s*完整版)\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\b(LIVE|Live)\b', '', title)
    title = re.sub(r'[\(\)（）~～☆\+『』「」【】]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip().rstrip('. ')

    if not artist:
        parts = re.split(r'\s+[_\/／]\s+', cleaned_f)
        if len(parts) > 1 and not any(x in parts[1] for x in ['4弦', '5弦', '4st', '5st', 'LIVE', 'ver', 'short']):
            artist = parts[1]
        else:
            artist = "BanG Dream!"

    return title, artist

def fetch_cover_url(title: str, artist: str) -> str:
    queries = []
    if artist and artist != "BanG Dream!":
        queries.append(f"{artist} {title}")
    queries.append(title)

    for q in queries:
        if not q.strip():
            continue
        try:
            data = {'s': q, 'type': 1, 'limit': 1, 'offset': 0}
            r = session.post('https://music.163.com/api/cloudsearch/pc', data=data, timeout=5)
            if r.status_code == 200:
                res = r.json()
                songs = res.get('result', {}).get('songs', [])
                if songs:
                    pic = songs[0].get('al', {}).get('picUrl')
                    if pic:
                        return pic
        except Exception:
            pass
        time.sleep(0.05)
    return ""

def batch_update_all_covers():
    folders = [f for f in os.listdir(TABS_ROOT) if os.path.isdir(os.path.join(TABS_ROOT, f))]
    print(f"Starting batch official cover fetch for {len(folders)} songs...")
    success_count = 0
    skipped_count = 0

    for idx, folder in enumerate(folders):
        title, artist = extract_metadata(folder)
        song_id = hashlib.md5(folder.encode('utf-8')).hexdigest()[:12]
        target_path = os.path.join(COVERS_DIR, f"{song_id}.jpg")

        pic_url = fetch_cover_url(title, artist)
        if pic_url:
            try:
                r = session.get(pic_url, timeout=6)
                if r.status_code == 200 and len(r.content) > 1024:
                    with open(target_path, "wb") as f:
                        f.write(r.content)
                    success_count += 1
                    print(f"[{idx+1}/{len(folders)}] [SUCCESS] {title} by {artist}")
                    continue
            except Exception as e:
                pass

        skipped_count += 1
        print(f"[{idx+1}/{len(folders)}] [FALLBACK] {title} by {artist}")

    print(f"\n==========================================")
    print(f"BATCH COMPLETE!")
    print(f"Official Covers Downloaded: {success_count} / {len(folders)} ({success_count/len(folders)*100:.1f}%)")
    print(f"Procedural Fallbacks Kept: {skipped_count}")
    print(f"==========================================")

if __name__ == "__main__":
    batch_update_all_covers()
