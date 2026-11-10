"""Overlay for the annotated video: boxes, plates, the gate line and a side panel.

Text is drawn with Pillow and the DejaVu fonts that ship with matplotlib (a
dependency of supervision), so accents (``no leída``) render on any system.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from platedemo.gate import Line

# RGB
COLORS = {
    "residente": (46, 204, 113),
    "visita": (64, 156, 255),
    "proveedor": (243, 166, 30),
    "desconocido": (235, 77, 75),
    "no leída": (150, 160, 165),
    "leyendo": (235, 235, 235),
}
PANEL_BG = (17, 20, 18)
INK = (242, 241, 234)
MUTED = (150, 156, 148)
PANEL_W = 420


@lru_cache(maxsize=None)
def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    try:
        import matplotlib

        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        name = "DejaVuSansMono" if mono else "DejaVuSans"
        name += "-Bold.ttf" if bold else ".ttf"
        return ImageFont.truetype(str(base / name), size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


@dataclass
class TrackView:
    track_id: int
    box: tuple[float, float, float, float]  # working coordinates
    plate: str  # display text, "" if not read yet
    status: str  # one of COLORS
    counted: str | None = None  # "entra"/"sale" once it crossed


@dataclass
class EventView:
    clock: str
    direction: str
    plate: str
    status: str
    note: str = ""


@dataclass
class InsideView:
    plate: str
    status: str
    since: str
    previous: bool


def _chip(d: ImageDraw.ImageDraw, xy, text: str, fill, f, pad=(8, 4), ink=(15, 15, 15)) -> int:
    x, y = xy
    w = d.textlength(text, font=f)
    h = f.size
    d.rounded_rectangle([x, y, x + w + 2 * pad[0], y + h + 2 * pad[1]], radius=6, fill=fill)
    d.text((x + pad[0], y + pad[1] - 1), text, font=f, fill=ink)
    return int(w + 2 * pad[0])


def draw_frame(frame_bgr: np.ndarray, tracks: list[TrackView], line: Line | None, events: list[EventView],
               inside: list[InsideView], counters: dict[str, int], clock: str, title: str = "",
               credit: str = "", flash: bool = False, flip: bool = False, label_min_width: float = 90) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    canvas = Image.new("RGB", (w + PANEL_W, h), PANEL_BG)
    canvas.paste(Image.fromarray(frame_bgr[:, :, ::-1]), (0, 0))
    d = ImageDraw.Draw(canvas, "RGBA")

    # gate line with direction labels
    lc = (255, 214, 0) if not flash else (255, 255, 255)
    if line is not None:
        d.line([(line.x1, line.y1), (line.x2, line.y2)], fill=lc + (230,), width=4)
        for (x, y) in ((line.x1, line.y1), (line.x2, line.y2)):
            d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=lc)
        mx, my = (line.x1 + line.x2) / 2, (line.y1 + line.y2) / 2
        nx, ny = -(line.y2 - line.y1) / line.length, (line.x2 - line.x1) / line.length  # right-hand normal
        # label each side with what crossing into it means
        f_small = font(15, bold=True)
        positive, negative = ("SALE", "ENTRA") if flip else ("ENTRA", "SALE")
        for sign, label in ((1, f"cruzar hacia acá: {positive}"), (-1, f"cruzar hacia acá: {negative}")):
            tx, ty = mx + sign * nx * 28 - 90, my + sign * ny * 28 - 9
            d.text((tx, ty), label, font=f_small, fill=lc, stroke_width=3, stroke_fill=(0, 0, 0))

    # vehicles
    f_plate = font(30, bold=True, mono=True)
    f_tag = font(15, bold=True)
    for t in tracks:
        x1, y1, x2, y2 = t.box
        col = COLORS.get(t.status, COLORS["leyendo"])
        if not t.plate and not t.counted and (x2 - x1) < label_min_width:
            d.rectangle([x1, y1, x2, y2], outline=col + (150,), width=1)  # too far to read: box only
            continue
        d.rectangle([x1, y1, x2, y2], outline=col + (255,), width=3)
        label = t.plate or "leyendo…"
        tag = f"ID {t.track_id}  {t.status}" + (f"  {t.counted}" if t.counted else "")
        pw = d.textlength(label, font=f_plate) + 20
        px = max(0, min(x1, w - pw))
        py = max(0, y1 - 74)
        d.rounded_rectangle([px, py, px + pw, py + 42], radius=6, fill=(255, 255, 255, 235), outline=col, width=4)
        d.text((px + 10, py + 4), label, font=f_plate, fill=(10, 10, 10))
        _chip(d, (px, py + 46), tag, col + (240,), f_tag, pad=(6, 3))

    if credit:
        fc = font(13)
        d.text((10, h - 22), credit, font=fc, fill=(235, 235, 235), stroke_width=2, stroke_fill=(0, 0, 0))

    # side panel
    x0 = w + 22
    y = 18
    d.text((x0, y), title or "Portería", font=font(22, bold=True), fill=INK)
    y += 30
    d.text((x0, y), f"Hora {clock}", font=font(16, mono=True), fill=MUTED)
    y += 32
    f_num, f_lab = font(26, bold=True), font(13)
    for i, (k, v) in enumerate(counters.items()):
        cx = x0 + i * 128
        d.text((cx, y), str(v), font=f_num, fill=INK)
        d.text((cx, y + 32), k, font=f_lab, fill=MUTED)
    y += 64
    d.line([(x0, y), (w + PANEL_W - 22, y)], fill=(60, 64, 60), width=1)
    y += 12
    d.text((x0, y), "Últimos eventos", font=font(16, bold=True), fill=INK)
    y += 28
    f_row, f_mono = font(15), font(17, bold=True, mono=True)
    for e in events[-6:][::-1]:
        col = COLORS.get(e.status, COLORS["leyendo"])
        d.text((x0, y + 2), e.clock[:8], font=font(13, mono=True), fill=MUTED)
        d.text((x0 + 76, y + 2), e.direction, font=font(14), fill=MUTED)
        d.text((x0 + 124, y), e.plate or "—", font=f_mono, fill=INK)
        _chip(d, (x0 + 266, y), e.status, col, font(13, bold=True), pad=(6, 2))
        y += 30
    y = max(y, 330) + 6
    d.line([(x0, y), (w + PANEL_W - 22, y)], fill=(60, 64, 60), width=1)
    y += 12
    d.text((x0, y), f"Siguen adentro ({len(inside)})", font=font(16, bold=True), fill=INK)
    y += 28
    for r in inside[:9]:
        if y > h - 40:
            break
        col = COLORS.get(r.status, COLORS["leyendo"])
        d.ellipse([x0, y + 6, x0 + 10, y + 16], fill=col)
        d.text((x0 + 18, y), r.plate or "sin patente", font=f_mono, fill=INK)
        d.text((x0 + 150, y + 2), r.status, font=f_row, fill=MUTED)
        d.text((x0 + 268, y + 2), ("desde " + r.since[:5]) + (" *" if r.previous else ""), font=font(13), fill=MUTED)
        y += 26
    if any(r.previous for r in inside):
        d.text((x0, h - 26), "* ingresó antes del inicio del video", font=font(12), fill=MUTED)
    return np.asarray(canvas)[:, :, ::-1].copy()
