"""Write a short 16 kHz WAV for the CI smoke test.

A tone is enough: the point is to make the frozen build load its model and its
VAD asset, which is what was missing in v1.0.1.
"""
import math
import struct
import sys
import wave

OUT = sys.argv[1] if len(sys.argv) > 1 else "ci-tone.wav"
RATE, SECONDS, FREQ = 16_000, 2, 180

with wave.open(OUT, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * FREQ * t / RATE)))
        for t in range(RATE * SECONDS)
    ))
print(f"wrote {OUT}")
