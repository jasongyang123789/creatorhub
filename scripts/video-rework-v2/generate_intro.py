"""CreatorHub / 创作工作台 — offline 2.5D editorial motion.

Run with the repo's .venv/Scripts/python.exe -IB, providing --out on G:.
The generator writes the video first, then samples its decoded frames for the
storyboard and stills. Inputs and font are read-only. No network is used.
"""
import argparse
import functools
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave


W, H, SS, FPS, LENGTH, RATE = 1080, 1920, 2, 30, 6, 48000
N = FPS * LENGTH
PAPER = (247, 245, 239)
INK = (24, 24, 23)
WHITE = (253, 252, 248)
BLUE = (29, 66, 231)
BLUE_EDGE = (21, 46, 174)
MUTE = (89, 88, 83)
HAIR = (204, 201, 192)
FONT_PATH = None
REPO = Path(__file__).resolve().parents[2]
VIDEO = 'CreatorHub-片头-v2-有声.mp4'
TITLE = '创作工作台'
HOOK = '把想法'
MESSAGE = '做成内容'
TAGLINE = '让每个想法落地'
BRAND = 'CREATORHUB'
CROPS = (
    ('creation.png', (0, 0, 532, 261)),
    ('packaging.png', (0, 0, 532, 261)),
    ('crm.png', (0, 0, 634, 311)),
)
BEATS = ('选题', '制作', '跟进')
PANEL_TITLES = ('创作入口', '包装设置', '本地客户工作台')
BOARD = (
    (38, '01 / 05', '把想法做成内容', '00.00–01.70  /  开场'),
    (67, '02 / 05', '选题', '01.70–02.30  /  创作入口'),
    (84, '03 / 05', '制作', '02.30–03.00  /  包装设置'),
    (109, '04 / 05', '跟进', '03.00–04.15  /  本地客户工作台'),
    (159, '05 / 05', '创作工作台', '04.15–06.00  /  品牌落版'),
)
STILLS = {38: '01-hook.png', 84: '02-workbench.png', 159: '03-brand.png'}


def point(x):
    return round(x * SS)


def coords(values):
    return tuple(map(point, values))


def ease(p):
    p = min(1., max(p, 0.))
    return 1 - (1 - p) ** 3


def blend(a, b, t):
    return tuple(round(i + (j - i) * t) for i, j in zip(a, b))


@functools.lru_cache(maxsize=40)
def font(px, factor=SS):
    return ImageFont.truetype(str(FONT_PATH), round(px * factor))


@functools.lru_cache(maxsize=100)
def glyph(text, px, max_width=9999, track=0):
    size = px
    while True:
        f = font(size)
        b = f.getbbox(text)
        tracked = bool(track and len(text) > 1)
        if tracked:
            advance = sum(f.getlength(c) for c in text[:-1]) + point(track) * (len(text) - 1)
            b = (f.getbbox(text[0])[0], b[1], math.ceil(advance + f.getbbox(text[-1])[2]), b[3])
        if b[2] - b[0] <= point(max_width):
            break
        size -= 1
        if size < min(12, px):
            raise ValueError(f'Text cannot fit: {text}')
    mask = Image.new('L', (b[2] - b[0], b[3] - b[1]))
    d = ImageDraw.Draw(mask)
    if tracked:
        x = -b[0]
        for c in text:
            d.text((round(x), -b[1]), c, fill=255, font=f)
            x += f.getlength(c) + point(track)
    else:
        d.text((-b[0], -b[1]), text, fill=255, font=f)
    return mask


def text(im, value, x, y, px, color=INK, *, width=890, track=0, align='left', p=1., slide=0.):
    if p <= 0:
        return
    mask = glyph(value, px, width, track)
    ox = point(x) - (mask.width if align == 'right' else 0)
    oy = point(y + (1 - p) * slide)
    if p < 1:
        height = round(p * mask.height)
        if height < 1:
            return
        # A glyph rises through a fixed horizontal baseline mask, not a fade.
        mask = mask.crop((0, 0, mask.width, height))
    assert 0 <= ox <= im.width - mask.width and 0 <= oy <= im.height - mask.height, value
    im.paste(color, (ox, oy, ox + mask.width, oy + mask.height), mask)


