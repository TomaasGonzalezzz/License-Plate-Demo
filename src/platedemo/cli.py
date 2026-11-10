"""Command line: ``python -m platedemo VIDEO --line x1,y1,x2,y2 --out OUT``.

Run ``python -m platedemo --help`` for every option. Examples::

    # the demo clip, with the example lists and the earlier entries
    python -m platedemo video.mp4 --line 0,520,1280,520 --lists data/listas --inside data/listas/estado_inicial.csv --out out

    # a recorder over RTSP (stop with Ctrl+C)
    python -m platedemo "rtsp://usuario:clave@192.168.0.20:554/Streaming/Channels/101" --line ... --out out
"""

from __future__ import annotations

import argparse
import sys

from platedemo.pipeline import RunConfig, run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="platedemo", description="Lectura de patentes en video para control de accesos (demo).")
    p.add_argument("video", help="archivo de video o URL RTSP")
    p.add_argument("--line", help="línea de la portería x1,y1,x2,y2 en píxeles del cuadro de trabajo (máx. --max-width de ancho); obligatoria salvo con --trigger zona")
    p.add_argument("--flip", action="store_true", help="invertir entra/sale (por defecto, cruzar hacia la derecha de la línea es 'entra')")
    p.add_argument("--stabilize", action="store_true", help="seguir pequeños movimientos de la cámara (viento, cámara en mano): la línea queda pegada a la calle")
    p.add_argument("--out", default="out", help="carpeta de salida")
    p.add_argument("--lists", help="carpeta con residentes.csv, proveedores.csv y visitas.csv")
    p.add_argument("--inside", help="CSV con los vehículos que ya estaban adentro al empezar (patente,estado,hora_ingreso,nombre)")
    p.add_argument("--format", default="ar", help="formatos válidos: ar (viejo + Mercosur), ar-viejo, ar-mercosur, any, o patrones como 'LLL NNNN|NN LLL'")
    p.add_argument("--clock", default="10:00:00", help="hora del reloj al inicio del video (HH:MM:SS)")
    p.add_argument("--device", default="cpu", choices=["cpu", "dml", "auto"], help="dml = placa de video por DirectML (Windows), vuelve a CPU si falla")
    p.add_argument("--stride", type=int, default=2, help="procesar 1 de cada N cuadros")
    p.add_argument("--start", type=int, default=0, help="primer cuadro")
    p.add_argument("--end", type=int, help="último cuadro (excluido)")
    p.add_argument("--max-width", type=int, default=1280, help="ancho del cuadro de trabajo")
    p.add_argument("--read-zone", help="zona x1,y1,x2,y2 donde se intenta leer la patente (por defecto, todo el cuadro)")
    p.add_argument("--min-vehicle-width", type=float, default=90.0, help="ancho mínimo del vehículo para intentar leer (píxeles de trabajo)")
    p.add_argument("--min-plate-width", type=float, default=60.0, help="ancho mínimo de la patente para leerla (píxeles del video original)")
    p.add_argument("--vehicle-model", default="rf-detr-nano-384-coco")
    p.add_argument("--plate-model", default="yolo-v9-t-640-license-plate-end2end")
    p.add_argument("--ocr-model", default="cct-s-v2-global-model")
    p.add_argument("--min-confidence", type=float, default=0.45, help="confianza mínima para aceptar una lectura")
    p.add_argument("--match-distance", type=float, default=1.0, help="distancia máxima para coincidir con una lista")
    p.add_argument("--title", default="Portería")
    p.add_argument("--credit", default="", help="texto de crédito del video (licencia) que se dibuja abajo")
    p.add_argument("--no-video", action="store_true", help="no escribir el video anotado (más rápido)")
    live = p.add_argument_group("modo en vivo (--live)")
    live.add_argument("--live", action="store_true", help="modo en vivo: sin detector de vehículos, patentes en la zona de lectura, eventos al instante")
    live.add_argument("--no-realtime", action="store_true", help="en vivo con un archivo: procesar todos los cuadros en vez de reproducir a velocidad real")
    live.add_argument("--show", action="store_true", help="mostrar una ventana con la imagen anotada (q para salir)")
    live.add_argument("--save-video", action="store_true", help="grabar lo que se vio en vivo en live.mp4")
    live.add_argument("--max-seconds", type=float, help="cortar después de estos segundos de video")
    live.add_argument("--live-plate-model", default="yolo-v9-t-640-license-plate-end2end",
                      help="detector de patentes en vivo (yolo-v9-t-384-... es más rápido, con patentes más grandes)")
    live.add_argument("--min-live-plate-width", type=float, default=40.0, help="ancho mínimo de patente en vivo (píxeles del video)")
    live.add_argument("--trigger", choices=["linea", "zona"], default="linea",
                      help="linea: se registra al cruzar la línea; zona: al leer la patente dentro de --read-zone (barrera donde el auto frena)")
    live.add_argument("--lane", choices=["entra", "sale"], default="entra", help="con --trigger zona: sentido del carril")
    live.add_argument("--min-travel", type=float, help="píxeles que tiene que moverse una patente para contar (por defecto 40 con línea, 0 con zona)")
    live.add_argument("--plate-threshold", type=float, default=0.3,
                      help="confianza mínima del detector de patentes (más bajo = encuentra más de noche, con más ruido)")
    live.add_argument("--cut-threshold", type=float, default=12.0,
                      help="cambio de imagen que se toma como corte o cámara movida (0 = nunca; útil con videos editados con zoom)")
    return p


