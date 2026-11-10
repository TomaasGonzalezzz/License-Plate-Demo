"""One video in; events, crops, a summary and an annotated video out.

For each processed frame:

1. detect vehicles on the working-size frame (RF-DETR) and track them (ByteTrack);
2. for each tracked vehicle big enough to read, crop it from the full-resolution
   frame, find the plate in the crop, read it (fast-plate-ocr) and add the
   reading to that vehicle's vote;
3. check if the vehicle crossed the gate line (entra / sale);
4. a crossing becomes a final event once the vehicle leaves the view or a few
   seconds after the crossing, so readings taken after the crossing (often the
   closest, sharpest ones) still count;
5. the final plate is compared with the access lists and the register of who is
   inside is updated.

Outputs in ``out_dir``: ``events.csv``, ``readings.csv`` (every single OCR
reading), ``recortes/`` (plate and vehicle crop per event), ``resumen.txt`` and
``resumen.json`` (closing summary), ``estado_final.csv`` (who is still inside,
reusable as the starting state of the next run), ``annotated.mp4`` and ``run.json``.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from platedemo.draw import EventView, InsideView, TrackView, draw_frame
from platedemo.formats import PlateRules
from platedemo.gate import IN, OUT, CrossingDetector, Line, Register
from platedemo.matching import UNKNOWN, UNREAD, AccessLists
from platedemo.video import VideoWriter, probe, read_frames, to_h264
from platedemo.voting import PlateVoter


@dataclass
class RunConfig:
    line: str  # x1,y1,x2,y2 in working-frame pixels
    flip: bool = False
    lists: str | None = None
    inside: str | None = None
    formats: str = "ar"
    clock: str = "10:00:00"
    # models
    device: str = "cpu"
    vehicle_model: str = "rf-detr-nano-384-coco"
    vehicle_threshold: float = 0.45
    plate_model: str = "yolo-v9-t-640-license-plate-end2end"
    plate_threshold: float = 0.3
    ocr_model: str = "cct-s-v2-global-model"
    # reading
    min_vehicle_width: float = 90.0  # working pixels; smaller vehicles are too far to read
    read_zone: str | None = None  # x1,y1,x2,y2 working pixels; only read inside it
    plate_margin: float = 0.06
    min_plate_width: float = 60.0  # source pixels; smaller plates are skipped (not worth an OCR call)
    max_readings: int = 60
    stop_after_agree: int = 6  # stop once this many big readings (>= stop_width px) give exactly the voted plate
    stop_width: float = 100.0
    top_k: int = 12
    min_readings: int = 2
    min_confidence: float = 0.45
    match_distance: float = 1.0
    # crossing / events
    stabilize: bool = False  # follow small camera movements (see stabilize.py)
    margin: float = 6.0
    extend: float = 40.0
    confirm: int = 2
    confirm_distance: float = 20.0  # working pixels past the line to confirm a crossing
    jump_factor: float = 0.5  # a jump larger than this x the smaller box height (+15 px) resets a track's side
    area_jump: float = 1.6  # box area changing more than this ratio between two observations resets it too
    finalize_seconds: float = 2.5
    # tracking (trackers.ByteTrackTracker)
    track_activation_threshold: float = 0.5
    lost_track_buffer: int = 30
    minimum_consecutive_frames: int = 2
    minimum_iou_threshold: float = 0.1
    # video
    start: int = 0
    end: int | None = None
    stride: int = 2
    max_width: int = 1280
    write_video: bool = True
    h264: bool = True
    title: str = "Portería"
    credit: str = ""


@dataclass
class Event:
    index: int
    track_id: int
    frame: int
    seconds: float
    clock: str
    direction: str
    plate: str = ""
    display: str = ""
    confidence: float = 0.0
    status: str = UNREAD
    name: str = ""
    note: str = ""
    readings: int = 0
    final: bool = False
    plate_crop: str = ""
    vehicle_crop: str = ""


@dataclass
class _Track:
    voter: PlateVoter
    last_seen: int = 0
    box: tuple = (0, 0, 0, 0)
    best_weight: float = -1.0
    best_plate: np.ndarray | None = None
    best_vehicle: np.ndarray | None = None
    done_reading: bool = False
    event: Event | None = None
    prev_h: float = 0.0
    prev_area: float = 0.0


@dataclass
class RunResult:
    events: list[Event]
    summary: dict
    seconds: float
    frames: int
    timings: dict = field(default_factory=dict)


def _clock(start: str, seconds: float) -> str:
    t0 = datetime.strptime(start, "%H:%M:%S")
    return (t0 + timedelta(seconds=seconds)).strftime("%H:%M:%S")


def run(video: str | Path, out_dir: str | Path, cfg: RunConfig, models=None, progress=True) -> RunResult:
    import supervision as sv
    from trackers import ByteTrackTracker

    from platedemo.models import PlateDetector, PlateReader, VehicleDetector

    out_dir = Path(out_dir)
    crops_dir = out_dir / "recortes"
    crops_dir.mkdir(parents=True, exist_ok=True)
    info = probe(video, cfg.max_width)
    line = Line.parse(cfg.line)
    zone = [float(v) for v in cfg.read_zone.split(",")] if cfg.read_zone else None
    rules = PlateRules.from_spec(cfg.formats)
    lists = AccessLists.load(cfg.lists, cfg.match_distance)
    register = Register.load(cfg.inside, cfg.match_distance)
    if models is None:
        models = (VehicleDetector(cfg.vehicle_model, cfg.vehicle_threshold, cfg.device),
                  PlateDetector(cfg.plate_model, cfg.plate_threshold, cfg.device),
                  PlateReader(cfg.ocr_model, cfg.device))
    vehicles, plates, reader = models
    eff_fps = info.fps / cfg.stride
    tracker = ByteTrackTracker(lost_track_buffer=cfg.lost_track_buffer, frame_rate=eff_fps,
                               track_activation_threshold=cfg.track_activation_threshold,
                               minimum_consecutive_frames=cfg.minimum_consecutive_frames,
                               minimum_iou_threshold=cfg.minimum_iou_threshold)
    crossing = CrossingDetector(line, cfg.margin, cfg.flip, cfg.extend, cfg.confirm, cfg.confirm_distance)
    stab = None
    if cfg.stabilize:
        from platedemo.stabilize import Stabilizer

        stab = Stabilizer()
    tracks: dict[int, _Track] = {}
    events: list[Event] = []
    writer = None
    if cfg.write_video:
        writer = VideoWriter(out_dir / "annotated_raw.mp4", info.width + 420, info.height, eff_fps)
    readings_file = open(out_dir / "readings.csv", "w", newline="", encoding="utf-8")
    readings_csv = csv.writer(readings_file)
    readings_csv.writerow(["frame", "segundo", "id", "lectura", "corregida", "confianza_media", "ancho_patente_px"])
    tracks_file = open(out_dir / "tracks.csv", "w", newline="", encoding="utf-8")
    tracks_csv = csv.writer(tracks_file)
    tracks_csv.writerow(["frame", "segundo", "id", "x1", "y1", "x2", "y2", "salto", "cruce"])
    timings = {"vehiculos": 0.0, "patentes": 0.0, "ocr": 0.0, "dibujo": 0.0}
    finalize_frames = int(cfg.finalize_seconds * info.fps)
    lost_frames = int(1.0 * info.fps)
    inv = 1.0 / info.scale
    flash_until = -1
    frames = 0
    t_start = time.perf_counter()

    def finalize(tr: _Track, tid: int) -> None:
        ev = tr.event
        if ev is None or ev.final:
            return
        vote = tr.voter.accepted()
        clock_t = datetime.strptime(ev.clock, "%H:%M:%S").time()
        if vote:
            m = lists.match(vote.text, clock_t)
            ev.plate, ev.display, ev.confidence = vote.text, rules.display(vote.text), vote.confidence
            ev.status, ev.name, ev.note, ev.readings = m.status, m.label, m.note, vote.readings
        else:
            raw = tr.voter.result()
            ev.status, ev.readings = UNREAD, raw.readings if raw else 0
            ev.note = f"mejor intento: {raw.text} ({raw.confidence:.2f})" if raw else "no se encontró la patente"
        if ev.direction == IN:
            register.enter(ev.plate or None, tid, ev.status, ev.clock, ev.name)
        else:
            rec = register.exit(ev.plate or None, tid)
            if rec is None and ev.plate:
                ev.note = (ev.note + "; " if ev.note else "") + "salida sin ingreso registrado"
        stem = f"evento_{ev.index:02d}_{ev.plate or 'sin_lectura'}"
        if tr.best_plate is not None:
            ev.plate_crop = f"recortes/{stem}_patente.jpg"
            cv2.imwrite(str(out_dir / ev.plate_crop), tr.best_plate)
        if tr.best_vehicle is not None:
            ev.vehicle_crop = f"recortes/{stem}_vehiculo.jpg"
            cv2.imwrite(str(out_dir / ev.vehicle_crop), tr.best_vehicle)
        ev.final = True

    try:
        for index, full, small in read_frames(video, info, cfg.start, cfg.end, cfg.stride):
            seconds = index / info.fps
            clock = _clock(cfg.clock, seconds)
            t0 = time.perf_counter()
            boxes = vehicles(small)
            timings["vehiculos"] += time.perf_counter() - t0
            if stab is not None:
                t0 = time.perf_counter()
                stab.update(small, [(b.x1, b.y1, b.x2, b.y2) for b in boxes])
                timings["estabilizacion"] = timings.get("estabilizacion", 0.0) + time.perf_counter() - t0
            if boxes:
                det = sv.Detections(xyxy=np.array([[b.x1, b.y1, b.x2, b.y2] for b in boxes], dtype=float),
                                    confidence=np.array([b.confidence for b in boxes]),
                                    class_id=np.zeros(len(boxes), dtype=int),
                                    data={"class_name": np.array([b.label for b in boxes])})
            else:
                det = sv.Detections.empty()
            tracked = tracker.update(det)
            if len(tracked):
                tracked = tracked[tracked.tracker_id >= 0]
            views: list[TrackView] = []
            for tid, box in zip(tracked.tracker_id if len(tracked) else [], tracked.xyxy if len(tracked) else []):
                tid = int(tid)
                tr = tracks.setdefault(tid, _Track(PlateVoter(rules, cfg.min_readings, cfg.min_confidence, top_k=cfg.top_k)))
                tr.last_seen, tr.box = index, tuple(float(v) for v in box)
                x1, y1, x2, y2 = tr.box
                anchor = ((x1 + x2) / 2, y2)
                # read the plate
                in_zone = zone is None or (zone[0] <= anchor[0] <= zone[2] and zone[1] <= anchor[1] <= zone[3])
                if not tr.done_reading and (x2 - x1) >= cfg.min_vehicle_width and in_zone:
                    sx1, sy1 = int(max(0, x1 * inv)), int(max(0, y1 * inv))
                    sx2, sy2 = int(min(info.source_width, x2 * inv)), int(min(info.source_height, y2 * inv))
                    vcrop = full[sy1:sy2, sx1:sx2]
                    if vcrop.size:
                        t0 = time.perf_counter()
                        found = plates(vcrop)
                        timings["patentes"] += time.perf_counter() - t0
                        # prefer the most confident plate in the lower 3/4 of the vehicle
                        found = [p for p in found if (p.y1 + p.y2) / 2 > 0.25 * vcrop.shape[0]] or found
                        if found:
                            p = max(found, key=lambda b: b.confidence)
                            mw, mh = cfg.plate_margin * (p.x2 - p.x1), cfg.plate_margin * (p.y2 - p.y1)
                            px1, py1 = int(max(0, p.x1 - mw)), int(max(0, p.y1 - mh))
                            px2, py2 = int(min(vcrop.shape[1], p.x2 + mw)), int(min(vcrop.shape[0], p.y2 + mh))
                            pcrop = vcrop[py1:py2, px1:px2]
                            if pcrop.size and (p.x2 - p.x1) >= cfg.min_plate_width:
                                t0 = time.perf_counter()
                                text, probs = reader(pcrop)
                                timings["ocr"] += time.perf_counter() - t0
                                pw = float(p.x2 - p.x1)
                                tr.voter.add(text, probs, pw, index)
                                fixed, _ = rules.validate(text)
                                readings_csv.writerow([index, f"{seconds:.2f}", tid, text, fixed or "",
                                                       f"{float(np.mean(probs)) if len(probs) else 0:.3f}", int(pw)])
                                weight = (float(np.mean(probs)) if len(probs) else 0) * min(1.0, pw / 120)
                                if weight > tr.best_weight:
                                    tr.best_weight = weight
                                    tr.best_plate = pcrop.copy()
                                    vb = vcrop
                                    if vb.shape[1] > 900:
                                        vb = cv2.resize(vb, (900, int(900 * vb.shape[0] / vb.shape[1])))
                                    tr.best_vehicle = vb.copy()
                                if len(tr.voter.readings) >= cfg.max_readings:
                                    tr.done_reading = True
                                else:
                                    v = tr.voter.accepted()
                                    if v:
                                        big = sum(1 for r in tr.voter.readings if r.plate_width >= cfg.stop_width
                                                  and rules.validate(r.text)[0] == v.text)
                                        if big >= cfg.stop_after_agree:
                                            tr.done_reading = True  # stable reading: save CPU for other vehicles
                # crossing
                h, area = y2 - y1, (x2 - x1) * (y2 - y1)
                prev_h, prev_area = tr.prev_h or h, tr.prev_area or area
                # boxes that merge (one vehicle passing next to another) or a trailer that comes and
                # goes change the area abruptly: forget the side instead of counting a crossing
                abrupt = max(area, prev_area) / max(1.0, min(area, prev_area)) > cfg.area_jump
                ref_anchor = stab.to_ref(anchor) if stab is not None else anchor
                direction = crossing.update(tid, ref_anchor, jump_limit=cfg.jump_factor * min(h, prev_h) + 15, reset=abrupt)
                tr.prev_h, tr.prev_area = h, area
                tracks_csv.writerow([index, f"{seconds:.2f}", tid, *(f"{v:.1f}" for v in tr.box),
                                     int(abrupt), direction or ""])
                if direction:
                    ev = Event(len(events) + 1, tid, index, round(seconds, 2), clock, direction)
                    events.append(ev)
                    tr.event = ev
                    flash_until = index + int(0.4 * info.fps)
                if tr.event is not None and not tr.event.final and index - tr.event.frame >= finalize_frames:
                    finalize(tr, tid)
                vote = tr.voter.accepted()
                if tr.event is not None and tr.event.final:
                    status, plate_txt = tr.event.status, tr.event.display
                elif vote:
                    status = lists.match(vote.text, datetime.strptime(clock, "%H:%M:%S").time()).status
                    plate_txt = rules.display(vote.text)
                else:
                    status, plate_txt = "leyendo", ""
                views.append(TrackView(tid, tr.box, plate_txt, status, tr.event.direction if tr.event else None))
            # vehicles that left the view: finalize their crossing
            for tid, tr in tracks.items():
                if tr.event is not None and not tr.event.final and index - tr.last_seen > lost_frames:
                    finalize(tr, tid)
            if writer:
                t0 = time.perf_counter()
                ev_views = [EventView(e.clock, e.direction, e.display or ("leyendo…" if not e.final else ""),
                                      e.status if e.final else "leyendo", e.note) for e in events]
                inside = [InsideView(rec.plate and rules.display(rec.plate) or "", rec.status, rec.since, rec.previous)
                          for rec in register.still_inside()]
                counters = {"ingresos": sum(e.direction == IN for e in events),
                            "egresos": sum(e.direction == OUT for e in events),
                            "alertas": sum(e.final and e.status in (UNKNOWN, UNREAD) for e in events)}
                shown = line
                if stab is not None:
                    a, b = stab.from_ref((line.x1, line.y1)), stab.from_ref((line.x2, line.y2))
                    shown = Line(a[0], a[1], b[0], b[1])
                img = draw_frame(small, views, shown, ev_views, inside, counters, clock, cfg.title, cfg.credit,
                                 flash=index <= flash_until, flip=cfg.flip, label_min_width=cfg.min_vehicle_width)
                writer.write(img)
                timings["dibujo"] += time.perf_counter() - t0
            frames += 1
            if progress and frames % 25 == 0:
                el = time.perf_counter() - t_start
                print(f"  frame {index} ({seconds:.1f} s)  {frames / el:.2f} cuadros/s  eventos: {len(events)}", flush=True)
        for tid, tr in tracks.items():
            finalize(tr, tid)
    finally:
        readings_file.close()
        tracks_file.close()
        if writer:
            writer.close()
    wall = time.perf_counter() - t_start
    if writer and cfg.h264:
        if to_h264(out_dir / "annotated_raw.mp4", out_dir / "annotated.mp4", crf=24):
            (out_dir / "annotated_raw.mp4").unlink()
        else:
            (out_dir / "annotated_raw.mp4").rename(out_dir / "annotated.mp4")
    elif writer:
        (out_dir / "annotated_raw.mp4").rename(out_dir / "annotated.mp4")

    summary = _summary(events, register, rules)
    if stab is not None:
        timings["_estabilizacion_ok"] = stab.ok_frames
        timings["_estabilizacion_fallida"] = stab.failed_frames
    _write_outputs(out_dir, cfg, events, register, summary, frames, wall, timings, info, models)
    return RunResult(events, summary, wall, frames, timings)


def _summary(events: list[Event], register: Register, rules: PlateRules) -> dict:
    by_status: dict[str, int] = {}
    for e in events:
        by_status[e.status] = by_status.get(e.status, 0) + 1
    return {
        "ingresos": sum(e.direction == IN for e in events),
        "egresos": sum(e.direction == OUT for e in events),
        "por_estado": by_status,
        "desconocidos": [rules.display(e.plate) for e in events if e.status == UNKNOWN],
        "no_leidas": sum(e.status == UNREAD for e in events),
        "siguen_adentro": [{"patente": rules.display(r.plate) if r.plate else r.key, "estado": r.status,
                            "desde": r.since, "nombre": r.label, "de_antes": r.previous}
                           for r in register.still_inside()],
        "salidas_sin_ingreso": [rules.display(p) if not p.startswith("#") else p for p in register.exits_without_entry],
    }


def _write_outputs(out_dir, cfg, events, register, summary, frames, wall, timings, info, models) -> None:
    with open(out_dir / "events.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hora", "segundo_video", "id", "patente", "confianza", "estado", "sentido", "nombre", "nota",
                    "lecturas", "recorte_patente", "recorte_vehiculo"])
        for e in events:
            w.writerow([e.clock, e.seconds, e.track_id, e.display, f"{e.confidence:.2f}" if e.plate else "", e.status,
                        e.direction, e.name, e.note, e.readings, e.plate_crop, e.vehicle_crop])
    register.save(out_dir / "estado_final.csv")
    (out_dir / "resumen.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["Resumen al cierre", "=================",
             f"Ingresos: {summary['ingresos']}", f"Egresos: {summary['egresos']}",
             "Por estado: " + ", ".join(f"{k} {v}" for k, v in summary["por_estado"].items()),
             f"Desconocidos: {', '.join(summary['desconocidos']) or 'ninguno'}",
             f"Sin patente leída: {summary['no_leidas']}", "",
             f"Siguen adentro ({len(summary['siguen_adentro'])}):"]
    for r in summary["siguen_adentro"]:
        lines.append(f"  {r['patente']:<10} {r['estado']:<12} desde {r['desde']}" + ("  (ingresó antes del video)" if r["de_antes"] else ""))
    if summary["salidas_sin_ingreso"]:
        lines += ["", "Salidas sin ingreso registrado: " + ", ".join(summary["salidas_sin_ingreso"])]
    (out_dir / "resumen.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    vehicles, plates, reader = models
    run_info = {
        "config": asdict(cfg),
        "source": {"width": info.source_width, "height": info.source_height, "fps": info.fps},
        "frames_processed": frames,
        "wall_seconds": round(wall, 1),
        "processed_frames_per_second": round(frames / wall, 2) if wall else 0,
        "video_seconds_per_wall_second": round(frames * cfg.stride / info.fps / wall, 3) if wall else 0,
        "ms_per_frame": {k: round(1000 * v / max(1, frames), 1) for k, v in timings.items() if not k.startswith("_")},
        "stabilizer_frames": {k[1:]: v for k, v in timings.items() if k.startswith("_")},
        "providers": {"vehiculos": vehicles.providers, "patentes": plates.providers},
        "events": len(events),
    }
    (out_dir / "run.json").write_text(json.dumps(run_info, indent=2), encoding="utf-8")