def rect(draw, box, fill):
    draw.rectangle(coords(box), fill=fill)


def line(draw, pts, fill, width=1):
    draw.line([coords(xy) for xy in pts], fill=fill, width=point(width))


def polygon(draw, pts, fill):
    draw.polygon([coords(xy) for xy in pts], fill=fill)


def soft_shadow(im, bounds, radius, strength):
    x0, y0, x1, y1 = bounds
    pad = radius * 3
    x, y = max(0, x0 - pad), max(0, y0 - pad)
    w, h = min(W, x1 + pad) - x, min(H, y1 + pad) - y
    m = Image.new('L', coords((w, h)))
    ImageDraw.Draw(m).rounded_rectangle(coords((x0 - x, y0 - y, x1 - x, y1 - y)),
                                         radius=point(17), fill=strength)
    m = m.filter(ImageFilter.GaussianBlur(point(radius)))
    im.paste((24, 22, 17), coords((x, y, x + w, y + h)), m)


def paper(color, dark=False):
    # Fixed microtexture: it never crawls between frames.
    y, x = np.ogrid[:H, :W]
    rng = np.random.default_rng(20261120)
    grain = rng.normal(0, .70 if dark else .84, (H, W)).astype('float32')
    light = (6.0 if dark else 4.2) * np.exp(-(((x - 218) / 740) ** 2 + ((y - 690) / 1120) ** 2))
    value = grain + light - (0 if dark else 2.1 * (y / H))
    rgb = np.clip(np.rint(value[:, :, None] + color), 0, 255).astype('uint8')
    return Image.fromarray(rgb).resize((W * SS, H * SS), Image.Resampling.BICUBIC)


def fold_mark(draw, x, y, size, color, inner):
    k = size * .29
    polygon(draw, [(x, y), (x + size - k, y), (x + size, y + k),
                   (x + size, y + size), (x, y + size)], color)
    polygon(draw, [(x + size - k, y), (x + size - k, y + k), (x + size, y + k)], inner)


def running_header(im, index):
    d = ImageDraw.Draw(im)
    fold_mark(d, 98, 240, 24, BLUE, BLUE_EDGE)
    text(im, BRAND, 139, 239, 29, width=690, track=2.6)
    text(im, f'{index:02d} / 03', 982, 243, 25, MUTE, track=1.5, align='right')
    line(d, [(98, 306), (982, 306)], HAIR)


