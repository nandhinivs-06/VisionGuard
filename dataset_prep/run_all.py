"""Run the whole Member 1 pipeline on the public datasets:

  download -> build_videos -> organize -> split -> extract frames -> report

    python -m dataset_prep.run_all
    python -m dataset_prep.run_all --skip-download --fps 2
"""
import argparse

from . import build_videos, download, extract_frames, organize, report, split


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--fps", default="1", help="frames sampled per second of video")
    ap.add_argument("--max-frames-per-video", default="300")
    ap.add_argument("--link-mode", choices=["hardlink", "copy"], default="hardlink")
    a = ap.parse_args()

    if not a.skip_download:
        print("=== download ===")
        try:
            download.main([])
        except SystemExit as e:
            if e.code:
                print("Some downloads failed - continuing with what is available (rerun later to resume).")
    print("\n=== build_videos ===")
    build_videos.main(["--link-mode", a.link_mode])
    print("\n=== organize ===")
    organize.main(["--separate", "--link-mode", a.link_mode])
    print("\n=== split ===")
    split.main(["--materialize", "--link-mode", a.link_mode])
    print("\n=== extract frames ===")
    extract_frames.main(["--fps", a.fps, "--max-frames-per-video", a.max_frames_per_video])
    print("\n=== report ===")
    report.main()


if __name__ == "__main__":
    main()
