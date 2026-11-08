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


def polygons(template, width, height, origin=(0, 0), padding=0):
    angle = numeric(template.get("angle"), -26, -80, 80) * math.pi / 180
    period = numeric(template.get("period"), 34, 8, 90) * min(width, height) / 1080
    share = numeric(template.get("share"), 42, 10, 90) / 100
    irregularity = numeric(template.get("irregularity"), 0, 0, 100) / 100
    seed = int(numeric(template.get("seed"), 1234, 0, 2147483647))
    length = math.hypot(width, height) * .7 + padding
    white, gap = period * share, period * (1 - share)
    jitter = min(period * .25 * irregularity, gap * .28, white * .28)
    dw = min(white * .3, gap * .18) * irregularity
    cosine, sine = math.cos(angle), math.sin(angle)
    for index in range(math.floor(-length / period) - 2, math.ceil(length / period) + 3):
        left = index * period + (hash_value(index, seed, 1) * 2 - 1) * jitter
        right = left
        left_width = white + (hash_value(index, seed, 3) * 2 - 1) * dw
        right_width = left_width
        yield [(origin[0] + width / 2 + u * cosine - v * sine, origin[1] + height / 2 + u * sine + v * cosine)
               for u, v in ((-length, left), (length, right), (length, right + right_width), (-length, left + left_width))]


def save_mask(template, width, height, target: Path):
    aa = 4
    image = Image.new("L", (width * aa, height * aa), 0)
    draw = ImageDraw.Draw(image)
    for points in polygons(template, width, height):
        draw.polygon([(round(x * aa - .5), round(y * aa - .5)) for x, y in points], fill=255)
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    image.save(target, optimize=True)


def motion_alpha(template, width, height, cfg, target):
    """Static atlas sampled by a moving crop; neither photo nor video is translated."""
    angle = math.radians(float(template['angle']))
    speed = cfg['stripeSpeed'] * min(width, height) / 1080
    dx, dy = -math.sin(angle) * speed, math.cos(angle) * speed
    end = cfg['stripeTime'] + cfg['duration']
    span_x, span_y = math.ceil(abs(dx) * end), math.ceil(abs(dy) * end)
    ox, oy = (span_x if dx > 0 else 0) + 4, (span_y if dy > 0 else 0) + 4
    size = (width + span_x + 8, height + span_y + 8)
    if size[0] * size[1] > 24_000_000:
        raise ValueError('Слишком большое смещение полос: уменьшите скорость или длительность.')
    aa = 2
    image = Image.new('L', (size[0] * aa, size[1] * aa), 0)
    draw = ImageDraw.Draw(image)
    for points in polygons(template, width, height, (ox, oy), span_x + span_y + 8):
        draw.polygon([(round(x*aa-.5), round(y*aa-.5)) for x,y in points], fill=255)
    image = image.resize(size, Image.Resampling.LANCZOS)
    image.point(lambda x: round(255 - x * cfg['videoOpacity'])).save(target)
    return ox, oy, dx, dy
