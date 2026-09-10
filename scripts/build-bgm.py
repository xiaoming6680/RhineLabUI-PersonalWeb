"""Build the boot sound and the instrumental background loop from the user's local
reference screen recording.

    python scripts/build-bgm.py path/to/recording.mp4 [--ffmpeg path] [--work dir]
                               [--model model.ckpt] [--skip-separation]

Steps
  1. Decode the recording's audio track (48 kHz stereo float).
  2. Cut the boot recording 1:1: from the first white frame (0.5 s) up to the
     first music sample. It is written unchanged as public/audio/boot-intro.ogg.
  3. Remove the vocals from the music with audio-separator (BS-Roformer by default).
  4. Track beats on the instrumental, then search for a bar-aligned loop whose
     material before and after the seam matches best; refine the seam by
     cross-correlation, bake a short equal-power crossfade into the loop end and
     write public/audio/bgm-loop.ogg plus src/bgm-loop.ts.

The recording and both derived files keep the source's provenance; they are not
covered by the repository's MIT licence. See public/audio/README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 48000
FIRST_FRAME = 0.5  # recording time of the first white frame = reference 6.76 s
ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "public" / "audio"
DEFAULT_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"


def ffmpeg_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def decode(ffmpeg: str, video: Path, out: Path) -> np.ndarray:
    if not out.exists():
        subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(video), "-vn", "-ar", str(RATE),
             "-ac", "2", "-c:a", "pcm_f32le", str(out)],
            check=True,
        )
    audio, rate = sf.read(out, dtype="float32")
    assert rate == RATE and audio.ndim == 2
    return audio


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-12))


def music_onset(audio: np.ndarray) -> int:
    """First sample of the music: the sound after the last silence of the boot."""
    mono = audio.mean(1)
    hop = RATE // 100
    frames = len(mono) // hop
    level = np.array([rms_db(mono[i * hop:(i + 1) * hop]) for i in range(frames)])
    # The music is the first sound that then stays above -60 dB for ten seconds,
    # entered from at least 0.2 s of silence; the boot sound before it is sparse.
    loud = level > -60
    silent = level < -70
    gap_end = None
    for i in range(20, len(level) - 1000):
        if silent[i - 20:i].all() and loud[i:i + 1000].all():
            gap_end = i
            break
    if gap_end is None:
        raise SystemExit("Could not find the silence before the music.")
    start = gap_end * hop
    threshold = 10 ** (-60 / 20)
    while start > 0 and abs(mono[start - 1]) > threshold:
        start -= 1
    return start


def encode_ogg(ffmpeg: str, wav: Path, ogg: Path, quality: float) -> None:
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-i", str(wav), "-c:a", "libvorbis", "-q:a",
         str(quality), str(ogg)],
        check=True,
    )


def separate(work: Path, mix: Path, model: str, ffmpeg: str) -> Path:
    # audio-separator looks for an executable literally named ffmpeg on PATH.
    binaries = work / "bin"
    binaries.mkdir(exist_ok=True)
    alias = binaries / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not alias.exists():
        import shutil

        shutil.copyfile(ffmpeg, alias)
        alias.chmod(0o755)
    os.environ["PATH"] = str(binaries) + os.pathsep + os.environ.get("PATH", "")
    from audio_separator.separator import Separator  # type: ignore

    out_dir = work / "stems"
    out_dir.mkdir(exist_ok=True)
    separator = Separator(
        output_dir=str(out_dir),
        output_format="WAV",
        model_file_dir=str(work / "models"),
        output_single_stem="Instrumental",
    )
    separator.load_model(model_filename=model)
    outputs = separator.separate(str(mix))
    files = [out_dir / o for o in outputs]
    instrumental = [f for f in files if "instrumental" in f.name.lower()]
    return instrumental[0] if instrumental else files[0]


def beat_grid(mono: np.ndarray):
    """Fit a rigid, constant-tempo beat grid (the music keeps one tempo) by
    maximising onset strength on a comb of beat positions, then sync features."""
    import librosa  # type: ignore

    sr, hop = 22050, 128
    y = librosa.resample(mono, orig_sr=RATE, target_sr=sr)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    fps = sr / hop
    t = np.arange(len(onset)) / fps
    end = t[-1]

    def comb(period: float, phase: float) -> float:
        return float(np.interp(np.arange(max(phase, 0), end, period), t, onset).mean())

    best = max(
        ((comb(p, phi), p, phi) for p in np.arange(0.40, 0.65, 0.0002) for phi in np.arange(0, 0.65, 0.005) if phi < p),
        key=lambda item: item[0],
    )
    score, period, phase = best
    for p in np.arange(period - 0.0003, period + 0.0003, 0.00002):
        for phi in np.arange(phase - 0.006, phase + 0.006, 0.0005):
            value = comb(p, phi)
            if value > score:
                score, period, phase = value, p, phi
    beats = np.arange(max(phase, 0), end, period)
    strength = np.interp(beats, t, onset)
    downbeat = max(range(4), key=lambda p: float(strength[p::4].sum()))
    windows = [
        float(strength[i:i + 60].mean() / onset.mean()) for i in range(0, len(beats) - 60, 60)
    ]
    frames = librosa.time_to_frames(beats, sr=sr, hop_length=512)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=512)
    rms = librosa.feature.rms(y=y, hop_length=512)
    sync = lambda f: librosa.util.sync(f, frames, aggregate=np.mean)[:, 1:]  # noqa: E731
    return {
        "tempo": 60 / period,
        "beats": beats,
        "phase": downbeat,
        "gridStrength": windows,
        "chroma": sync(chroma),
        "mfcc": sync(mfcc),
        "rms": sync(rms)[0],
    }


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = a.ravel()
    b = b.ravel()
    return float(1 - np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def loop_candidates(grid, first_bar: int, min_bars: int, max_bars: int, after=8, before=4):
    beats = grid["beats"]
    chroma, mfcc, rms = grid["chroma"], grid["mfcc"], grid["rms"]
    n = min(chroma.shape[1], mfcc.shape[1], len(rms))
    phase = grid["phase"]
    bars = list(range(phase, n - after, 4))
    results = []
    for a_i, a in enumerate(bars):
        if a_i < first_bar or a - before < 0:
            continue
        for b in bars[a_i + 1:]:
            length = (b - a) // 4
            if length < min_bars or length > max_bars or b + after > n:
                continue
            post = 0.6 * cosine(chroma[:, a:a + after], chroma[:, b:b + after]) + 0.4 * cosine(
                mfcc[1:, a:a + after], mfcc[1:, b:b + after]
            )
            pre = 0.6 * cosine(chroma[:, a - before:a], chroma[:, b - before:b]) + 0.4 * cosine(
                mfcc[1:, a - before:a], mfcc[1:, b - before:b]
            )
            level = abs(float(np.log10(rms[a:a + after].mean() + 1e-6) - np.log10(rms[b:b + after].mean() + 1e-6)))
            score = post + 0.5 * pre + 0.8 * level
            results.append(
                {"startBeat": a, "endBeat": b, "bars": length, "score": score, "post": post,
                 "pre": pre, "level": level, "start": float(beats[a]), "end": float(beats[b])}
            )
    results.sort(key=lambda r: r["score"])
    return results


def envelope(mono: np.ndarray, hop: int) -> np.ndarray:
    n = len(mono) // hop
    return np.sqrt(np.mean(mono[: n * hop].reshape(n, hop) ** 2, axis=1))


def refine_end(mono: np.ndarray, start: float, end: float, window=0.06) -> float:
    """Shift the loop end by up to ±window seconds so the rhythm around both
    seam points lines up (cross-correlation of the 4 ms energy envelope)."""
    hop = RATE // 250
    env = envelope(mono, hop)
    span = int(1.5 * RATE / hop)
    a = int(start * RATE / hop)
    ref = env[a - span:a + span]
    ref = ref - ref.mean()
    best, best_lag = -np.inf, 0
    for lag in range(-int(window * RATE / hop), int(window * RATE / hop) + 1):
        b = int(end * RATE / hop) + lag
        seg = env[b - span:b + span]
        seg = seg - seg.mean()
        c = float(np.dot(ref, seg) / (np.linalg.norm(ref) * np.linalg.norm(seg) + 1e-9))
        if c > best:
            best, best_lag = c, lag
    return end + best_lag * hop / RATE, best


def build_loop(instrumental: np.ndarray, start: float, end: float, fade: float):
    a, b = int(round(start * RATE)), int(round(end * RATE))
    n = int(fade * RATE)
    out = instrumental[:b].copy()
    t = np.linspace(0, 1, n, endpoint=False)[:, None]
    fade_out = np.cos(t * np.pi / 2)
    fade_in = np.sin(t * np.pi / 2)
    out[b - n:b] = out[b - n:b] * fade_out + instrumental[a - n:a] * fade_in
    ramp = np.linspace(0, 1, int(0.005 * RATE))[:, None]
    out[: len(ramp)] *= ramp
    return out, a / RATE, b / RATE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--work", default=str(ROOT / ".tools" / "bgm"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--skip-separation", action="store_true")
    parser.add_argument("--stem", help="use an already separated instrumental WAV instead of running the model")
    parser.add_argument("--music-start", type=float, default=0.0,
                        help="seconds of the separated music to skip before the loop file starts")
    parser.add_argument("--min-bars", type=int, default=24)
    parser.add_argument("--max-bars", type=int, default=64)
    parser.add_argument("--first-bar", type=int, default=2)
    parser.add_argument("--fade", type=float, default=0.5)
    parser.add_argument("--choose", type=int, default=0, help="pick the n-th ranked candidate")
    parser.add_argument("--start", type=float, help="force loop start (seconds in the loop file)")
    parser.add_argument("--end", type=float, help="force loop end (seconds in the loop file)")
    args = parser.parse_args()

    video = Path(args.video)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_path(args.ffmpeg)
    audio = decode(ffmpeg, video, work / "recording.wav")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()

    onset = music_onset(audio)
    first = int(FIRST_FRAME * RATE)
    intro = audio[first:onset]
    sf.write(work / "boot-intro.wav", intro, RATE, subtype="FLOAT")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    encode_ogg(ffmpeg, work / "boot-intro.wav", AUDIO_DIR / "boot-intro.ogg", 7)
    intro_seconds = len(intro) / RATE
    print(f"boot sound: {FIRST_FRAME:.2f}–{onset / RATE:.3f} s of the recording ({intro_seconds:.3f} s)")

    mix = work / "music-mix.wav"
    if not mix.exists():
        sf.write(mix, audio[onset:], RATE, subtype="FLOAT")
    instrumental_path = Path(args.stem) if args.stem else work / "instrumental.wav"
    if args.stem:
        pass
    elif not args.skip_separation or not instrumental_path.exists():
        stem = separate(work, mix, args.model, ffmpeg)
        data, rate = sf.read(stem, dtype="float32")
        if rate != RATE:
            import librosa  # type: ignore

            data = librosa.resample(data.T, orig_sr=rate, target_sr=RATE).T
        sf.write(instrumental_path, data, RATE, subtype="FLOAT")
    instrumental, stem_rate = sf.read(instrumental_path, dtype="float32")
    if stem_rate != RATE:
        import librosa  # type: ignore

        instrumental = librosa.resample(instrumental.T, orig_sr=stem_rate, target_sr=RATE).T
    music_offset = round(args.music_start, 3)
    instrumental = instrumental[int(music_offset * RATE):]
    mono = instrumental.mean(1)

    grid = beat_grid(mono)
    print(
        f"tempo {grid['tempo']:.3f} BPM, {len(grid['beats'])} beats, downbeat phase {grid['phase']}, "
        f"grid strength per 30 s: {[round(w, 2) for w in grid['gridStrength']]}"
    )
    candidates = loop_candidates(grid, args.first_bar, args.min_bars, args.max_bars)
    for c in candidates[:12]:
        print(
            f"  {c['start']:7.2f} → {c['end']:7.2f}  {c['bars']:2d} bars  score {c['score']:.3f}"
            f" (post {c['post']:.3f} pre {c['pre']:.3f} level {c['level']:.3f})"
        )
    if args.start is not None and args.end is not None:
        start, end = args.start, args.end
        chosen = {"start": start, "end": end, "forced": True}
    else:
        chosen = candidates[args.choose]
        start, end = chosen["start"], chosen["end"]
    end, alignment = refine_end(mono, start, end)
    loop, start, end = build_loop(instrumental, start, end, args.fade)
    peak = float(np.abs(loop).max())
    if peak > 0.98:
        loop *= 0.98 / peak
    sf.write(work / "bgm-loop.wav", loop, RATE, subtype="FLOAT")
    encode_ogg(ffmpeg, work / "bgm-loop.wav", AUDIO_DIR / "bgm-loop.ogg", 6)
    duration = len(loop) / RATE
    # Seam audition: the last 4 s of the loop into the first 4 s after loopStart, twice.
    a, b = int(start * RATE), int(end * RATE)
    seam = np.concatenate([loop[b - 4 * RATE:b], loop[a:a + 4 * RATE]] * 2)
    sf.write(work / "seam-preview.wav", seam, RATE, subtype="FLOAT")

    meta = {
        "source": {"file": video.name, "sha256": digest, "sampleRate": RATE},
        "bootIntro": {"recordingStart": FIRST_FRAME, "recordingEnd": onset / RATE,
                      "seconds": intro_seconds, "referenceStart": 6.76},
        "music": {"recordingStart": onset / RATE,
                  "stem": Path(args.stem).name if args.stem else instrumental_path.name,
                  "model": None if args.stem else args.model,
                  "skipped": music_offset},
        "loop": {"start": start, "end": end, "duration": duration, "crossfade": args.fade,
                 "tempo": grid["tempo"], "gridStrength": grid["gridStrength"],
                 "seamAlignment": alignment, "candidate": chosen,
                 "peak": min(peak, 0.98)},
    }
    (AUDIO_DIR / "bgm-source.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    number = lambda x: f"{x:.6f}".rstrip("0").rstrip(".")  # noqa: E731  (prettier style)
    (ROOT / "src" / "bgm-loop.ts").write_text(
        "// Generated by scripts/build-bgm.py; do not edit by hand.\n"
        "export const BGM_LOOP = {\n"
        "  /** Length of audio/boot-intro.ogg: the reference boot sound before the music starts. */\n"
        f"  introSeconds: {number(intro_seconds)},\n"
        "  /** Seconds of the recording's music skipped before the loop file starts. */\n"
        f"  musicOffset: {number(music_offset)},\n"
        "  /** audio/bgm-loop.ogg plays from 0 once, then repeats [loopStart, loopEnd). */\n"
        f"  loopStart: {number(start)},\n"
        f"  loopEnd: {number(end)},\n"
        f"  duration: {number(duration)},\n"
        "} as const;\n",
        encoding="utf-8",
    )
    print(f"loop {start:.3f} → {end:.3f} s ({(end - start):.2f} s, alignment {alignment:.3f}); file {duration:.2f} s")


if __name__ == "__main__":
    main()