class Intro:
    def __init__(self, assets):
        base = paper(PAPER)
        self.opening = base.copy()
        self.workbench = base
        self.closing = paper(INK, dark=True)
        running_header(self.opening, 1)
        running_header(self.workbench, 2)
        d = ImageDraw.Draw(self.workbench)
        text(self.workbench, '创作流程', 99, 443, 36, color=MUTE, track=1.8)
        line(d, [(98, 1501), (982, 1501)], HAIR)
        text(self.workbench, '实际工作台画面', 99, 1536, 32, color=MUTE, track=.6)
        self.faces = tuple(self.face(i, assets / name, box) for i, (name, box) in enumerate(CROPS))

    @staticmethod
    def face(i, source, box):
        with Image.open(source) as shot:
            if not (0 <= box[0] < box[2] <= shot.width and 0 <= box[1] < box[3] <= shot.height):
                raise ValueError(f'Crop outside {source}: {box}, {shot.size}')
            excerpt = shot.convert('RGB').crop(box).resize(coords((826, 405)), Image.Resampling.LANCZOS)
        im = Image.new('RGBA', coords((862, 516)))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=point(12), fill=WHITE,
                            outline=(188, 185, 176), width=point(1))
        text(im, PANEL_TITLES[i], 26, 23, 34, width=740)
        text(im, f'{i + 1:02d}', 835, 29, 23, MUTE, align='right')
        line(d, [(18, 72), (844, 72)], (219, 215, 205))
        im.paste(excerpt, coords((18, 84)))
        d.rectangle(coords((17, 83, 844, 489)), outline=(222, 218, 208), width=point(1))
        return im

    @functools.lru_cache(maxsize=2)
    def cover(self, n):
        im = self.opening.copy()
        t = n / FPS
        text(im, HOOK, 98, 550, 160, p=ease((t + .13) / .40), slide=49)
        p = ease((t - .16) / .39)
        if p <= 0:
            return im
        y = round(800 + 43 * (1 - p))
        bottom = round(y + 433 * p)
        fold = min(91, (bottom - y) * .60)
        left, right = 98, 982
        soft_shadow(im, (left + 9, y + 19, right + 1, bottom + 18), 31, round(63 * p))
        d = ImageDraw.Draw(im)
        polygon(d, [(left, y + 9), (right - fold, y + 9), (right, y + fold + 9),
                    (right, bottom + 9), (left, bottom + 9)], BLUE_EDGE)
        polygon(d, [(left, y), (right - fold, y), (right, y + fold),
                    (right, bottom), (left, bottom)], BLUE)
        polygon(d, [(right - fold, y), (right, y + fold), (right - fold, y + fold)], (50, 85, 238))
        line(d, [(left + 1, y + 1), (right - fold, y + 1)], (84, 110, 242), 1)
        tp = ease((t - .24) / .43)
        if bottom > y + 88:
            text(im, MESSAGE, 137, max(y + 26, 919), 156, WHITE, width=795, p=tp, slide=42)
        return im

    def panel(self, im, t, since, idx):
        move = ease((t - 1.70 + .045) / .43)
        lift = (1 - move) * 43
        x0, x1 = 98 + 23 * (1 - move), 960 - 16 * (1 - move)
        y0, y1 = 819 + lift, 1335 + lift - 32 * (1 - move)
        # As the frame settles closer to camera, its far edge straightens.
        skew = 28 * (1 - move)
        corners = [(x0, y0 + skew), (x1, y0), (x1, y1), (x0, y1 - skew * .35)]
        shadow_box = (round(x0 + 16), round(y0 + 20), round(x1 + 20), round(y1 + 37))
        soft_shadow(im, shadow_box, 36, 71)
        d = ImageDraw.Draw(im)
        polygon(d, [(x + 21, y + 24) for x, y in corners], BLUE)
        polygon(d, [corners[1], (corners[1][0] + 21, corners[1][1] + 24),
                    (corners[2][0] + 21, corners[2][1] + 24), corners[2]], BLUE_EDGE)
        face = self.faces[idx]
        if idx and since < .20:
            face = self.faces[idx - 1].copy()
            reveal = round(face.width * ease(since / .20))
            if reveal:
                face.paste(self.faces[idx].crop((0, 0, reveal, face.height)), (0, 0))
        if move >= 1:
            im.paste(face, coords((x0, y0)), face)
        else:
            lx, ly = math.floor(x0), math.floor(y0)
            rw, rh = math.ceil(x1 - lx + 1), math.ceil(y1 - ly + 1)
            src = [(0, 0), (face.width, 0), (face.width, face.height), (0, face.height)]
            mat, rhs = [], []
            for (x, y), (u, v) in zip(corners, src):
                a, b = (x - lx) * SS, (y - ly) * SS
                mat.extend([[a, b, 1, 0, 0, 0, -a * u, -b * u], [0, 0, 0, a, b, 1, -a * v, -b * v]])
                rhs.extend([u, v])
            inv = tuple(np.linalg.solve(mat, rhs))
            warped = face.transform(coords((rw, rh)), Image.Transform.PERSPECTIVE, inv, Image.Resampling.BICUBIC)
            im.paste(warped, coords((lx, ly)), warped)

    @functools.lru_cache(maxsize=10)
    def process(self, n):
        t = max(1.70, n / FPS)
        idx = 2 if t >= 3 else 1 if t >= 2.30 else 0
        since = t - (1.70, 2.30, 3.00)[idx]
        im = self.workbench.copy()
        d = ImageDraw.Draw(im)
        for j in range(3):
            x = 766 + j * 74
            rect(d, (x, 635, x + 66, 641), BLUE if j == idx else HAIR)
        p = ease((since + .11) / .42)
        text(im, BEATS[idx], 98, 546, 151, p=p, slide=32)
        self.panel(im, t, since, idx)
        return im

    @functools.lru_cache(maxsize=8)
    def brand(self, n):
        t = max(0., n / FPS - 4.15)
        im = self.closing.copy()
        d = ImageDraw.Draw(im)
        k = ease((t + .06) / .37)
        fold_mark(d, 98, 521 + 32 * (1 - k), 108, BLUE, BLUE_EDGE)
        text(im, TITLE, 95, 744, 155, WHITE, width=885, p=ease((t + .11) / .43), slide=34)
        text(im, TAGLINE, 99, 977, 53, (221, 219, 213), width=880, track=1.0,
             p=ease((t + .03) / .43), slide=22)
        line(d, [(98, 1207), (982, 1207)], (91, 89, 84))
        text(im, BRAND, 98, 1254, 45, WHITE, width=720, track=3.9, p=ease((t - .05) / .43), slide=17)
        text(im, '03 / 03', 982, 1266, 25, (193, 190, 182), track=1.5, align='right')
        return im

    @staticmethod
    def wipe(a, b, p):
        # Identical leading-edge wipe for both scene changes.
        if p <= 0:
            return a
        if p >= 1:
            return b
        im = a.copy()
        tip = (-80 + (W + 155) * p)
        d = ImageDraw.Draw(im)
        polygon(d, [(0, 0), (tip + 37, 0), (tip - 37, H), (0, H)], BLUE)
        m = Image.new('L', im.size)
        polygon(ImageDraw.Draw(m), [(0, 0), (tip + 10, 0), (tip - 64, H), (0, H)], 255)
        im.paste(b, (0, 0), m)
        return im

    @functools.lru_cache(maxsize=4)
    def frame(self, n):
        t = n / FPS
        if t < 1.70:
            im = self.cover(min(n, 21))
        elif t < 1.94:
            im = self.wipe(self.cover(21), self.process(n), ease((t - 1.70) / .24))
        elif t < 4.15:
            key = 65 if 65 <= n <= 68 else 79 if 79 <= n <= 89 else min(n, 100) if n >= 90 else n
            im = self.process(key)
        elif t < 4.39:
            im = self.wipe(self.process(100), self.brand(n), ease((t - 4.15) / .24))
        else:
            im = self.brand(min(n, 140))
        return im.resize((W, H), Image.Resampling.LANCZOS)

    def rgb_frames(self):
        # Reuse exact settled frames; there is no animation during the hold.
        held = None
        oldkey = None
        for n in range(N):
            key = (min(n, 21) if n < 51 else 65 if 65 <= n <= 68 else 79 if 79 <= n <= 89
                   else 100 if 100 <= n <= 124 else min(n, 140) if n >= 132 else n)
            if key != oldkey:
                held = self.frame(key).tobytes()
                oldkey = key
            yield held


