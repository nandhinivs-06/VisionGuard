"""Step 0 - Download the public fire/smoke VIDEO datasets into data/raw/<source>/.

No registration needed for any of them. Downloads are resumable: rerun the
command and finished files are skipped, partial files continue.

    python -m dataset_prep.download                 # everything (~3 GB)
    python -m dataset_prep.download --only kmu firesense
    python -m dataset_prep.download --list

See DATASETS.md for what each source contains, licences and citations.
"""
import argparse
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

import requests

from .common import DATA_DIR

RAW = DATA_DIR / "raw"
HF = "https://huggingface.co"
MIN_SEGMENTED = 8 * 2**20  # files bigger than this are fetched in parallel byte ranges
STALL_BYTES_PER_MIN = 16 << 10
UA = {"User-Agent": "Mozilla/5.0 (fire-smoke-cv-project dataset downloader)"}


def hf_files(repo, pattern):
    r = requests.get(f"{HF}/api/datasets/{repo}?blobs=true", headers=UA, timeout=60)
    r.raise_for_status()
    return [(f"{HF}/datasets/{repo}/resolve/main/{quote(s['rfilename'])}", s["rfilename"], s.get("size"))
            for s in r.json()["siblings"] if re.search(pattern, s["rfilename"])]


def zenodo_files(record):
    r = requests.get(f"https://zenodo.org/api/records/{record}", headers=UA, timeout=60)
    r.raise_for_status()
    return [(f["links"]["self"], f["key"], f.get("size")) for f in r.json()["files"]]


def apache_listing(base, exts=(".avi", ".mp4", ".mpg", ".wmv", ".mov"), depth=2):
    """Crawl a plain web directory listing (used for Bilkent VisiFire)."""
    out = []
    html = requests.get(base, headers=UA, timeout=60).text
    for href in sorted(set(re.findall(r'href="([^"?#]+)"', html))):
        if href.startswith(("/", "http", "..")) or href.startswith("."):
            continue
        url = urljoin(base, href)
        if href.endswith("/") and depth > 1:
            out += apache_listing(url, exts, depth - 1)
        elif href.lower().endswith(exts):
            out.append((url, unquote(url[len(VISIFIRE):]), None))
    return out


VISIFIRE = "http://signal.ee.bilkent.edu.tr/VisiFire/Demo/"

KMU = "https://cvpr.kmu.ac.kr/Dataset/"
USTC = "http://staff.ustc.edu.cn/~yfn/"


def fixed(base, names):
    """Plain URLs; ask the server for each size so big files can use parallel segments."""
    out = []
    for n in names:
        url = base + quote(n)
        try:
            size = int(requests.head(url, headers=UA, timeout=60, allow_redirects=True).headers.get("Content-Length", 0))
        except (requests.RequestException, ValueError):
            size = 0
        out.append((url, n, size or None))
    return out


# Public fire/smoke VIDEO datasets (no registration). See DATASETS.md.
SOURCES = {
    "firesense": dict(desc="FIRESENSE: 49 flame/smoke positive + negative videos (Zenodo 836749, CC-BY-4.0)",
                      files=lambda: zenodo_files(836749)),
    "kmu": dict(desc="KMU Fire & Smoke Database: flame, smoke, wildfire smoke + flame-like negatives (Keimyung Univ.)",
                files=lambda: fixed(KMU, ["Indoor_outdoor(short_distance)flame.zip",
                                          "Indoor_outdoor(short_distance)smoke.zip",
                                          "Wildfire_smoke.zip", "Smoke_flame_like_moving_object.zip"])),
    "visifire": dict(desc="Bilkent VisiFire: fire, forest smoke, smoke and negative clips",
                     files=lambda: apache_listing(VISIFIRE + "FireClips/") + apache_listing(VISIFIRE + "ForestSmoke/")
                     + apache_listing(VISIFIRE + "SmokeClips/") + apache_listing(VISIFIRE + "OtherClips/")),
    "ustc_vsd": dict(desc="USTC video smoke detection (Feiniu Yuan): 3 smoke + 3 non-smoke videos",
                     files=lambda: fixed(USTC, ["Dry_leaf_smoke_02.avi", "Cotton_rope_smoke_04.avi",
                                                "Black_smoke_517.avi", "Traffic_1000.avi",
                                                "Basketball_yard.avi", "Waving_leaves_895.avi"])),
    "hf_fire_videos": dict(desc="Fire / non-fire clips (HF zwanderer/smolvlm2-fire-videos, no licence - internal use)",
                           files=lambda: hf_files("zwanderer/smolvlm2-fire-videos", r"^videos/")),
}


def fetch_range(url, part, start, end, label, retries=60):
    """Download bytes [start, end] (inclusive, end=None -> to EOF) into `part`, resuming from its size."""
    for attempt in range(1, retries + 1):
        try:
            done = part.stat().st_size if part.exists() else 0
            if end is not None and done >= end - start + 1:
                return
            rng = f"bytes={start + done}-{'' if end is None else end}"
            headers = {**UA, **({"Range": rng} if start + done or end is not None else {})}
            with requests.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as r:
                if r.status_code == 416 and end is None:  # already complete
                    return
                r.raise_for_status()
                if "Range" in headers and r.status_code != 206:
                    if start or end is not None:
                        raise RuntimeError("server does not support ranged downloads")
                    done = 0  # server ignored Range -> restart from scratch
                expected = int(r.headers.get("Content-Length", 0))
                got, window_start, window_got = 0, time.time(), 0
                with open(part, "ab" if done else "wb") as f:
                    for chunk in r.iter_content(64 << 10):
                        f.write(chunk)
                        got += len(chunk)
                        window_got += len(chunk)
                        if time.time() - window_start > 60:  # reconnect if the server only trickles data
                            if window_got < STALL_BYTES_PER_MIN:
                                raise requests.ConnectionError("stalled (< 16 KB/min)")
                            window_start, window_got = time.time(), 0
            if expected and got < expected:  # connection closed early -> resume
                raise requests.ConnectionError(f"incomplete ({got:,} of {expected:,} bytes)")
            return
        except (requests.RequestException, OSError) as e:
            print(f"      retry {attempt}/{retries} for {label}: {e}", flush=True)
            time.sleep(min(5 * attempt, 60))
    raise RuntimeError(f"failed to download {label}")


