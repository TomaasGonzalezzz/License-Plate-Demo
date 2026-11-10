"""Score a run against the hand-made ground truth, and the OCR against an Argentine plate set.

Ground truth (``evaluation/ground_truth/<clip>.csv``) has one row per vehicle that
really crosses the gate: time window, direction, the plate as read by a person
(``?`` for a character that cannot be told apart, empty if illegible) and whether
it is legible. It was written before running the model, from enlarged crops.

Usage::

    python -m platedemo.evaluate events out/events.csv evaluation/ground_truth/porteria_pexels.csv --lists data/listas
    python -m platedemo.evaluate ocr PATH/benchmark --models cct-s-v2-global-model,argentinian-plates-cnn-synth-model
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import time
from pathlib import Path

from platedemo.formats import normalize
from platedemo.matching import UNREAD, AccessLists


def _plate_ok(pred: str, truth: str) -> tuple[bool, int, int]:
    """Exact match on known characters; returns (all_known_match, known_correct, known_total)."""
    pred, truth = normalize(pred), truth.upper()
    if len(pred) != len(truth):
        known = sum(c != "?" for c in truth)
        return False, 0, known
    correct = sum(1 for p, t in zip(pred, truth) if t != "?" and p == t)
    known = sum(c != "?" for c in truth)
    return correct == known, correct, known


def score_events(events_csv: str | Path, truth_csv: str | Path, lists_dir: str | None = None, clock: str = "10:15:00") -> dict:
    preds = list(csv.DictReader(open(events_csv, encoding="utf-8")))
    truth = list(csv.DictReader(open(truth_csv, encoding="utf-8")))
    lists = AccessLists.load(lists_dir)
    used: set[int] = set()
    rows = []
    for t in truth:
        lo, hi = float(t["desde_s"]), float(t["hasta_s"])
        cand = [i for i, p in enumerate(preds) if i not in used and lo - 1.0 <= float(p["segundo_video"]) <= hi + 1.0
                and p["sentido"] == t["sentido"]]
        match = cand[0] if cand else None
        if match is not None:
            used.add(match)
        p = preds[match] if match is not None else None
        gt_plate = t["patente"].strip().upper()
        legible = t["legible"].strip()
        # expected status: what the lists say for the true plate (unreadable -> "no leída")
        if not gt_plate or legible == "no":
            expected = UNREAD
        else:
            expected = lists.match(gt_plate.replace("?", "X") if "?" in gt_plate else gt_plate).status if lists.entries else ""
            if "?" in gt_plate:
                # with an unknown character, use the closest list entry ignoring that position
                best = min(lists.entries, key=lambda e: sum(a != b for a, b in zip(e.plate, gt_plate) if b != "?") + abs(len(e.plate) - len(gt_plate)), default=None)
                if best and len(best.plate) == len(gt_plate) and all(b == "?" or a == b for a, b in zip(best.plate, gt_plate)):
                    expected = best.category
        row = {"vehiculo": t["vehiculo"], "patente_real": gt_plate or "(ilegible)", "detectado": p is not None,
               "patente_leida": (p or {}).get("patente", ""), "estado": (p or {}).get("estado", ""), "estado_esperado": expected}
        if p is not None and gt_plate and legible != "no":
            exact, ok, n = _plate_ok(p["patente"], gt_plate)
            row.update(lectura_correcta=exact, caracteres_ok=ok, caracteres=n)
        row["estado_correcto"] = p is not None and p["estado"] == expected
        rows.append(row)
    false_events = [preds[i] for i in range(len(preds)) if i not in used]
    readable = [r for r in rows if "lectura_correcta" in r]
    summary = {
        "cruces_reales": len(truth),
        "cruces_detectados": sum(r["detectado"] for r in rows),
        "cruces_falsos": len(false_events),
        "patentes_legibles": sum(1 for t in truth if t["legible"].strip() != "no" and t["patente"].strip()),
        "patentes_leidas_exactas": sum(1 for r in readable if r["lectura_correcta"]),
        "caracteres_ok": sum(r.get("caracteres_ok", 0) for r in readable),
        "caracteres_total": sum(r.get("caracteres", 0) for r in readable),
        "estados_correctos": sum(r["estado_correcto"] for r in rows),
        "detalle": rows,
        "eventos_sin_vehiculo_real": [{k: p[k] for k in ("segundo_video", "id", "patente", "estado")} for p in false_events],
    }
    return summary


def score_ocr(folder: str | Path, models: list[str], device: str = "cpu") -> dict:
    import cv2

    from platedemo.formats import PlateRules
    from platedemo.models import PlateReader

    folder = Path(folder)
    rows = list({r["image_path"]: r for r in csv.DictReader(open(folder / "annotations.csv", encoding="utf-8"))}.values())
    rules = PlateRules.from_spec("ar")
    out = {}
    for m in models:
        reader = PlateReader(m, device)
        res = {"total": 0, "exactas": 0, "exactas_con_correccion": 0, "mercosur": [0, 0], "viejas": [0, 0]}
        for r in rows:
            img = cv2.imread(str(folder / r["image_path"]))
            text, _ = reader(img)
            gt = normalize(r["plate_text"])
            fixed, _ = rules.validate(text)
            res["total"] += 1
            res["exactas"] += normalize(text) == gt
            hit = (fixed or normalize(text)) == gt
            res["exactas_con_correccion"] += hit
            key = "mercosur" if len(gt) == 7 else "viejas"
            res[key][0] += hit
            res[key][1] += 1
        out[m] = res
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="platedemo.evaluate")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("events", help="comparar events.csv con la verdad de referencia")
    e.add_argument("events")
    e.add_argument("truth")
    e.add_argument("--lists")
    e.add_argument("--json")
    o = sub.add_parser("ocr", help="lectura sobre patentes argentinas recortadas (annotations.csv + imgs/)")
    o.add_argument("folder")
    o.add_argument("--models", default="cct-s-v2-global-model,argentinian-plates-cnn-synth-model,global-plates-mobile-vit-v2-model")
    o.add_argument("--json")
    a = p.parse_args(argv)
    if a.cmd == "events":
        s = score_events(a.events, a.truth, a.lists)
        print(f"Cruces: {s['cruces_detectados']}/{s['cruces_reales']} detectados, {s['cruces_falsos']} falsos")
        print(f"Patentes legibles leídas exactas: {s['patentes_leidas_exactas']}/{s['patentes_legibles']}"
              f"  (caracteres {s['caracteres_ok']}/{s['caracteres_total']})")
        print(f"Estado correcto: {s['estados_correctos']}/{s['cruces_reales']}")
        for r in s["detalle"]:
            print(f"  {r['vehiculo']:<15} real {r['patente_real']:<11} leída {r['patente_leida'] or '-':<11} "
                  f"estado {r['estado'] or '-':<12} esperado {r['estado_esperado']}")
        result = s
    else:
        result = score_ocr(a.folder, a.models.split(","))
        for m, r in result.items():
            print(f"{m:38s} {r['exactas_con_correccion']}/{r['total']} "
                  f"({r['exactas_con_correccion'] / r['total']:.1%})  sin corrección {r['exactas']}  "
                  f"Mercosur {r['mercosur'][0]}/{r['mercosur'][1]}  viejas {r['viejas'][0]}/{r['viejas'][1]}")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
