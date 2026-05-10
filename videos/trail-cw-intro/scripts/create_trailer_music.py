import math
import random
import struct
import wave
from pathlib import Path


SAMPLE_RATE = 48_000
DURATION = 40.0
BPM = 126
BEAT = 60.0 / BPM
ROOTS = [46.249, 55.0, 61.735, 69.296]
ARP = [184.997, 220.0, 277.183, 329.628, 369.994, 440.0, 554.365, 659.255]


def env_exp(x: float, decay: float) -> float:
    return math.exp(-x * decay)


def sine(freq: float, t: float) -> float:
    return math.sin(2.0 * math.pi * freq * t)


def soft_clip(x: float) -> float:
    return math.tanh(x * 1.15)


def add_kick(buf, start, gain):
    length = int(SAMPLE_RATE * 0.48)
    base = int(start * SAMPLE_RATE)
    for i in range(length):
        idx = base + i
        if idx >= len(buf):
            break
        t = i / SAMPLE_RATE
        freq = 42 + 88 * env_exp(t, 9.0)
        amp = env_exp(t, 7.5) * gain
        click = env_exp(t, 70.0) * 0.22
        v = (sine(freq, t) + click) * amp
        buf[idx] += v


def add_snare(buf, start, gain, rng):
    length = int(SAMPLE_RATE * 0.30)
    base = int(start * SAMPLE_RATE)
    last = 0.0
    for i in range(length):
        idx = base + i
        if idx >= len(buf):
            break
        t = i / SAMPLE_RATE
        noise = rng.uniform(-1.0, 1.0)
        last = last * 0.35 + noise * 0.65
        tone = sine(185, t) * 0.28
        amp = env_exp(t, 10.5) * gain
        buf[idx] += (last * 0.72 + tone) * amp


def add_hat(buf, start, gain, rng):
    length = int(SAMPLE_RATE * 0.075)
    base = int(start * SAMPLE_RATE)
    prev = 0.0
    for i in range(length):
        idx = base + i
        if idx >= len(buf):
            break
        t = i / SAMPLE_RATE
        noise = rng.uniform(-1.0, 1.0)
        hp = noise - prev * 0.62
        prev = noise
        buf[idx] += hp * env_exp(t, 42.0) * gain


def add_pluck(buf, start, freq, gain, pan=0.0):
    length = int(SAMPLE_RATE * 0.62)
    base = int(start * SAMPLE_RATE)
    for i in range(length):
        idx = base + i
        if idx >= len(buf):
            break
        t = i / SAMPLE_RATE
        amp = env_exp(t, 8.0) * gain
        wave_a = sine(freq, t)
        wave_b = sine(freq * 2.01, t) * 0.35
        v = (wave_a + wave_b) * amp
        buf[idx] += v


def add_bass(buf, start, freq, dur, gain):
    length = int(SAMPLE_RATE * dur)
    base = int(start * SAMPLE_RATE)
    for i in range(length):
        idx = base + i
        if idx >= len(buf):
            break
        t = i / SAMPLE_RATE
        local = i / max(1, length)
        amp = min(1.0, local * 28.0) * env_exp(local, 0.95) * gain
        v = sine(freq, t) * 0.88 + sine(freq * 2, t) * 0.16 + sine(freq * 0.5, t) * 0.30
        buf[idx] += v * amp


def add_pad(buf, start, dur, notes, gain):
    length = int(SAMPLE_RATE * dur)
    base = int(start * SAMPLE_RATE)
    for i in range(length):
        idx = base + i
        if idx >= len(buf):
            break
        t = i / SAMPLE_RATE
        local = i / max(1, length)
        fade = min(1.0, local * 8.0) * min(1.0, (1.0 - local) * 8.0)
        v = 0.0
        for n in notes:
            v += sine(n, t) * 0.25 + sine(n * 1.005, t) * 0.16
        buf[idx] += v * fade * gain


def add_riser(buf, start, dur, gain, rng):
    length = int(SAMPLE_RATE * dur)
    base = int(start * SAMPLE_RATE)
    for i in range(length):
        idx = base + i
        if idx >= len(buf):
            break
        t = i / SAMPLE_RATE
        local = i / max(1, length)
        freq = 180 + 1200 * (local ** 1.45)
        noise = rng.uniform(-1.0, 1.0) * local
        amp = (local ** 1.2) * gain
        buf[idx] += (sine(freq, t) * 0.42 + noise * 0.20) * amp


def build():
    rng = random.Random(42)
    frames = int(SAMPLE_RATE * DURATION)
    mono = [0.0] * frames

    add_pad(mono, 0, 15.8, [46.249, 69.296, 92.499, 138.591], 0.18)
    add_pad(mono, 16, 16, [55.0, 82.407, 110.0, 164.814], 0.16)
    add_pad(mono, 28, 12, [46.249, 69.296, 92.499, 184.997], 0.12)

    total_beats = int(DURATION / BEAT) + 1
    for b in range(total_beats):
        t = b * BEAT
        section = 0.55 if t < 8 else 0.78 if t < 16 else 1.0 if t < 31.5 else 0.7
        if t >= 2:
            add_kick(mono, t, 0.74 * section)
        if b % 4 in (2,) and t >= 8:
            add_snare(mono, t, 0.38 * section, rng)
        if t >= 8:
            add_hat(mono, t + BEAT * 0.5, 0.10 * section, rng)
        if t >= 16:
            add_hat(mono, t + BEAT * 0.25, 0.07 * section, rng)
            add_hat(mono, t + BEAT * 0.75, 0.07 * section, rng)

    for b in range(0, total_beats, 2):
        t = b * BEAT
        if t >= 4:
            freq = ROOTS[(b // 8) % len(ROOTS)]
            add_bass(mono, t, freq, BEAT * 1.8, 0.26 if t < 16 else 0.36)

    for s in [8, 16, 24, 31.5]:
        add_riser(mono, s - 1.65, 1.55, 0.34, rng)

    for step in range(int(DURATION / (BEAT / 2))):
        t = step * BEAT / 2
        if 8 <= t < 32:
            note = ARP[(step + (2 if t >= 16 else 0)) % len(ARP)]
            gain = 0.12 if t < 16 else 0.18
            add_pluck(mono, t, note, gain)

    peak = max(abs(x) for x in mono) or 1.0
    scale = 0.86 / peak
    out_path = Path(__file__).resolve().parents[1] / "assets" / "audio" / "trail-cw-trailer.wav"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        for i, sample in enumerate(mono):
            t = i / SAMPLE_RATE
            width = 0.14 * sine(0.17, t)
            left = soft_clip(sample * scale * (1.0 - width))
            right = soft_clip(sample * scale * (1.0 + width))
            wf.writeframes(struct.pack("<hh", int(left * 32767), int(right * 32767)))
    print(out_path)


if __name__ == "__main__":
    build()