def band_noise(n, rng, low, high):
    f = np.fft.rfftfreq(n, 1. / RATE)
    profile = (1. - np.exp(-(f / low) ** 2)) * np.exp(-(f / high) ** 2 * 1.7)
    sig = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * profile, n=n)
    return sig / max(1e-8, np.std(sig))


def score(path):
    rng = np.random.default_rng(60415)
    mix = np.zeros((RATE * LENGTH, 2))

    def add(start, mid, side=None, pan=0.):
        i = round(start * RATE)
        n = min(len(mid), len(mix) - i)
        sig = mid[:n]
        s = 0 if side is None else side[:n]
        mix[i:i + n, 0] += sig * (1 - pan) + s
        mix[i:i + n, 1] += sig * (1 + pan) - s

    def air(end, length, gain, pan):
        n = round(length * RATE)
        q = np.arange(n) / n
        env = np.sin(np.pi * q) ** 1.35 * (.52 + .56 * q)
        low = band_noise(n, rng, 170, 1420)
        high = band_noise(n, rng, 640, 5100)
        mid = (low * (1 - .62 * q) + high * (.20 + .76 * q)) * env * gain
        side = band_noise(n, rng, 380, 5150) * (env * gain * .17)
        add(end - length, mid, side, pan)

    def hit(start, gain, base, pan):
        n = round(.155 * RATE)
        t = np.arange(n) / RATE
        atk = 1 - np.exp(-t / .0017)
        phase = 2 * np.pi * (base * t + 29 * .021 * (1. - np.exp(-t / .021)))
        body = .72 * np.sin(phase) * np.exp(-t / .028)
        wood = (.25 * np.sin(2 * np.pi * 223 * t) + .14 * np.sin(2 * np.pi * 521 * t)) * np.exp(-t / .014)
        click = band_noise(n, rng, 620, 3750) * np.exp(-t / .009) * .11
        tail = np.minimum(1, (.155 - t) / .018)
        add(start, (body + wood + click) * atk * tail * gain, pan=pan)

    for end, length, gain, pan in [( .35, .24, .060, -.07), (1.71, .225, .085, .05),
                                  (2.31, .15, .045, -.05), (3.01, .15, .045, .04), (4.20, .25, .094, -.01)]:
        air(end, length, gain, pan)
    for start, gain, base, pan in [( .35, .35, 89, -.025), (1.70, .46, 83, .03), (2.30, .28, 98, -.02),
                                   (3.0, .29, 91, .02), (4.20, .52, 76, 0)]:
        hit(start, gain, base, pan)
    # Dadd9, soft felt-like harmonics with a short finishing tail.
    for j, (hz, amp, pan) in enumerate([(146.832, .125, -.11), (220., .099, .09),
                                        (369.994, .089, -.06), (329.628, .064, .16)]):
        onset = 4.23 + j * .027
        t = np.arange(round((5.92 - onset) * RATE)) / RATE
        env = (1 - np.exp(-t / .022)) * (.57 * np.exp(-t / .37) + .43 * np.exp(-t / .83))
        env *= np.clip((5.92 - onset - t) / .25, 0, 1) ** 1.6
        snd = np.sin(2 * np.pi * hz * t + .12 * np.sin(2 * np.pi * 1.35 * t))
        snd += .22 * np.sin(2 * np.pi * (hz * 2.002) * t + .18) * np.exp(-t / .38)
        snd += .076 * np.sin(2 * np.pi * (hz * 3.009) * t) * np.exp(-t / .16)
        add(onset, amp * env * snd, .052 * amp * env * np.sin(2 * np.pi * (hz * 1.002) * t), pan)
    peak = float(np.max(np.abs(mix)))
    assert peak > .08
    mix *= 10 ** (-3.6 / 20) / peak
    pcm = np.rint(mix * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as out:
        out.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        out.writeframes(pcm.tobytes())


def render(ffmpeg, intro, wav, partial, dest, tmp):
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'warning', '-nostats', '-y',
           '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{W}x{H}', '-framerate', str(FPS),
           '-i', 'pipe:0', '-i', str(wav), '-map', '0:v:0', '-map', '1:a:0',
           '-filter_threads', '2', '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p',
           '-c:v', 'libx264', '-preset', 'fast', '-crf', '16', '-threads:v', '5', '-maxrate', '12M', '-bufsize', '20M',
           '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
           '-c:a', 'aac', '-b:a', '320k', '-ar', str(RATE), '-ac', '2', '-movflags', '+faststart',
           '-metadata', 'title=CreatorHub | 创作工作台 (2.5D Motion)', str(partial)]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE, cwd=tmp) as p:
        try:
            for rgb in intro.rgb_frames():
                p.stdin.write(rgb)
            p.stdin.close()
            if p.wait():
                raise RuntimeError('FFmpeg render failed')
        except BaseException:
            p.kill()
            raise
    partial.replace(dest)


