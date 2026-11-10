# Evaluation

Two separate questions:

1. **Does the whole pipeline work on a real video with Argentine plates?** One public 4K clip, seven vehicles that cross the gate line, checked one by one against a hand-made ground truth.
2. **How well does the reader alone read Argentine plates?** 147 cropped plates from a public benchmark.

Short version: on the clip, the final run gets every crossing and every status right, reads three of five legible plates exactly and the other two with one wrong character each. But it is one clip, seven vehicles, and the rules were adjusted while looking at it. The plate reader alone gets 95.2 % of Argentine cropped plates right.

## 1. The clip

`pexels-38118760.mp4`: residential street in Argentina, 3840x2160 at 24 fps, 58.4 s, camera on a tripod (see [DATA.md](DATA.md)). Every moving vehicle drives towards the camera and turns, so the demo uses it as the **exit lane** of a gated neighbourhood (`--flip`), with a starting state that says who had entered earlier that morning.

### Ground truth

Written **before** running the full pipeline, from enlarged crops of the 4K frames (the plate detector was used only to locate plates for the crops; the text was read by eye). File: [`evaluation/ground_truth/porteria_pexels.csv`](../evaluation/ground_truth/porteria_pexels.csv).

| Vehicle | Plate (by eye) | Legible | Expected status |
| --- | --- | --- | --- |
| Motorcycle, from the front | none (Argentine motorcycles only have a rear plate) | no | no leída |
| Armoured van 1 | `AG 279 ?C` (the sixth character could be X or N) | partly | proveedor |
| Armoured van 2 | `AG 145 PE` | yes | desconocido (not in the lists) |
| Fiat Palio | `HME 273` (old black plate) | yes | visita |
| Black Mercedes | unreadable even by eye (dark car, against the light) | no | no leída |
| Scania truck | `GEE 291` | yes | proveedor |
| Ford Ka | `AC 611 JO` | yes | residente |

Not gate crossings: a blue car that drives across the bottom of the frame (cross traffic), and the parked cars.

One change after seeing results, stated here: the Scania's time window was `42-48 s` and became `37.5-48 s`. The truck was parked 20 px from the gate line and crosses it as soon as it starts moving, at 38 s; the event is the same vehicle (track 1, its plate read as `GIE 291`). Without that change, the final run would show 6 of 7 crossings and 1 false one, with the same events.

### Results, run by run

Every run below is the full 58 s clip, one of every two frames, CPU only. The first run is the untouched pipeline.

| Run | What changed | Crossings found / false | Plates exact (legible) | Characters right | Status right |
| --- | --- | :---: | :---: | :---: | :---: |
| 1 | first version | 6 / 1 | 0 of 5 | 6 of 26 | 3 of 7 |
| 2 | vote with the 12 best readings by size and confidence; skip plates under 60 px; line across the whole frame | 6 / 2 | 3 of 5 | 24 of 26 | 4 of 7 |
| 3 | confidence squared in the vote; accept a reading only above 0.45 | 6 / 2 | 3 of 5 | 25 of 26 | 6 of 7 |
| 4 | reset a track when its box jumps or changes size abruptly; confirm a crossing 20 px past the line | 6 / 2 | 3 of 5 | 25 of 26 | 6 of 7 |
| **5 (final)** | **stabilise against camera movement** | **7 / 0** | **3 of 5** | **30 of 32** | **7 of 7** |

(Character counts go from 26 to 32 when the Scania is detected, since its plate then counts.)

Final run, vehicle by vehicle:

| Vehicle | Read | Status | Right? |
| --- | --- | --- | --- |
| Motorcycle | (none) | no leída | yes |
| Armoured van 1 | `AG 279 XC` | proveedor | yes (all known characters right) |
| Armoured van 2 | `AG 145 PE` | desconocido, "salida sin ingreso registrado" | yes |
| Fiat Palio | `HWE 273` | visita (matched `HME 273`, distance 1) | status yes, plate one character off |
| Black Mercedes | (none; best attempt `PTE111` at 0.31) | no leída | yes |
| Scania | `GIE 291` | proveedor (matched `GEE 291`, distance 1) | status yes, plate one character off |
| Ford Ka | `AC 611 JO` | residente | yes |

At the end, the register lists the three vehicles from the starting state that did not leave, and flags `AG 145 PE` and the two unread vehicles as exits without a recorded entry.

### What went wrong on the way

Each problem below was real, found in a run, and is now covered by a unit test where possible.

