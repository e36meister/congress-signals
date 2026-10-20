"""Read-only test: can the runner download a video's audio (yt-dlp) and transcribe it locally (faster-whisper)?"""
import sys, subprocess, os, glob
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "yt-dlp", "faster-whisper"], check=False)
vids = os.environ.get("VIDS", "1LkboeNiQCs lgcJjnp9ec4").split()
os.makedirs("yt", exist_ok=True)
for v in vids:
    for client in ("default", "tv", "ios", "web_safari", "mweb"):
        args = [sys.executable, "-m", "yt_dlp", "-q", "-f", "bestaudio", "-o", f"yt/{v}.%(ext)s", f"https://www.youtube.com/watch?v={v}"]
        if client != "default":
            args[3:3] = ["--extractor-args", f"youtube:player_client={client}"]
        r = subprocess.run(args, capture_output=True, text=True, timeout=180)
        got = glob.glob(f"yt/{v}.*")
        print(v, client, "OK" if got else "FAILED", (r.stderr or "")[-300:].replace("\n", " "))
        if got:
            break
from faster_whisper import WhisperModel
m = WhisperModel("small", device="cpu", compute_type="int8")
for f in sorted(glob.glob("yt/*")):
    segs, info = m.transcribe(f, beam_size=1)
    print(f"\n=== {os.path.basename(f)} ({info.language}, {info.duration:.0f}s)\n" + " ".join(s.text.strip() for s in segs))
