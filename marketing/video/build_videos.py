#!/usr/bin/env python3
"""Build the KBM DBManager promotional videos from their storyboards.

    python marketing/video/build_videos.py --footage <dir of 1920x1080 app screenshots> --out <dir>
        [--only overview linkedin teaser] [--tts-model kokoro-v1.0.onnx --tts-voices voices-v1.0.bin]

Everything a video says or shows is defined in STORYBOARDS below: scene type, footage (a real
screenshot of the running application), lower-third title, narration and subtitle. The script
renders each video (1920 x 1080, 30 fps, H.264 + AAC) with KBM branding (kbmgfx.py), synthesises
the narration with Kokoro TTS when a model is given, writes .srt subtitles, a storyboard and a
narration script (marketing/video/*.md are generated from the same data, so they cannot drift).

Claims must stay within docs/product/capability-matrix.md — no roadmap item is presented as
available.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kbmgfx as g  # noqa: E402

W, H, FPS, SR = 1920, 1080, 30, 48000
HERE = Path(__file__).resolve().parent

ENGINES = [  # the product's own engine marks (frontend/src/lib/engines.ts)
    ("Pg", "PostgreSQL", "#35649a"),
    ("My", "MySQL", "#9a5a10"),
    ("Ma", "MariaDB", "#7a5230"),
    ("SS", "SQL Server", "#a4373a"),
    ("Or", "Oracle", "#b23a20"),
    ("D2", "Db2 LUW", "#1f7a72"),
    ("Dz", "Db2 for z/OS", "#185a54"),
]


@dataclass
class Scene:
    kind: str  # cover | title | engines | footage | tagline | closing
    say: str = ""  # narration (what the voice reads)
    sub: str = ""  # subtitle text (defaults to `say`)
    shot: str = ""  # footage file name (without .png)
    label: str = ""  # lower-third label (upper case)
    title: str = ""  # lower-third or card title
    min_dur: float = 4.0
    pan: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # x0,y0 -> x1,y1 (fraction of slack)
    zoom: tuple[float, float] = (1.0, 1.06)
    extra: dict = field(default_factory=dict)

    @property
    def subtitle(self) -> str:
        return self.sub or self.say


TAGLINE = "Monitor. Manage. Secure. Optimize."
URL = "db-manager.readthedocs.io"

OVERVIEW = [
    Scene("cover", min_dur=4.2),
    Scene("title", say="KBM DBManager. Unified database operations, from Khorafi Business Machines.", min_dur=5.5),
    Scene(
        "engines",
        say="One platform for PostgreSQL, MySQL, MariaDB, Microsoft SQL Server, Oracle and Db2, with partial support for Db2 for zee OS.",
        sub="One platform for PostgreSQL, MySQL, MariaDB, Microsoft SQL Server, Oracle and Db2, with partial support for Db2 for z/OS.",
    ),
    Scene(
        "footage",
        shot="dashboard-overview",
        label="Fleet dashboard",
        title="Every server, one screen",
        say="The fleet dashboard shows every server you are allowed to see: health, sessions, critical findings, open advice and active alerts, at a glance.",
        pan=(0.2, 0.0, 0.1, 0.6),
    ),
    Scene(
        "footage",
        shot="topology",
        label="Fleet and topology",
        title="Organised by environment, site and cluster",
        say="Fleet views and an interactive topology organise servers by environment, site and cluster, with each engine clearly identified.",
        pan=(0.0, 0.3, 0.5, 0.2),
    ),
    Scene(
        "footage",
        shot="server-performance",
        label="Monitoring",
        title="Performance from an hour to ninety days",
        say="Collectors gather normalised metrics into VictoriaMetrics, so performance trends stay fast, from the last hour to the last ninety days.",
        pan=(0.1, 0.2, 0.4, 0.7),
    ),
    Scene(
        "footage",
        shot="sessions",
        label="Sessions and locks",
        title="Who is waiting, and on whom",
        say="Live sessions, locks and blocking chains show who is waiting, and on whom. Cancelling a session needs the right role, and is audited.",
        pan=(0.5, 0.0, 0.2, 0.4),
    ),
    Scene(
        "footage",
        shot="query-performance",
        label="Query performance",
        title="The statements that matter",
        say="Query statistics, regression detection and execution plans point to the statements that matter.",
        pan=(0.0, 0.1, 0.6, 0.5),
    ),
    Scene(
        "footage",
        shot="security-posture",
        label="Security",
        title="Posture scored from evidence",
        say="Security posture scores each server from evidence: privileges, accounts, authentication and encryption.",
        pan=(0.3, 0.0, 0.0, 0.5),
    ),
    Scene(
        "footage",
        shot="recommendations",
        label="Recommendations",
        title="116 rules, evidence first",
        say="A hundred and sixteen rules turn telemetry into recommendations, each with evidence, impact and risk. Nothing is applied automatically.",
        pan=(0.0, 0.0, 0.4, 0.6),
    ),
    Scene(
        "footage",
        shot="backup-dashboard",
        label="Backup and restore",
        title="Verified backups, guided restores",
        say="Backup and Restore protects the platform and the managed databases, with verified copies and guided restores.",
        pan=(0.2, 0.1, 0.5, 0.5),
    ),
    Scene(
        "footage",
        shot="database-onboarding-engine-selection",
        label="Onboarding",
        title="Least privilege, tested live",
        say="Guided wizards register a server with a least-privilege account and a live, staged connection test. Monitoring only is the default.",
        pan=(0.5, 0.3, 0.5, 0.6),
        zoom=(1.0, 1.04),
    ),
    Scene(
        "footage",
        shot="collectors",
        label="Enterprise platform",
        title="Secure, auditable, cloud native",
        say="Scoped roles, LDAP and OIDC sign-in, multi-factor authentication and a full audit trail are built in. KBM DBManager runs on Docker Compose or Kubernetes, with signed images and a Helm chart.",
        pan=(0.0, 0.0, 0.15, 0.05),
        zoom=(1.08, 1.22),
    ),
    Scene(
        "tagline", say="Monitor, manage, secure and optimise. Every database, one platform.", sub=TAGLINE, min_dur=5.0
    ),
    Scene("closing", say="KBM DBManager.", min_dur=6.0),
]

LINKEDIN = [
    Scene("cover", min_dur=3.4),
    Scene("title", say="Meet KBM DBManager: unified database operations from Khorafi Business Machines.", min_dur=5.0),
    Scene("engines", say="PostgreSQL, MySQL, MariaDB, SQL Server, Oracle and Db2, on one platform."),
    Scene(
        "footage",
        shot="dashboard-overview",
        label="Fleet dashboard",
        title="Every server, one screen",
        say="See the health of your whole fleet at a glance.",
        pan=(0.2, 0.0, 0.1, 0.6),
    ),
    Scene(
        "footage",
        shot="server-performance",
        label="Monitoring",
        title="Performance, from an hour to ninety days",
        say="Follow performance, sessions and blocking as they happen.",
        pan=(0.1, 0.2, 0.4, 0.7),
    ),
    Scene(
        "footage",
        shot="recommendations",
        label="Recommendations",
        title="116 rules, evidence first",
        say="Act on evidence-based security and performance recommendations.",
        pan=(0.0, 0.0, 0.4, 0.6),
    ),
    Scene(
        "footage",
        shot="backup-dashboard",
        label="Backup and restore",
        title="Verified backups, guided restores",
        say="Protect the platform and your databases with verified backups.",
        pan=(0.2, 0.1, 0.5, 0.5),
    ),
    Scene(
        "footage",
        shot="collectors",
        label="Enterprise platform",
        title="Kubernetes ready, audited end to end",
        say="Role-based access, audit, and a Helm chart for Kubernetes are built in.",
        pan=(0.0, 0.0, 0.15, 0.05),
        zoom=(1.08, 1.22),
    ),
    Scene("tagline", say="Monitor. Manage. Secure. Optimize.", sub=TAGLINE, min_dur=4.0),
    Scene("closing", say="KBM DBManager, by K B M.", sub="KBM DBManager, by KBM.", min_dur=5.0),
]

TEASER = [
    Scene("cover", min_dur=2.8),
    Scene("title", say="KBM DBManager.", min_dur=3.2),
    Scene(
        "footage",
        shot="dashboard-overview",
        label="Unified database operations",
        title="Every database, one platform",
        say="Every database. One platform.",
        min_dur=3.6,
        pan=(0.2, 0.0, 0.1, 0.5),
    ),
    Scene("engines", say="", min_dur=2.6),
    Scene("tagline", say="Monitor. Manage. Secure. Optimize.", sub=TAGLINE, min_dur=3.0),
    Scene("closing", min_dur=3.4),
]

STORYBOARDS = {
    "overview": ("kbm-dbmanager-product-overview", "Product overview (main / YouTube version)", OVERVIEW, False),
    "linkedin": ("kbm-dbmanager-linkedin-promo", "LinkedIn version (burned-in subtitles)", LINKEDIN, True),
    "teaser": ("kbm-dbmanager-teaser", "Short teaser", TEASER, True),
}

# ----------------------------------------------------------------------------------- audio


def synth(text: str, kokoro, voice: str) -> np.ndarray:  # type: ignore[no-untyped-def]
    if not text or kokoro is None:
        return np.zeros(0, dtype=np.float32)
    samples, sr = kokoro.create(text, voice=voice, speed=0.98, lang="en-us")
    x = np.asarray(samples, dtype=np.float32)
    # resample 24 kHz -> 48 kHz (linear is fine for speech at this ratio)
    t = np.arange(int(len(x) * SR / sr)) * sr / SR
    return np.interp(t, np.arange(len(x)), x).astype(np.float32)


def pad_music(seconds: float) -> np.ndarray:
    """A quiet ambient pad (generated here, so it has no licence questions): slow chords of
    soft partials with long attack and release."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    chords = [(220.0, 261.63, 329.63), (196.0, 246.94, 293.66), (174.61, 220.0, 261.63), (196.0, 246.94, 329.63)]
    out = np.zeros(n, dtype=np.float64)
    seg = 6.0
    for i in range(int(seconds // seg) + 1):
        a, b = int(i * seg * SR), min(n, int((i * seg + seg + 2) * SR))
        if a >= n:
            break
        tt = t[a:b] - i * seg
        env = np.clip(tt / 2.2, 0, 1) * np.clip((seg + 2 - tt) / 2.2, 0, 1)
        for f in chords[i % len(chords)]:
            out[a:b] += env * (np.sin(2 * np.pi * f * tt) + 0.35 * np.sin(2 * np.pi * 2 * f * tt + 0.3)) / 3
        out[a:b] += env * 0.5 * np.sin(2 * np.pi * chords[i % len(chords)][0] / 2 * tt)
    fade = np.clip(t / 1.5, 0, 1) * np.clip((seconds - t) / 2.5, 0, 1)
    return (out * fade * 0.06).astype(np.float32)


# ----------------------------------------------------------------------------------- visuals


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


class Renderer:
    def __init__(self, footage: Path) -> None:
        self.footage = footage
        self.cache: dict[str, Image.Image] = {}
        self.blue = g.blue_canvas(W, H, bands=(0.885, 0.94), dots=(0.74, 0.66)).convert("RGB")
        self.blue_plain = g.blue_canvas(W, H, bands=(0.31, 0.44), dots=(0.72, 0.62)).convert("RGB")
        self.logo_full = g.logo("kbm-logo-white", 560)
        self.logo_small = g.logo("kbm-logo-white", 210)

    def shot(self, name: str) -> Image.Image:
        if name not in self.cache:
            self.cache[name] = Image.open(self.footage / f"{name}.png").convert("RGB")
        return self.cache[name]

    # each scene renders frame `i` of `n`
    def frame(self, s: Scene, i: int, n: int, burn: str | None) -> Image.Image:
        p = i / max(1, n - 1)
        im = getattr(self, f"_{s.kind}")(s, p, i / FPS)
        if burn:
            self._subtitle(im, burn)
        return im

    def _cover(self, s: Scene, p: float, t: float) -> Image.Image:
        im = self.blue_plain.copy().convert("RGBA")
        a = ease((t - 0.3) / 1.2)
        lg = self.logo_full.copy()
        lg.putalpha(lg.getchannel("A").point(lambda v: int(v * a)))
        y = int((H - lg.height) / 2 + 30 * (1 - a))
        im.alpha_composite(lg, ((W - lg.width) // 2, y))
        return im.convert("RGB")

    def _title(self, s: Scene, p: float, t: float) -> Image.Image:
        im = self.blue.copy().convert("RGBA")
        im.alpha_composite(self.logo_small, (120, 96))
        d = ImageDraw.Draw(im)
        a = ease((t - 0.2) / 0.8)
        dy = int(24 * (1 - a))
        g.tracked(
            d, (124, 470 + dy), "UNIFIED DATABASE OPERATIONS", g.font(26, 600), (255, 255, 255, int(190 * a)), 0.24
        )
        d.text((116, 512 + dy), "KBM DBManager", font=g.font(132, 700), fill=(255, 255, 255, int(255 * a)))
        b = ease((t - 0.9) / 0.8)
        d.text(
            (124, 690), "by Khorafi Business Machines (KBM)", font=g.font(34, 500), fill=(255, 255, 255, int(220 * b))
        )
        return im.convert("RGB")

    def _engines(self, s: Scene, p: float, t: float) -> Image.Image:
        im = self.blue.copy().convert("RGBA")
        im.alpha_composite(self.logo_small, (120, 96))
        d = ImageDraw.Draw(im)
        g.tracked(d, (124, 330), "ONE PLATFORM · SEVEN ENGINE FAMILIES", g.font(26, 600), (255, 255, 255, 190), 0.22)
        tile, gap = 150, 56
        x0 = (W - (len(ENGINES) * tile + (len(ENGINES) - 1) * gap)) // 2
        for k, (mono, label, hue) in enumerate(ENGINES):
            a = ease((t - 0.25 - k * 0.18) / 0.5)
            if a <= 0:
                continue
            x, y = x0 + k * (tile + gap), int(440 + 30 * (1 - a))
            card = Image.new("RGBA", (tile, tile), (0, 0, 0, 0))
            cd = ImageDraw.Draw(card)
            cd.rounded_rectangle([0, 0, tile - 1, tile - 1], 30, fill=hue, outline=(255, 255, 255, 60), width=2)
            if mono == "Dz":
                cd.rounded_rectangle([0, int(tile * 0.74), tile - 1, tile - 1], 30, fill=(11, 31, 29, 140))
            f = g.font(64, 700)
            tw = f.getlength(mono)
            cd.text(((tile - tw) / 2, tile * 0.22), mono, font=f, fill="white")
            card.putalpha(card.getchannel("A").point(lambda v, a=a: int(v * a)))
            im.alpha_composite(card, (x, y))
            fl = g.font(28, 600)
            d.text(
                (x + (tile - fl.getlength(label)) / 2, y + tile + 26),
                label,
                font=fl,
                fill=(255, 255, 255, int(235 * a)),
            )
        return im.convert("RGB")

    def _footage(self, s: Scene, p: float, t: float) -> Image.Image:
        im = self.blue.copy().convert("RGBA")
        src = self.shot(s.shot)
        cw, ch = 1560, 878  # 16:9 card
        z = s.zoom[0] + (s.zoom[1] - s.zoom[0]) * ease(p)
        vw, vh = int(src.width / z), int(src.height / z)
        x0, y0, x1, y1 = s.pan
        q = ease(p)
        sx = int((src.width - vw) * (x0 + (x1 - x0) * q))
        sy = int((src.height - vh) * (y0 + (y1 - y0) * q))
        view = src.crop((sx, sy, sx + vw, sy + vh)).resize((cw, ch), Image.LANCZOS)
        a = ease(t / 0.6)
        card = g.shadowed(view.convert("RGBA"), radius=18, blur=26, offset=14)
        cx = (W - card.width) // 2 + 120
        cy = (H - card.height) // 2 - 30 + int(20 * (1 - a))
        im.alpha_composite(card, (cx, cy))
        # lower third: navy bar with tracked label and bold title (KBM slide style)
        b = ease((t - 0.5) / 0.6)
        if s.title and b > 0:
            lt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(lt)
            ft, fl = g.font(46, 700), g.font(22, 600)
            wbar = int(max(ft.getlength(s.title), fl.getlength(s.label.upper()) * 1.25) + 96)
            x, y = 96 - int(40 * (1 - b)), 830
            d.rectangle([x, y, x + wbar, y + 138], fill=g.NAVY + (int(238 * b),))
            d.rectangle([x, y, x + 8, y + 138], fill=(0x06, 0x89, 0xD8, int(255 * b)))
            g.tracked(d, (x + 40, y + 26), s.label.upper(), fl, (204, 216, 237, int(255 * b)), 0.2)
            d.text((x + 38, y + 58), s.title, font=ft, fill=(255, 255, 255, int(255 * b)))
            im.alpha_composite(lt)
        im.alpha_composite(self.logo_small.resize((150, int(150 * 250 / 491))), (64, 48))
        return im.convert("RGB")

    def _tagline(self, s: Scene, p: float, t: float) -> Image.Image:
        im = self.blue_plain.copy().convert("RGBA")
        d = ImageDraw.Draw(im)
        words = TAGLINE.split(" ")
        f = g.font(84, 700)
        total = sum(f.getlength(w + " ") for w in words) - f.getlength(" ")
        x = (W - total) / 2
        for k, w in enumerate(words):
            a = ease((t - 0.2 - k * 0.35) / 0.45)
            d.text((x, 470 + int(20 * (1 - a))), w, font=f, fill=(255, 255, 255, int(255 * a)))
            x += f.getlength(w + " ")
        return im.convert("RGB")

    def _closing(self, s: Scene, p: float, t: float) -> Image.Image:
        im = self.blue_plain.copy().convert("RGBA")
        a = ease((t - 0.1) / 0.8)
        lg = g.logo("kbm-logo-white", 380) if "closing_logo" not in self.cache else self.cache["closing_logo"]
        self.cache["closing_logo"] = lg
        l2 = lg.copy()
        l2.putalpha(l2.getchannel("A").point(lambda v: int(v * a)))
        im.alpha_composite(l2, ((W - lg.width) // 2, 230))
        d = ImageDraw.Draw(im)
        b = ease((t - 0.7) / 0.8)
        f = g.font(84, 700)
        txt = "KBM DBManager"
        d.text(((W - f.getlength(txt)) / 2, 470), txt, font=f, fill=(255, 255, 255, int(255 * b)))
        f2 = g.font(32, 500)
        for k, line in enumerate(["Unified Database Operations", f"Documentation: {URL}"]):
            c = ease((t - 1.2 - k * 0.3) / 0.8)
            d.text(((W - f2.getlength(line)) / 2, 600 + k * 56), line, font=f2, fill=(255, 255, 255, int(225 * c)))
        return im.convert("RGB")

    @staticmethod
    def _subtitle(im: Image.Image, text: str) -> None:
        f = g.font(40, 600)
        lines, cur = [], ""
        for w in text.split():
            trial = f"{cur} {w}".strip()
            if f.getlength(trial) > 1500 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        y = H - 60 - len(lines) * 54
        for line in lines:
            tw = f.getlength(line)
            d.rounded_rectangle([(W - tw) / 2 - 22, y - 6, (W + tw) / 2 + 22, y + 50], 10, fill=g.NAVY + (216,))
            d.text(((W - tw) / 2, y), line, font=f, fill="white")
            y += 54
        base = im.convert("RGBA")
        base.alpha_composite(ov)
        im.paste(base.convert("RGB"))


# ----------------------------------------------------------------------------------- build


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def build(key: str, footage: Path, out: Path, kokoro, voice: str) -> dict:  # type: ignore[no-untyped-def]
    name, desc, scenes, burn = STORYBOARDS[key]
    voices = [synth(s.say, kokoro, voice) for s in scenes]
    lead, tail, xfade = 0.5, 0.9, 0.4
    durs = [max(s.min_dur, (len(v) / SR + lead + tail) if len(v) else 0) for s, v in zip(scenes, voices, strict=True)]
    starts = np.cumsum([0] + durs[:-1]).tolist()
    total = sum(durs)
    # audio: narration + ducked pad
    nar = np.zeros(int(total * SR) + SR, dtype=np.float32)
    cues = []
    for st, v, s in zip(starts, voices, scenes, strict=True):
        a = int((st + lead) * SR)
        nar[a : a + len(v)] += v * 0.9
        if s.subtitle:
            end = st + lead + (len(v) / SR if len(v) else min(3.0, durs[scenes.index(s)] - 0.8))
            cues.append((st + lead, end, s.subtitle))
    music = pad_music(len(nar) / SR)
    env = np.convolve(np.abs(nar), np.ones(SR // 5) / (SR // 5), mode="same")
    duck = 1.0 - 0.55 * np.clip(env / 0.05, 0, 1)
    mix = nar + music * duck
    mix = mix / max(1.0, float(np.max(np.abs(mix))) / 0.95)
    out.mkdir(parents=True, exist_ok=True)
    wav = out / f"{name}.wav"
    import soundfile as sf

    sf.write(wav, np.stack([mix, mix], axis=1), SR)
    # subtitles
    srt = out / f"{name}.srt"
    srt.write_text("\n".join(f"{k + 1}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n" for k, (a, b, t) in enumerate(cues)))
    # video frames piped to ffmpeg
    r = Renderer(footage)
    mp4 = out / f"{name}.mp4"
    ff = subprocess.Popen(  # noqa: S603 - fixed argv; paths come from the command line of this tool
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{W}x{H}",
            "-r",
            str(FPS),
            "-i",
            "-",
            "-i",
            str(wav),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(mp4),
        ],
        stdin=subprocess.PIPE,
    )
    assert ff.stdin
    nf = int(total * FPS)
    prev_last: Image.Image | None = None
    for k, (s, st, du) in enumerate(zip(scenes, starts, durs, strict=True)):
        n = int(round(du * FPS))
        # burn subtitles where the words are not already on screen as titles
        on_screen_text = s.kind in ("title", "tagline", "closing")
        sub_cue = (
            next((c for c in cues if abs(c[0] - (st + lead)) < 1e-6), None) if burn and not on_screen_text else None
        )
        last = None
        for i in range(n):
            tt = st + i / FPS
            burn_text = sub_cue[2] if sub_cue and sub_cue[0] <= tt <= sub_cue[1] else None
            fr = r.frame(s, i, n, burn_text)
            if prev_last is not None and i < int(xfade * FPS):
                fr = Image.blend(prev_last, fr, (i + 1) / (xfade * FPS))
            ff.stdin.write(fr.tobytes())
            last = fr
        prev_last = last
        print(f"  {name}: scene {k + 1}/{len(scenes)} {s.kind} {du:.1f}s", flush=True)
    ff.stdin.close()
    ff.wait()
    wav.unlink()
    # thumbnail (KBM cover layout + the dashboard card)
    thumb = out / f"{name}-thumbnail.png"
    make_thumbnail(r, thumb)
    return {
        "file": mp4.name,
        "title": desc,
        "seconds": round(total, 1),
        "frames": nf,
        "subtitles": srt.name,
        "scenes": [
            {
                "kind": s.kind,
                "shot": s.shot,
                "label": s.label,
                "title": s.title,
                "narration": s.say,
                "subtitle": s.subtitle,
                "start": round(st, 1),
                "seconds": round(du, 1),
            }
            for s, st, du in zip(scenes, starts, durs, strict=True)
        ],
    }


def make_thumbnail(r: Renderer, path: Path) -> None:
    im = g.blue_canvas(1280, 720, bands=(0.885, 0.94), dots=(0.06, 0.66))
    im.alpha_composite(g.logo("kbm-logo-white", 190), (64, 56))
    d = ImageDraw.Draw(im)
    g.tracked(d, (68, 300), "UNIFIED DATABASE OPERATIONS", g.font(19, 600), (255, 255, 255, 200), 0.22)
    d.text((62, 330), "KBM", font=g.font(96, 800), fill="white")
    d.text((62, 432), "DBManager", font=g.font(96, 800), fill="white")
    d.text((66, 560), TAGLINE, font=g.font(28, 600), fill=(255, 255, 255, 230))
    shot = r.shot("dashboard-overview").crop((240, 0, 1920, 945)).resize((720, 405), Image.LANCZOS)
    card = g.shadowed(shot.convert("RGBA"), radius=14, blur=18, offset=10)
    im.alpha_composite(card, (1280 - card.width + 120, 150))
    im.convert("RGB").save(path, optimize=True)


def write_docs(results: dict) -> None:
    lines = [
        "# Promotional videos — storyboards and narration",
        "",
        "Generated by `marketing/video/build_videos.py` from the storyboards in that file; edit the",
        "storyboards there and rebuild, never this page. Footage is real screenshots of the running",
        "application (simulated fleet, clearly labelled as simulated). Branding follows the",
        "[KBM DBManager brand guidelines](../../docs/branding/kbm-dbmanager-brand-guidelines.md#14-video-graphics).",
        "",
    ]
    for key, (name, desc, scenes, burn) in STORYBOARDS.items():
        res = results.get(key)
        lines += [
            f"## {desc}",
            "",
            f"File `{name}.mp4`"
            + (f", {res['seconds']} s" if res else "")
            + f", 1920 × 1080, 30 fps; subtitles `{name}.srt`"
            + (" (also burned in)" if burn else "")
            + "; thumbnail `"
            + name
            + "-thumbnail.png`.",
            "",
            "| # | Scene | Footage | On screen | Narration |",
            "|---|---|---|---|---|",
        ]
        for k, s in enumerate(scenes, 1):
            on = " — ".join(x for x in [s.label, s.title] if x) or {
                "cover": "KBM logo reveal",
                "title": "KBM DBManager · Unified Database Operations",
                "engines": "the seven engine marks",
                "tagline": TAGLINE,
                "closing": f"KBM logo · KBM DBManager · {URL}",
            }.get(s.kind, "")
            lines.append(f"| {k} | {s.kind} | {s.shot or '—'} | {on} | {s.subtitle or '—'} |")
        lines.append("")
    (HERE / "storyboards.md").write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--footage", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*", default=list(STORYBOARDS))
    ap.add_argument("--tts-model")
    ap.add_argument("--tts-voices")
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--docs-only", action="store_true")
    a = ap.parse_args()
    g.ensure_fonts()
    results: dict = {}
    if not a.docs_only:
        kokoro = None
        if a.tts_model:
            from kokoro_onnx import Kokoro

            kokoro = Kokoro(a.tts_model, a.tts_voices)
        for key in a.only:
            print(f"building {key}", flush=True)
            results[key] = build(key, a.footage, a.out, kokoro, a.voice)
        (a.out / "videos.json").write_text(json.dumps(results, indent=2))
    write_docs(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
