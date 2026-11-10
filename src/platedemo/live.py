"""Live mode: a gate camera processed in real time.

Differences with the offline pipeline (pipeline.py), all chosen for speed:

* **Real-time source.** A reader thread decodes the stream and keeps only the
  newest frame. When the processing loop is free it takes that frame; frames that
  arrive meanwhile are dropped, exactly as with a real camera. A video file can be
  played at its real speed (``--realtime``) to test this honestly.
* **No vehicle detector.** The plate detector runs directly on a zone near the
  gate (``--read-zone``), on the full-resolution frame. That removes the most
  expensive model (about 0.5 to 1 s per frame on a laptop CPU).
* **Motion gate.** If nothing moves in the zone and no plate is being followed,
  the frame is skipped after a 1 ms check.
* **Plates are tracked, not vehicles.** Each plate box is linked to the closest
  one in the previous frames; its readings go to the same vote, and its centre
  crossing the gate line is the entry or exit.
* **Events are written the moment they happen**: printed, appended to
  ``events.csv`` and, for unknown or unread vehicles, to ``alertas.csv``.

Limitation: a vehicle whose plate is never found (no plate, covered, too dark)
produces no event in this mode; the offline pipeline, with the vehicle detector,
reports those as "no leída".
"""

from __future__ import annotations

import csv
import json
import queue
import threading
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
from platedemo.video import VideoWriter, to_h264
from platedemo.voting import PlateVoter


# ─────────────────────────────── source ───────────────────────────────

class LiveSource:
    """Decode a file or stream in a thread; ``latest()`` returns the newest frame only.

    For files with ``realtime=True`` frames are released at the video's own speed,
    and decoding that falls behind skips frames (``grab`` without converting), as a
    camera would. Without ``realtime`` every frame is delivered (useful for tests).
    """

    def __init__(self, source: str, realtime: bool = True, fps: float | None = None):
        self.source = source
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise FileNotFoundError(f"cannot open {source}")
        self.fps = fps or self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.is_stream = "://" in source
        self.realtime = realtime or self.is_stream
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self.decoded = 0
        self.skipped_decode = 0
        self.finished = False
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self) -> LiveSource:
        self.t0 = time.perf_counter()
        self._t.start()
        return self

    def _put(self, item) -> None:
        if not self.realtime:
            self._q.put(item)  # block: deliver every frame
            return
        try:
            self._q.get_nowait()  # drop the frame nobody took
        except queue.Empty:
            pass
        self._q.put(item)

    def _run(self) -> None:
        index = 0
        while not self._stop.is_set():
            if self.realtime and not self.is_stream:
                due = self.t0 + index / self.fps
                now = time.perf_counter()
                if now < due:
                    time.sleep(due - now)
                elif now - due > 1.5 / self.fps:
                    # behind schedule: skip this frame without the colour conversion
                    if not self.cap.grab():
                        break
                    self.skipped_decode += 1
                    index += 1
                    continue
            ok, frame = self.cap.read()
            if not ok:
                break
            self.decoded += 1
            self._put((index, index / self.fps, time.perf_counter(), frame))
            index += 1
        self.finished = True
        self._put(None)

    def latest(self, timeout: float = 5.0):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None if self.finished else "wait"

    def stop(self) -> None:
        self._stop.set()
        self.cap.release()


# ─────────────────────────────── tracking ───────────────────────────────

