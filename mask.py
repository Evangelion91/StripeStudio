"""Deterministic nonintersecting diagonal strip mask, matching web/app.js."""
import math
from pathlib import Path
from PIL import Image, ImageDraw


def numeric(value, default, low, high):
    try:
        number = float(value)
        return min(high, max(low, number)) if math.isfinite(number) else default
    except (ValueError, TypeError):
        return default


def hash_value(index, seed, channel):
    n = (int(index) * 374761393 + int(seed) * 668265263 + channel * 2246822519) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    n ^= n >> 16
    return n / 4294967295


def polygons(template, width, height, group=None):
    angle = numeric(template.get("angle"), -26, -80, 80) * math.pi / 180
    period = numeric(template.get("period"), 34, 8, 90) * min(width, height) / 1080
    share = numeric(template.get("share"), 42, 10, 90) / 100
    irregularity = numeric(template.get("irregularity"), 0, 0, 100) / 100
    seed = int(numeric(template.get("seed"), 1234, 0, 2147483647))
    length = math.hypot(width, height) * .7
    white, gap = period * share, period * (1 - share)
    jitter = min(period * .25 * irregularity, gap * .28, white * .28)
    dw = min(white * .3, gap * .18) * irregularity
    cosine, sine = math.cos(angle), math.sin(angle)
    minimum = -(abs(sine) * width + abs(cosine) * height) / 2
    first = math.floor((minimum - white) / period) + 1
    for index in range(math.floor(-length / period) - 2, math.ceil(length / period) + 3):
        if group is not None and (index - first) % 3 not in (group if isinstance(group, tuple) else (group,)):
            continue
        left = index * period + (hash_value(index, seed, 1) * 2 - 1) * jitter
        right = left
        left_width = white + (hash_value(index, seed, 3) * 2 - 1) * dw
        right_width = left_width
        yield [(width / 2 + u * cosine - v * sine, height / 2 + u * sine + v * cosine)
               for u, v in ((-length, left), (length, right), (length, right + right_width), (-length, left + left_width))]


def save_mask(template, width, height, target: Path, group=None):
    aa = 4
    image = Image.new("L", (width * aa, height * aa), 0)
    draw = ImageDraw.Draw(image)
    for points in polygons(template, width, height, group):
        draw.polygon([(round(x * aa - .5), round(y * aa - .5)) for x, y in points], fill=255)
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    image.save(target, optimize=True)