def validation(ffmpeg, ffprobe, video, tmp):
    if ffprobe:
        args = [ffprobe, '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(video)]
        meta = json.loads(subprocess.check_output(args, cwd=tmp))
        v, a = (next(s for s in meta['streams'] if s['codec_type'] == kind) for kind in ('video', 'audio'))
        assert (v['width'], v['height'], v['codec_name'], v['pix_fmt']) == (W, H, 'h264', 'yuv420p'), v
        assert v['avg_frame_rate'] == '30/1' and int(v['nb_read_frames']) == N, v
        assert (a['codec_name'], a['profile'], a['sample_rate'], a['channels']) == ('aac', 'LC', str(RATE), 2), a
        assert float(v['duration']) == float(a['duration']) == float(meta['format']['duration']) == LENGTH
    else:
        args = [ffmpeg, '-nostdin', '-hide_banner', '-xerror', '-i', str(video), '-map', '0:v:0',
                '-an', '-progress', 'pipe:1', '-f', 'null', '-']
        p = subprocess.run(args, check=True, capture_output=True, cwd=tmp)
        info, stats = p.stderr.decode(errors='replace'), p.stdout.decode()
        assert ('Video: h264' in info and 'yuv420p' in info and '1080x1920' in info and '30 fps' in info
                and 'Audio: aac (LC), 48000 Hz, stereo' in info and 'Duration: 00:00:06.00' in info)
        assert int(re.findall(r'^frame=(\d+)\s*$', stats, re.M)[-1]) == N
    args = [ffmpeg, '-nostdin', '-hide_banner', '-nostats', '-xerror', '-i', str(video), '-vn',
            '-map', '0:a:0', '-af', 'atrim=end_sample=288000,ebur128=peak=true', '-f', 'f32le', '-']
    p = subprocess.run(args, check=True, capture_output=True, cwd=tmp)
    pcm = np.frombuffer(p.stdout, dtype='<f4').reshape(-1, 2)
    diag = p.stderr.decode(errors='replace')
    assert len(pcm) == RATE * LENGTH, len(pcm)
    peak = 20 * math.log10(float(np.max(np.abs(pcm))))
    rms = 20 * math.log10(float(np.sqrt(np.mean(pcm ** 2))))
    true_peak = float(re.findall(r'Peak:\s+(-?[\d.]+) dBFS', diag)[-1])
    lufs = float(re.findall(r'I:\s+(-?[\d.]+) LUFS', diag)[-1])
    assert peak < -1 and true_peak < -1 and rms > -36
    assert all(np.max(np.abs(pcm[i * RATE:(i + 1) * RATE])) > .014 for i in (0, 1, 2, 3, 4, 5))
    return {'duration_s': LENGTH, 'fps': FPS, 'decoded_frames': N, 'dimensions': [W, H],
            'video': 'H.264/yuv420p', 'audio': 'AAC-LC stereo / 48 kHz', 'audio_samples_per_channel': len(pcm),
            'audio_peak_dbfs': round(peak, 2), 'audio_true_peak_dbtp': true_peak,
            'audio_rms_dbfs': round(rms, 2), 'audio_integrated_lufs': lufs}


def decoded_previews(ffmpeg, video, out, tmp):
    ids = [0, *(panel[0] for panel in BOARD), 150, 179]
    select = 'select=' + '+'.join(f'eq(n\\,{n})' for n in ids)
    args = [ffmpeg, '-nostdin', '-hide_banner', '-v', 'error', '-xerror', '-i', str(video), '-an',
            '-vf', select, '-fps_mode', 'vfr', '-threads:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']
    raw = subprocess.check_output(args, cwd=tmp)
    size = W * H * 3
    assert len(raw) == len(ids) * size, len(raw)
    images = {n: Image.frombytes('RGB', (W, H), raw[i * size:(i + 1) * size]) for i, n in enumerate(ids)}
    start = np.asarray(images[0])
    end = np.asarray(images[179])
    hold = np.asarray(images[150])
    assert np.mean(start) > 190 and np.mean(end) > 20 and np.mean(np.abs(end.astype('int16') - hold)) < 1.5
    for i, name in STILLS.items():
        images[i].save(out / name, compress_level=6)
    sheet = Image.new('RGB', (1458, 784), PAPER)
    d = ImageDraw.Draw(sheet)
    d.text((47, 35), TITLE, fill=INK, font=font(32, 1))
    d.text((1411, 51), 'CREATORHUB  /  STORYBOARD  /  06 SEC', fill=MUTE, font=font(19, 1), anchor='ra')
    d.line((48, 116, 1410, 116), fill=HAIR, width=1)
    for j, (n, serial, title, span) in enumerate(BOARD):
        x = 48 + j * 276
        d.text((x, 137), serial, fill=BLUE, font=font(19, 1))
        d.text((x + 252, 137), f'{n / FPS:05.2f} s', fill=MUTE, font=font(19, 1), anchor='ra')
        sheet.paste(images[n].resize((252, 448), Image.Resampling.LANCZOS), (x, 173))
        d.rectangle((x, 173, x + 251, 620), outline=(214, 209, 198))
        d.text((x, 643), title, fill=INK, font=font(31, 1))
        d.text((x, 701), span, fill=MUTE, font=font(14, 1))
    d.line((48, 750, 1410, 750), fill=HAIR, width=1)
    sheet.save(out / 'storyboard-v2.jpg', quality=96, subsampling=0, optimize=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='Output directory on G: (new or empty)')
    parser.add_argument('--assets', type=Path, default=REPO / 'app/fusion/intro_v2_assets')
    parser.add_argument('--title', default=TITLE)
    parser.add_argument('--hook', default=HOOK)
    parser.add_argument('--message', default=MESSAGE)
    parser.add_argument('--tagline', default=TAGLINE)
    parser.add_argument('--brand', default=BRAND)
    parser.add_argument('--font', type=Path, default=Path('C:/Windows/Fonts/msyhbd.ttc'))
    parser.add_argument('--ffmpeg', help='Override FFmpeg executable (default PATH, then imageio-ffmpeg)')
    parser.add_argument('--ffprobe', help='Override FFprobe executable (optional)')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.drive.upper() != 'G:':
        parser.error('--out must be on G:')
    out.mkdir(parents=True, exist_ok=True)
    if any((out / f).exists() for f in (VIDEO, 'storyboard-v2.jpg', *STILLS.values())):
        parser.error('Refusing to overwrite a prior delivery; choose a new --out')
    assets = args.assets.resolve()
    global FONT_PATH, np, Image, ImageDraw, ImageFont, ImageFilter
    FONT_PATH = args.font.resolve(strict=True)
    for key, limit in (('title', 16), ('hook', 12), ('message', 12), ('tagline', 24), ('brand', 24)):
        value = getattr(args, key).strip()
        if not value or len(value) > limit or any(not ch.isprintable() for ch in value):
            parser.error(f'{key} must contain 1-{limit} printable characters')
        globals()[key.upper()] = value
    sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(dir=out, prefix='.render-') as td:
        for name in ('TEMP', 'TMP', 'TMPDIR', 'XDG_CACHE_HOME', 'IMAGEIO_USERDIR', 'MPLCONFIGDIR', 'PYTHONPYCACHEPREFIX'):
            os.environ[name] = td
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        ffmpeg = args.ffmpeg or shutil.which('ffmpeg')
        if not ffmpeg:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        ffprobe = args.ffprobe or shutil.which('ffprobe')
        start = time.perf_counter()
        tmp = Path(td)
        wav = tmp / 'score.wav'
        video = out / VIDEO
        score(wav)
        render(ffmpeg, Intro(assets), wav, tmp / 'encode.mp4', video, tmp)
        generation = time.perf_counter() - start
        print(f'MP4 saved in {generation:.2f}s: {video}', flush=True)
        report = validation(ffmpeg, ffprobe, video, tmp)
        decoded_previews(ffmpeg, video, out, tmp)
        report.update(encode_generation_s=round(generation, 2), total_generation_s=round(time.perf_counter() - start, 2),
                      mp4_bytes=video.stat().st_size)
        report['text'] = {key: globals()[key.upper()] for key in ('title', 'hook', 'message', 'tagline', 'brand')}
        (out / 'render-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