def main_live(a) -> int:
    from platedemo.live import LiveConfig, run_live

    cfg = LiveConfig(
        line=a.line, read_zone=a.read_zone, trigger="zone" if a.trigger == "zona" else "line", lane_direction=a.lane,
        flip=a.flip, lists=a.lists, inside=a.inside, formats=a.format,
        clock=a.clock if a.clock != "10:00:00" else None, realtime=not a.no_realtime, device=a.device,
        plate_model=a.live_plate_model, ocr_model=a.ocr_model, work_width=a.max_width,
        min_plate_width=a.min_live_plate_width, min_confidence=a.min_confidence, match_distance=a.match_distance,
        max_seconds=a.max_seconds, show=a.show, stabilize=a.stabilize, min_travel=a.min_travel, save_video=a.save_video, title=a.title if a.title != "Portería" else "Portería (en vivo)",
        credit=a.credit, cut_threshold=a.cut_threshold, plate_threshold=a.plate_threshold,
    )
    print(f"En vivo: {a.video}  (Ctrl+C para cortar)", flush=True)
    try:
        s = run_live(a.video, a.out, cfg)
    except KeyboardInterrupt:
        print("cortado")
        return 0
    print()
    print(f"Video: {s['video_seconds']} s · procesado en {s['wall_seconds']} s · "
          f"{s['analysed_per_second']} cuadros analizados por segundo ({s['share_of_frames_analysed']:.0%} de los cuadros)")
    print(f"Cuadros sin movimiento (salteados): {s['frames_idle_no_motion']} · demora mediana de cada evento: {s['event_delay_s_median']} s")
    print(f"Siguen adentro: {', '.join(r['patente'] for r in s['still_inside']) or 'nadie'}")
    print(f"Salida en {a.out}/ (events.csv, alertas.csv, live_run.json)")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.live:
        return main_live(a)
    if not a.line:
        build_parser().error("--line es obligatoria (salvo en vivo con --trigger zona)")
    cfg = RunConfig(
        line=a.line, flip=a.flip, stabilize=a.stabilize, lists=a.lists, inside=a.inside, formats=a.format, clock=a.clock,
        device=a.device, stride=a.stride, start=a.start, end=a.end, max_width=a.max_width,
        read_zone=a.read_zone, min_vehicle_width=a.min_vehicle_width, min_plate_width=a.min_plate_width,
        vehicle_model=a.vehicle_model,
        plate_model=a.plate_model, ocr_model=a.ocr_model, min_confidence=a.min_confidence,
        match_distance=a.match_distance, title=a.title, credit=a.credit, write_video=not a.no_video,
    )
    print(f"Procesando {a.video} ...", flush=True)
    result = run(a.video, a.out, cfg)
    print()
    print(open(f"{a.out}/resumen.txt", encoding="utf-8").read())
    print(f"{len(result.events)} eventos en {result.seconds:.0f} s ({result.frames} cuadros procesados). Salida en {a.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
