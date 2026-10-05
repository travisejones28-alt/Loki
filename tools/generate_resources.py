"""Reproducible local icon and 350 ms notification WAV; no downloaded assets."""

import math
import struct
import wave
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    folder = Path(__file__).resolve().parents[1] / "loki" / "resources"
    folder.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((16, 16, 240, 240), radius=56, fill="#63dfa1")
    draw.rounded_rectangle((80, 64, 112, 192), radius=8, fill="#111a23")
    draw.rounded_rectangle((80, 160, 184, 192), radius=8, fill="#111a23")
    image.save(
        folder / "loki.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    image.save(folder / "loki.png")
    rate, duration = 22050, 0.35
    samples = []
    for i in range(round(rate * duration)):
        t = i / rate
        envelope = min(1.0, t / 0.018) * min(1.0, (duration - t) / 0.09)
        tone = math.sin(2 * math.pi * 740 * t) * 0.65 + math.sin(2 * math.pi * 988 * t) * 0.35
        samples.append(struct.pack("<h", round(32767 * 0.20 * envelope * tone)))
    with wave.open(str(folder / "alert.wav"), "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(b"".join(samples))


if __name__ == "__main__":
    main()