- **Reading too early.** Run 1 capped the readings per vehicle at 16. Vehicles are first seen far away (plates 50-65 px wide), so the cap was used up before they came close (plates 100-160 px), and the good readings never happened. Fix: skip plates under 60 px, read up to 60 times, and vote with the 12 heaviest readings.
- **Many mediocre readings out-voting a few sharp ones.** The Palio was read `HWE 273` at 0.93 confidence three times when close, and `NVE 271`, `NME 271`... at 0.5-0.7 many more times. With linear weights the `1` won; with confidence squared the `3` wins.
- **A guess on an unreadable plate.** The black Mercedes got `PTE 111` at 0.32 confidence, enough to be reported as a plate. The other accepted readings are between 0.60 and 1.00, so the threshold went to 0.45. A wrong plate is worse than "no leída": the guard can look at the crop.
- **The motorcycle and the parked car swapping IDs.** When the motorcycle passed right next to a parked car, the tracker briefly swapped their IDs, so the parked car "jumped" across the line. Fix: a jump larger than half the box height resets that track's side.
- **A parked truck whose box changes shape.** The Scania's box sometimes includes the trailer and sometimes not; its bottom moved a few pixels across the line. Fix: confirm only 20 px past the line, and treat an abrupt change in box area like a jump.
- **The camera is not perfectly still.** This was the real cause of the last false crossings. In the first two seconds the shot drifts down about 70 px and later settles back, so parked cars drifted across a line that was fixed on the screen. Fix: `--stabilize` matches background keypoints (vehicles masked out) to the first frame and keeps the line attached to the road. It worked on 700 of 700 frames. With a camera bolted to a wall this is not needed, but cameras on poles do sway.
- **A format check that could invent plates.** Adding Argentine motorcycle formats (`123 ABC`, `A 123 BCD`) let a garbled car reading be forced into a motorcycle format by changing three characters. Fix: at most two position fixes per reading.

### Speed

Measured on the machine this was built on: a Dell laptop with an Intel Core i7-10510U (4 cores, 15 W), Intel UHD graphics, other programs using a large part of the CPU at the time. **Not** the desktop with the Radeon RX 5700 XT; it should be clearly faster there.

| Step | ms per processed frame |
| --- | ---: |
| Vehicles (RF-DETR Nano, ONNX, 1280 px frame) | 412 |
| Plates (detector on each readable vehicle) | 255 |
| Reading (OCR) | 35 |
| Stabilisation | 50 |
| Drawing the annotated frame | 88 |

Final run: 701 processed frames in 627 s, 1.12 processed frames per second with 4K input and one of every two frames. The 58 s clip takes about 10.5 minutes (decoding 4K is part of the rest). ONNX Runtime runs with one thread per physical core, which was faster than the default on this hyper-threaded CPU. DirectML on this laptop's integrated Intel GPU was slower than the CPU, so the default is `--device cpu`; on a discrete GPU try `--device dml`.

For live use: a 1080p stream instead of 4K, about 5 analysed frames per second (a car at the gate stays for several seconds), and plate reading only inside a zone near the gate (`--read-zone`).

## 2. The plate reader on Argentine plates

Set: `arg_plate_benchmark` (147 cropped plates; [DATA.md](DATA.md)). Command:

```sh
python -m platedemo.evaluate ocr PATH/benchmark --json results/ocr_benchmark_arg.json
```

| OCR model | Right | Mercosur | Old format and motorcycle |
| --- | :---: | :---: | :---: |
| `cct-s-v2-global-model` (default) | **140 / 147 (95.2 %)** | 68 / 68 | 72 / 79 |
| `argentinian-plates-cnn-synth-model` | 140 / 147 (95.2 %) | 68 / 68 | 72 / 79 |
| `global-plates-mobile-vit-v2-model` | 134 / 147 (91.2 %) | 67 / 68 | 67 / 79 |

Caveats:

- These are clean, tight crops from the same source as the models' training data, so this is a friendly test. Crops cut by our own detector out of a moving video are harder; the clip above is the realistic case.
- The two Argentine-only models were also tried on the clip in an early probe and did worse than the global one on the Mercosur plates of the armoured vans (for example `AB677MD` and `AZ991UL` for `AG 279 XC`), which is why the global model is the default.
- The format check (with the motorcycle formats and at most two fixes per reading) gives the same count as the raw readings: it never turns a right reading into a wrong one on this set. Before the motorcycle formats were added, it broke one correct motorcycle reading, because 4 of the 147 labels are motorcycle plates.

## How to reproduce

```sh
# full pipeline on the clip, then score it
python -m platedemo pexels-38118760.mp4 --line 0,480,1280,480 --flip --stabilize \
  --lists data/listas --inside data/listas/estado_inicial.csv --clock 10:15:00 --out out
python -m platedemo.evaluate events out/events.csv evaluation/ground_truth/porteria_pexels.csv --lists data/listas

# the saved final run, without running any model
python -m platedemo.evaluate events results/porteria_pexels/events.csv evaluation/ground_truth/porteria_pexels.csv --lists data/listas
```