@dataclass
class PlateTrack:
    tid: int
    box: tuple  # source pixels
    voter: PlateVoter
    first_t: float
    last_t: float
    hits: int = 1
    event: dict | None = None
    best_w: float = -1.0
    best_crop: np.ndarray | None = None
    first_centre: tuple[float, float] | None = None
    travel: float = 0.0  # farthest the plate centre got from where it was first seen (source px)
    done: bool = False  # its event is final with a plate: no need to keep it alive through gaps

    @property
    def centre(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.box
        return (x1 + x2) / 2, (y1 + y2) / 2


class PlateTracker:
    """Greedy association of plate boxes by centre distance, scaled by plate width."""

    def __init__(self, max_age: float = 1.5, reach: float = 3.0, max_age_done: float = 1.5):
        self.max_age = max_age
        # a plate already decided is dropped sooner: the next car stopping on the same spot must not
        # inherit its track
        self.max_age_done = min(max_age, max_age_done)
        self.reach = reach
        self.tracks: dict[int, PlateTrack] = {}
        self._next = 1

    def update(self, boxes: list[tuple], t: float, make_voter) -> list[tuple[PlateTrack, tuple]]:
        pairs, used = [], set()
        for b in sorted(boxes, key=lambda b: -(b[2] - b[0])):
            cx, cy, w = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b[2] - b[0]
            best, best_d = None, None
            for tr in self.tracks.values():
                if tr.tid in used:
                    continue
                tx, ty = tr.centre
                d = float(np.hypot(cx - tx, cy - ty))
                # the reach grows a little after a gap; a plate already decided does not stretch (a car
                # that appears elsewhere a moment later is another car)
                grow = 1.0 if tr.done else min(2.0, max(1.0, (t - tr.last_t) * 4))
                limit = self.reach * max(w, tr.box[2] - tr.box[0]) * grow
                if d <= limit and (best_d is None or d < best_d):
                    best, best_d = tr, d
            if best is None:
                best = PlateTrack(self._next, b, make_voter(), t, t)
                self.tracks[best.tid] = best
                self._next += 1
            else:
                best.box, best.last_t, best.hits = b, t, best.hits + 1
            used.add(best.tid)
            pairs.append((best, b))
        return pairs

    def expired(self, t: float) -> list[PlateTrack]:
        gone = [tr for tr in self.tracks.values() if t - tr.last_t > (self.max_age_done if tr.done else self.max_age)]
        for tr in gone:
            del self.tracks[tr.tid]
        return gone


# ─────────────────────────────── run ───────────────────────────────

@dataclass
class LiveConfig:
    line: str | None = None  # x1,y1,x2,y2 in working pixels (frame scaled to work_width); needed for trigger="line"
    read_zone: str | None = None  # x1,y1,x2,y2 in working pixels; default: whole frame
    # "line": a vehicle is logged when its plate crosses the line (direction from the side).
    # "zone": a vehicle is logged when its plate is read inside the zone; one lane = one direction,
    #         as at a barrier where cars stop instead of crossing a line.
    trigger: str = "line"
    lane_direction: str = IN
    flip: bool = False
    lists: str | None = None
    inside: str | None = None
    formats: str = "ar"
    clock: str | None = None  # HH:MM:SS of the video start; default: the computer's clock
    realtime: bool = True
    device: str = "cpu"
    plate_model: str = "yolo-v9-t-640-license-plate-end2end"
    plate_threshold: float = 0.3
    ocr_model: str = "cct-s-v2-global-model"
    work_width: int = 1280
    min_plate_width: float = 40.0  # source pixels
    motion_threshold: float = 2.5  # mean grey difference (0-255) that counts as movement
    motion_fraction: float = 0.004  # ...or this share of the zone changed clearly (a small, far vehicle)
    idle_check: float = 0.5  # seconds: look for plates at least this often, even with no movement
    cut_threshold: float = 12.0  # a difference this large is a scene cut or a moved camera (0 = never)
    # seconds a plate track survives without a detection. Default: 1.5 with a line, 3 with a zone (a car
    # waiting at a barrier is often missed for a moment; picking it up again avoids logging it twice)
    max_age: float | None = None
    max_readings: int = 20  # stop reading a plate after this many readings
    dedupe_seconds: float = 20.0  # same plate, same direction within this window = the same passage
    min_readings: int = 2
    min_confidence: float = 0.45
    match_distance: float = 1.0
    margin: float = 6.0
    confirm: int = 2
    # working px a plate must move before it counts, so parked cars never do. Default: 40 with a line,
    # 0 with a zone (at a barrier the zone is drawn over the lane only, and a car may already be stopped
    # when it is first seen).
    min_travel: float | None = None
    finalize_agree: int = 4  # close an event early when this many readings agree...
    finalize_confidence: float = 0.75  # ...with at least this confidence; otherwise wait until the plate leaves
    stabilize: bool = False
    max_seconds: float | None = None
    show: bool = False
    save_video: bool = False
    title: str = "Portería (en vivo)"
    credit: str = ""


def run_live(source: str, out_dir: str | Path, cfg: LiveConfig, verbose: bool = True) -> dict:
    from platedemo.models import PlateDetector, PlateReader

    out = Path(out_dir)
    (out / "recortes").mkdir(parents=True, exist_ok=True)
    src = LiveSource(source, realtime=cfg.realtime)
    scale = cfg.work_width / src.width  # working px per source px
    wh = int(round(src.height * scale))
    if cfg.trigger not in ("line", "zone"):
        raise ValueError("trigger must be 'line' or 'zone'")
    if cfg.trigger == "line" and not cfg.line:
        raise ValueError("trigger 'line' needs --line")
    if cfg.min_travel is None:
        cfg.min_travel = 40.0 if cfg.trigger == "line" else 0.0
    line = Line.parse(cfg.line) if cfg.line else None
    line_src = line.scaled(1 / scale) if line else Line(0, 0, 1, 0)
    zone = [float(v) / scale for v in cfg.read_zone.split(",")] if cfg.read_zone else [0, 0, src.width, src.height]
    zx1, zy1, zx2, zy2 = (int(max(0, zone[0])), int(max(0, zone[1])), int(min(src.width, zone[2])), int(min(src.height, zone[3])))
    rules = PlateRules.from_spec(cfg.formats)
    lists = AccessLists.load(cfg.lists, cfg.match_distance)
    register = Register.load(cfg.inside, cfg.match_distance)
    plates = PlateDetector(cfg.plate_model, cfg.plate_threshold, cfg.device)
    reader = PlateReader(cfg.ocr_model, cfg.device)
    crossing = CrossingDetector(line_src, cfg.margin / scale, cfg.flip, extend=40 / scale, confirm=cfg.confirm)
    if cfg.max_age is None:
        cfg.max_age = 1.5 if cfg.trigger == "line" else 3.0
    tracker = PlateTracker(cfg.max_age)
    make_voter = lambda: PlateVoter(rules, cfg.min_readings, cfg.min_confidence)  # noqa: E731
    clock0 = datetime.strptime(cfg.clock, "%H:%M:%S") if cfg.clock else datetime.now()
    stab = None
    if cfg.stabilize:
        from platedemo.stabilize import Stabilizer

        stab = Stabilizer(work_width=480)

    ev_f = open(out / "events.csv", "w", newline="", encoding="utf-8")
    ev_w = csv.writer(ev_f)
    ev_w.writerow(["hora", "segundo_video", "id", "patente", "confianza", "estado", "sentido", "nombre", "nota",
                   "lecturas", "demora_s", "recorte_patente"])
    al_f = open(out / "alertas.csv", "w", newline="", encoding="utf-8")
    al_w = csv.writer(al_f)
    al_w.writerow(["hora", "patente", "estado", "sentido", "nota"])
    tk_f = open(out / "plates.csv", "w", newline="", encoding="utf-8")
    tk_w = csv.writer(tk_f)
    tk_w.writerow(["segundo", "id", "x1", "y1", "x2", "y2", "cx_ref", "cy_ref", "recorrido", "lectura", "cruce"])
    snapshots: list[dict] = []
    zr = [c * scale for c in (zx1, zy1, zx2, zy2)]

    def render(frame_src: np.ndarray, snap: dict) -> np.ndarray:
        small = cv2.resize(frame_src, (cfg.work_width, wh), interpolation=cv2.INTER_AREA)
        colour = (0, 214, 255) if cfg.trigger == "zone" else (120, 120, 120)
        cv2.rectangle(small, (int(zr[0]), int(zr[1])), (int(zr[2]) - 1, int(zr[3]) - 1), colour, 2 if cfg.trigger == "zone" else 1)
        return draw_frame(small, snap["views"], line, snap["events"], snap["inside"], snap["counters"],
                          f"{snap['clock']} · {snap['fps']:.1f} cuadros/s", cfg.title, cfg.credit, flip=cfg.flip,
                          label_min_width=0)

    events: list[dict] = []
    stats = {"processed": 0, "idle": 0, "detector_ms": [], "frame_ms": [], "lag_s": []}
    prev_grey = None
    last_check = -1e9
    t_start = time.perf_counter()

    def clock_at(video_s: float) -> str:
        return (clock0 + timedelta(seconds=video_s)).strftime("%H:%M:%S")

    numbering = iter(range(1, 1_000_000))

    def open_event(tr: PlateTrack, video_s: float, direction: str) -> None:
        tr.event = {"n": next(numbering), "hora": clock_at(video_s), "segundo_video": round(video_s, 2),
                    "sentido": direction, "estado": "leyendo", "final": False, "box": tr.box, "tid": tr.tid}
        events.append(tr.event)

    pending: list[tuple[float, PlateTrack]] = []

    def close_unread(tr: PlateTrack, video_s: float) -> None:
        """Zone mode: a plate seen at the gate that was never read firmly is logged as unread, unless the
        same car is read a moment later under another track (the tracker lost it for an instant). So the
        decision waits max_age seconds."""
        if cfg.trigger == "zone" and tr.event is None and len(tr.voter.readings) >= cfg.min_readings \
                and tr.travel >= cfg.min_travel / scale:
            pending.append((video_s + cfg.max_age, tr))

    def settle_unread(video_s: float, force: bool = False) -> None:
        for item in [p for p in pending if force or p[0] <= video_s]:
            pending.remove(item)
            log_unread(item[1], video_s)

    def log_unread(tr: PlateTrack, video_s: float) -> None:
        w = tr.box[2] - tr.box[0]
        for ev in events:
            if ev.get("tid") == tr.tid or ev.get("estado") == UNREAD:
                continue
            if tr.first_t - 2 <= ev["segundo_video"] <= tr.last_t + cfg.max_age:
                bx = ev["box"]
                d = np.hypot((bx[0] + bx[2]) / 2 - tr.centre[0], (bx[1] + bx[3]) / 2 - tr.centre[1])
                if d <= 6 * max(w, bx[2] - bx[0]):
                    return
        open_event(tr, tr.first_t, cfg.lane_direction)
        finish(tr, video_s)

    def finish(tr: PlateTrack, video_t: float) -> None:
        """Turn a crossing into a final event, the moment its plate is decided."""
        ev = tr.event
        if ev is None or ev.get("final"):
            return
        vote = tr.voter.accepted()
        at = datetime.strptime(ev["hora"], "%H:%M:%S").time()
        tr.done = bool(vote)
        if vote:
            # the tracker sometimes loses a plate for a moment and starts a new track: same plate, same
            # direction, a few seconds apart is one passage, not two
            for prev in reversed(events):
                if prev is ev or not prev.get("final") or not prev.get("plate"):
                    continue
                if ev["segundo_video"] - prev["segundo_video"] > cfg.dedupe_seconds:
                    break
                from platedemo.matching import plate_distance

                if prev["sentido"] == ev["sentido"] and plate_distance(prev["plate"], vote.text) <= 1.0:
                    events.remove(ev)
                    ev["final"] = True
                    return
            m = lists.match(vote.text, at)
            ev.update(patente=rules.display(vote.text), plate=vote.text, confianza=vote.confidence, estado=m.status,
                      nombre=m.label, nota=m.note, lecturas=vote.readings)
        else:
            raw = tr.voter.result()
            ev.update(estado=UNREAD, lecturas=raw.readings if raw else 0,
                      nota=f"mejor intento: {raw.text} ({raw.confidence:.2f})" if raw else "")
        if ev["sentido"] == IN:
            register.enter(ev.get("plate"), tr.tid, ev["estado"], ev["hora"], ev.get("nombre", ""))
        elif register.exit(ev.get("plate"), tr.tid) is None and ev.get("plate"):
            ev["nota"] = (ev.get("nota", "") + "; " if ev.get("nota") else "") + "salida sin ingreso registrado"
        if tr.best_crop is not None:
            name = f"recortes/evento_{ev['n']:03d}_{ev.get('plate') or 'sin_lectura'}.jpg"
            cv2.imwrite(str(out / name), tr.best_crop)
            ev["recorte"] = name
        ev["final"] = True
        ev["demora_s"] = round(video_t - ev["segundo_video"], 2)
        ev_w.writerow([ev["hora"], ev["segundo_video"], tr.tid, ev.get("patente", ""),
                       f"{ev.get('confianza', 0):.2f}" if ev.get("plate") else "", ev["estado"], ev["sentido"],
                       ev.get("nombre", ""), ev.get("nota", ""), ev.get("lecturas", 0), ev["demora_s"], ev.get("recorte", "")])
        ev_f.flush()
        if ev["estado"] in (UNKNOWN, UNREAD) or "sin ingreso" in ev.get("nota", ""):
            al_w.writerow([ev["hora"], ev.get("patente", ""), ev["estado"], ev["sentido"], ev.get("nota", "")])
            al_f.flush()
        if verbose:
            flag = "  ALERTA" if ev["estado"] in (UNKNOWN, UNREAD) else ""
            print(f"[{ev['hora']}] {ev['sentido']:<5} {ev.get('patente') or '—':<10} {ev['estado']:<11} "
                  f"{ev.get('nombre', '')}{flag}", flush=True)

    src.start()
    try:
        while True:
            item = src.latest()
            if item is None:
                break
            if item == "wait":
                continue
            index, video_t, t_cap, frame = item
            if cfg.max_seconds and video_t > cfg.max_seconds:
                break
            t0 = time.perf_counter()
            # 1) motion gate on a tiny grey copy of the zone
            grey = cv2.cvtColor(cv2.resize(frame[zy1:zy2, zx1:zx2], (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            delta = None if prev_grey is None else cv2.absdiff(grey, prev_grey)
            diff = 0.0 if delta is None else float(np.mean(delta))
            # movement = the average change is large, or a small patch changed a lot (a car far away in a
            # wide view barely moves the average); and never sleep longer than idle_check seconds
            moving = (delta is None or diff > cfg.motion_threshold
                      or float(np.mean(delta > 18)) > cfg.motion_fraction
                      or video_t - last_check >= cfg.idle_check)
            prev_grey = grey
            if cfg.cut_threshold and diff > cfg.cut_threshold:
                # scene cut (edited footage) or the camera was moved: close everything that was being followed
                for tr in list(tracker.tracks.values()):
                    tr.last_t = -1e9
                for tr in tracker.expired(video_t):
                    close_unread(tr, video_t)
                    finish(tr, video_t)
                if stab is not None:
                    stab.ref_des = None  # new reference view
            pairs = []
            if moving or tracker.tracks:
                last_check = video_t
                # 2) plates directly on the zone, full resolution
                td = time.perf_counter()
                found = [p for p in plates(frame[zy1:zy2, zx1:zx2]) if p.x2 - p.x1 >= cfg.min_plate_width]
                stats["detector_ms"].append(1000 * (time.perf_counter() - td))
                boxes = [(p.x1 + zx1, p.y1 + zy1, p.x2 + zx1, p.y2 + zy1) for p in found]
                if stab is not None:
                    stab.update(frame, boxes)
                pairs = tracker.update(boxes, video_t, make_voter)
                for tr, (x1, y1, x2, y2) in pairs:
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    mx, my = int(0.06 * (x2 - x1)), int(0.06 * (y2 - y1))
                    crop = frame[max(0, y1 - my):y2 + my, max(0, x1 - mx):x2 + mx]
                    text = ""
                    settled = len(tr.voter.readings) >= cfg.max_readings
                    if not settled and len(tr.voter.readings) >= 6:
                        v = tr.voter.accepted()
                        settled = bool(v and v.agree >= 5)
                    if crop.size and not settled:  # a plate already decided (or a parked car) is not read again
                        text, probs = reader(crop)
                        tr.voter.add(text, probs, x2 - x1, index)
                        if (x2 - x1) > tr.best_w:
                            tr.best_w, tr.best_crop = x2 - x1, crop.copy()
                    # 3) gate line, on the plate centre (mapped to the first frame if stabilising)
                    c = ((x1 + x2) / 2, (y1 + y2) / 2)
                    if stab is not None:
                        c = stab.to_ref(c)
                    if tr.first_centre is None:
                        tr.first_centre = c
                    tr.travel = max(tr.travel, float(np.hypot(c[0] - tr.first_centre[0], c[1] - tr.first_centre[1])))
                    if cfg.trigger == "line":
                        direction = crossing.update(tr.tid, c)
                        if direction and tr.travel < cfg.min_travel / scale:
                            crossing.uncount(tr.tid)  # a parked car's plate wobbling on the line
                            direction = None
                    else:
                        # zone: the first time this plate has a firm reading, the vehicle is at the gate.
                        # It must have moved to get there: parked cars inside the zone never count.
                        v = tr.voter.accepted() if tr.event is None else None
                        direction = cfg.lane_direction if v and tr.travel >= cfg.min_travel / scale else None
                    tk_w.writerow([f"{video_t:.2f}", tr.tid, x1, y1, x2, y2, f"{c[0]:.0f}", f"{c[1]:.0f}",
                                   f"{tr.travel:.0f}", text if crop.size else "", direction or ""])
                    if direction and tr.event is None:
                        open_event(tr, video_t, direction)
            else:
                stats["idle"] += 1
            # 4) a crossing is final once its plate is decided or the plate leaves the view
            for tr in list(tracker.tracks.values()):
                if tr.event and not tr.event["final"]:
                    v = tr.voter.accepted()
                    if v and v.agree >= cfg.finalize_agree and v.confidence >= cfg.finalize_confidence:
                        finish(tr, video_t)
            for tr in tracker.expired(video_t):
                close_unread(tr, video_t)  # seen at the gate but never read firmly: logged as unread, with its crop
                finish(tr, video_t)
            settle_unread(video_t)
            stats["processed"] += 1
            stats["frame_ms"].append(1000 * (time.perf_counter() - t0))
            stats["lag_s"].append(time.perf_counter() - t_cap)
            # 5) what a guard would see right now: a light snapshot of the state (no drawing here)
            if cfg.show or cfg.save_video:
                views = []
                for tr in tracker.tracks.values():
                    if video_t - tr.last_t > 0.4:
                        continue  # kept alive in case it shows up again, but not on screen where it no longer is
                    v = tr.voter.accepted()
                    status = tr.event["estado"] if tr.event and tr.event["final"] else (
                        lists.match(v.text).status if v else "leyendo")
                    b = tuple(c * scale for c in tr.box)
                    views.append(TrackView(tr.tid, b, rules.display(v.text) if v else "", status,
                                           tr.event["sentido"] if tr.event else None))
                snap = {
                    "index": index, "clock": clock_at(video_t), "views": views,
                    "events": [EventView(e["hora"], e["sentido"], e.get("patente", "") if e["final"] else "leyendo…",
                                         e["estado"] if e["final"] else "leyendo") for e in events],
                    "inside": [InsideView(rules.display(r.plate) if r.plate else "", r.status, r.since, r.previous)
                               for r in register.still_inside()],
                    "counters": {"ingresos": sum(e["sentido"] == IN for e in events),
                                 "egresos": sum(e["sentido"] == OUT for e in events),
                                 "alertas": sum(e["final"] and e["estado"] in (UNKNOWN, UNREAD) for e in events)},
                    "fps": 1000 / np.mean(stats["frame_ms"][-20:]) if stats["frame_ms"] else 0,
                }
                if cfg.save_video:
                    snapshots.append(snap)
                if cfg.show:
                    cv2.imshow("platedemo - en vivo (q para salir)", render(frame, snap))
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
    finally:
        src.stop()
        settle_unread(float("inf"), force=True)
        for tr in list(tracker.tracks.values()):
            finish(tr, tr.last_t)
        ev_f.close()
        al_f.close()
        tk_f.close()
        if cfg.show:
            cv2.destroyAllWindows()
    wall = time.perf_counter() - t_start
    if cfg.save_video and snapshots:
        # After the live run: draw every frame of the video with the newest state the live run had at that
        # moment. The video is smooth, but what it shows is exactly what was known live, frame by frame.
        cap = cv2.VideoCapture(source)
        k, i = 0, 0
        writer = None
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            while k + 1 < len(snapshots) and snapshots[k + 1]["index"] <= i:
                k += 1
            if snapshots[k]["index"] <= i:
                img = render(frame, snapshots[k])
                if writer is None:
                    writer = VideoWriter(out / "live_raw.mp4", img.shape[1], img.shape[0], src.fps)
                writer.write(img)
            i += 1
            if cfg.max_seconds and i / src.fps > cfg.max_seconds:
                break
        cap.release()
        if writer:
            writer.close()
            if not to_h264(out / "live_raw.mp4", out / "live.mp4", crf=26):
                (out / "live_raw.mp4").rename(out / "live.mp4")
            else:
                (out / "live_raw.mp4").unlink()
    register.save(out / "estado_final.csv")
    video_seconds = (src.decoded + src.skipped_decode) / src.fps
    summary = {
        "config": asdict(cfg),
        "source": {"width": src.width, "height": src.height, "fps": round(src.fps, 2)},
        "wall_seconds": round(wall, 1),
        "video_seconds": round(video_seconds, 1),
        "keeps_up": wall <= video_seconds * 1.05 if cfg.realtime else None,
        "frames_in_video": src.decoded + src.skipped_decode,
        "frames_analysed": stats["processed"],
        "frames_idle_no_motion": stats["idle"],
        "analysed_per_second": round(stats["processed"] / wall, 2) if wall else 0,
        "share_of_frames_analysed": round(stats["processed"] / max(1, src.decoded + src.skipped_decode), 3),
        "ms_per_analysed_frame_median": round(float(np.median(stats["frame_ms"])), 1) if stats["frame_ms"] else 0,
        "ms_plate_detector_median": round(float(np.median(stats["detector_ms"])), 1) if stats["detector_ms"] else 0,
        "lag_from_capture_s_median": round(float(np.median(stats["lag_s"])), 2) if stats["lag_s"] else 0,
        "event_delay_s_median": round(float(np.median([e["demora_s"] for e in events if "demora_s" in e])), 2) if events else None,
        "events": [{k: v for k, v in e.items() if k in ("hora", "segundo_video", "sentido", "patente", "estado", "nombre", "nota", "demora_s")}
                   for e in events],
        "still_inside": [{"patente": rules.display(r.plate) if r.plate else r.key, "estado": r.status, "desde": r.since}
                         for r in register.still_inside()],
    }
    (out / "live_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