def glob_escape(name):
    return re.sub(r"([\[\]*?])", r"[\1]", name)


def download(url, dst, size=None, segments=4):
    """Resumable download. Big files with a known size are fetched as `segments` parallel
    byte ranges (much faster on throttled servers); `size` also catches truncated files."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    if not size or size < MIN_SEGMENTED or segments < 2:
        fetch_range(url, part, 0, None, dst.name)
        if size and part.stat().st_size != size:
            raise RuntimeError(f"{dst.name}: got {part.stat().st_size:,} bytes, expected {size:,}")
        part.replace(dst)
        return

    step = -(-size // segments)
    ranges = [(i * step, min(size, (i + 1) * step) - 1) for i in range(segments)]
    segs = [dst.with_name(f"{dst.name}.seg{i}of{segments}") for i in range(segments)]
    # Segment files from a run with a different --segments cover other byte ranges: keep only
    # the first one (it starts at byte 0, so it is a valid prefix) and drop the rest.
    for old in sorted(dst.parent.glob(glob_escape(dst.name) + ".seg*")):
        if old in segs:
            continue
        if re.fullmatch(r"seg0(of\d+)?", old.suffix[1:]) and not part.exists():
            old.replace(part)
        else:
            old.unlink()
    if part.exists() and not segs[0].exists():  # reuse an older single-stream partial download
        with open(part, "r+b") as f:
            f.truncate(min(part.stat().st_size, ranges[0][1] + 1))
        part.replace(segs[0])

    stop = threading.Event()

    def progress():
        while not stop.wait(30):
            have = sum(p.stat().st_size for p in segs if p.exists())
            print(f"      {dst.name}: {have / 2**20:,.0f} / {size / 2**20:,.0f} MB ({100 * have / size:.1f}%)", flush=True)

    threading.Thread(target=progress, daemon=True).start()
    try:
        with ThreadPoolExecutor(segments) as ex:
            list(ex.map(lambda i: fetch_range(url, segs[i], *ranges[i], f"{dst.name} part {i + 1}"), range(segments)))
    finally:
        stop.set()
    with open(part, "wb") as out:
        for p in segs:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out, 16 << 20)
    if part.stat().st_size != size:
        raise RuntimeError(f"{dst.name}: got {part.stat().st_size:,} bytes, expected {size:,}")
    part.replace(dst)
    for p in segs:
        p.unlink()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=list(SOURCES))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-unzip", action="store_true")
    ap.add_argument("--workers", type=int, default=3, help="files downloaded at the same time")
    ap.add_argument("--segments", type=int, default=4, help="parallel connections per big file")
    args = ap.parse_args(argv)

    if args.list:
        for k, s in SOURCES.items():
            print(f"{k:<15} {s['desc']}")
        return

    failed = []
    for key in args.only or SOURCES:
        src = SOURCES[key]
        out = RAW / key
        print(f"\n=== {key}: {src['desc']}", flush=True)
        try:
            files = src["files"]()
        except requests.RequestException as e:
            print(f"   cannot list files ({e}) - skipped")
            failed.append(key)
            continue
        def get(item):
            i, (url, rel, size) = item
            dst = out / rel
            if dst.exists() and size and dst.stat().st_size != size:
                print(f"   {rel}: incomplete ({dst.stat().st_size:,} of {size:,} bytes) - resuming")
                dst.replace(dst.with_name(dst.name + ".part"))
            if not dst.exists():
                print(f"   [{i}/{len(files)}] {rel}", flush=True)
                try:
                    download(url, dst, size, args.segments)
                except RuntimeError as e:
                    print(f"   FAILED: {e}")
                    failed.append(f"{key}/{rel}")
                    return
                print(f"   done {rel}", flush=True)
            if dst.suffix == ".zip" and not args.no_unzip and not (out / dst.stem).exists():
                print(f"   unzipping {dst.name} ...", flush=True)
                try:
                    tmp = out / (dst.stem + ".unzipping")  # renamed only when complete
                    shutil.rmtree(tmp, ignore_errors=True)
                    with zipfile.ZipFile(dst) as z:
                        z.extractall(tmp)
                    tmp.rename(out / dst.stem)
                except zipfile.BadZipFile:
                    dst.rename(dst.with_name(dst.name + ".part"))  # resume it on the next run
                    print(f"   FAILED: {dst.name} is incomplete - rerun the command to resume")
                    failed.append(f"{key}/{rel}")

        with ThreadPoolExecutor(args.workers) as ex:
            list(ex.map(get, enumerate(files, 1)))
        if not any(f.startswith(key + "/") for f in failed):
            (out / "DONE").write_text(f"{len(files)} files\n", encoding="utf-8")
            print(f"   {key}: {len(files)} files ok", flush=True)

    print("\nFinished." if not failed else f"\nFinished with failures: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
