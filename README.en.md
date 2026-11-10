<div align="center">

# License Plate Demo

**Read the plates of vehicles crossing a gate in a video, check them against access lists, and keep a register of who is still inside.**

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![No AGPL](https://img.shields.io/badge/AGPL-none-lightgrey)

[Leer en español](README.md) · [How it was evaluated](docs/EVALUATION.md) · [Live mode](docs/LIVE.md) · [Data and licenses](docs/DATA.md)

<img src="demo/demo.gif" alt="Animated: each vehicle gets a box, its plate in large type and a colour for its status; a side panel lists the last events and who is still inside" width="900">

<sub>Video: Santiago Balbuena, Pexels (Pexels License), overlay added. Names in the lists are invented.</sub>

</div>

A computer-vision demo applied to vehicle access control. You give it a video from a fixed camera (or an RTSP stream from a recorder), a line that marks the gate, and three CSV lists (residents, suppliers, visitors with their allowed hours). For every vehicle that crosses the line it records the time, the plate, a confidence score, the status (`residente`, `visita`, `proveedor`, `desconocido` or `no leída`) and whether it went in or out, and it keeps a running list of who is still inside. Everything runs on a CPU and every component has a permissive license (no AGPL).

## Results at a glance

Main clip: a residential street in Argentina, 4K, fixed camera, 58 s, seven vehicles cross the gate line. Plates were read by hand from enlarged crops **before** running the model.

| | Result |
| --- | :---: |
| Gate crossings detected | **7 of 7**, 0 false |
| Status right (resident / visitor / supplier / unknown / unread) | **7 of 7** |
| Legible plates read exactly | 3 of 5 (30 of 32 characters) |
| Plates read with one wrong character, still matched to the right list entry | 2 (`GIE 291` for `GEE 291`, `HWE 273` for `HME 273`) |
| Illegible plates (motorcycle seen from the front, dark car against the light) | 2 of 2 reported as `no leída`, none invented |
| Speed (low-power laptop CPU, i7-10510U, 4K input, 1 of 2 frames) | 1.1 frames/s |

**This is one clip and seven vehicles, and the tracking rules were adjusted while looking at it** (the first, untouched run got 3 statuses right out of 7 and no plate fully right; every step is in [EVALUATION.md](docs/EVALUATION.md)). On a separate set of 147 cropped Argentine plates, the plate reader alone gets **140 right (95.2 %)**: 68 of 68 Mercosur, 72 of 79 old-format and motorcycle plates.

## What it does

```text
 video / RTSP ──► RF-DETR ──► ByteTrack ──► plate detector ──► fast-plate-ocr ──► vote across frames
  (4K kept for    (vehicles   (one ID per   (on the full-res    (text + per-char    (per character,
   plate crops)    on 1280 px) vehicle)      vehicle crop)       confidence)          weighted by size)
                                                                                        │
   annotated.mp4 ◄── register: who is inside ◄── gate line: in / out ◄── access lists ◄─┘
   events.csv          (start state + entries      (side change, confirmed,   (format check, tolerant
   recortes/            − exits; exits without      stabilised against         match: O/0, I/1, B/8...)
   resumen.txt          an entry are flagged)       camera movement)
```

1. **Detect and track vehicles.** [RF-DETR](https://github.com/roboflow/rf-detr) Nano (Apache-2.0, COCO weights, run as ONNX) on a 1280 px copy of each frame; ByteTrack from Roboflow's [trackers](https://github.com/roboflow/trackers) (Apache-2.0) gives each vehicle a steady ID. Same models as my [counting demo](https://github.com/TomaasGonzalezzz/Object-Counting-Demo).
2. **Find and read the plate** on the **full-resolution** crop of each vehicle: plate detector from [open-image-models](https://github.com/ankandrew/open-image-models) and OCR from [fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr) (MIT code).
3. **Vote across frames.** A plate is read in many frames. Each reading is weighted by its confidence squared and the plate's size; the 12 best vote character by character. A single sharp close-up beats several blurry far readings.
4. **Validate the format.** Argentine cars `ABC 123` and `AB 123 CD`, motorcycles `123 ABC` and `A 123 BCD`. Letters read where digits go (`O`→`0`, `I`→`1`...) are fixed by position, at most two per reading. Other countries: `--format "LLL NNNN"`.
5. **Match the lists, tolerantly.** Edit distance where swapping look-alike characters costs 0.25 instead of 1; a reading matches if the distance is ≤ 1. Visitors outside their hours are flagged.
6. **Gate line.** In or out depends on the side the vehicle crosses to. To avoid false crossings: two confirming frames, 20 px past the line, and a track whose box jumps or changes size abruptly (two vehicles side by side, a trailer that comes and goes) starts over. `--stabilize` follows small camera movements so the line stays on the road.
7. **Register.** Entries add, exits remove the closest plate inside. It can start from a saved state and writes the final one, so it carries over from one run to the next.

| Output | Contents |
| --- | --- |
| `annotated.mp4` | Boxes, IDs, the plate in large type with its status colour, the gate line, last events and who is still inside |
| `events.csv` | time, video second, ID, plate, confidence, status, direction, list name, note, number of readings, crop paths |
| `recortes/` | Best plate crop and vehicle crop per event |
| `resumen.txt`, `resumen.json` | Closing summary: entries, exits, unknown, unread, who is still inside, exits without an entry |
| `estado_final.csv` | Who is still inside, reusable as `--inside` next time |
| `readings.csv`, `tracks.csv`, `run.json` | Every OCR reading, every tracked box, settings and timings |

## Run it

Windows, macOS or Linux, Python 3.11+, about 400 MB of dependencies (no PyTorch).

```sh
git clone <this repo> && cd License-Plate-Demo
uv sync                      # or: python -m venv .venv && .venv/Scripts/pip install -e .
curl -L -o pexels-38118760.mp4 https://www.pexels.com/download/video/38118760/

uv run python -m platedemo pexels-38118760.mp4 --line 0,480,1280,480 --flip --stabilize \
  --lists data/listas --inside data/listas/estado_inicial.csv --clock 10:15:00 --out out
```

Model weights (about 40 MB) download on the first run. `--device dml` uses any DirectX 12 GPU on Windows through ONNX Runtime DirectML (AMD Radeon included) and falls back to the CPU if it fails. `--no-video` skips the annotated video. A recorder works the same way: pass `rtsp://user:pass@192.168.0.20:554/...` instead of a file. Check the result against the ground truth:

```sh
uv run python -m platedemo.evaluate events out/events.csv evaluation/ground_truth/porteria_pexels.csv --lists data/listas
uv run pytest -q
```

## Live mode

`--live` is the real-time version for a gate camera: no vehicle detector, plates read directly in a zone near the gate, frames dropped when the CPU is busy (as with a camera), and each event written the moment it happens. Each clip played at real speed three times on the same laptop CPU (ranges, since results vary with timing):

| Clip | Plates right | Analysed frames/s | Kept up |
| --- | :---: | :---: | :---: |
| Gate-style entrance, day and night (LPR camera in front of the lane) | 3 to 4 of 5 | 6.5 to 11 | 3 of 3 |
| Exit from behind, day and night | 1 to 2 of 2 | 6.2 to 6.6 | 3 of 3 |
| Argentine street at 1080p (general street camera, small plates) | 0 to 2 of 5 | 3.3 to 5.6 | 3 of 3 |

A second round used clips where vehicles stop in front of the camera, as at a gated-community barrier (`--trigger zona`), after fixing what those clips exposed:

| Clip | Right status | Analysed frames/s |
| --- | :---: | :---: |
| Parking barrier, day (cars stop) | 2 of 2 in every run | 2.6 to 7.2 |
| Night, infrared, cars waiting at the gate | 4 to 6 of 6 legible plates | 3.5 to 7.7 |
| Truck arriving at a plant gate | 1 of 1 in every run | 4.4 to 7.5 |

With a camera placed for plates it works in real time on a laptop, with occasional misses (mostly at night); a general street camera is not enough. Details, commands and what it does not see: [docs/LIVE.md](docs/LIVE.md).

What the live mode showed in each second-round case, recorded during the real-time run (invented lists):

| Case | Preview | Full video | Original video |
| --- | --- | --- | --- |
| Parking barrier, day | [GIF](demo/casos/barrera_dia.gif) | [MP4](demo/casos/barrera_dia.mp4) | [YouTube oJ1sAD7IoNs](https://www.youtube.com/watch?v=oJ1sAD7IoNs), 0:48 to 1:10 |
| Night, infrared | [GIF](demo/casos/noche_infrarrojo.gif) | [MP4](demo/casos/noche_infrarrojo.mp4) | [Lewis Surveillance, YouTube Roc4YDz6kjo](https://www.youtube.com/watch?v=Roc4YDz6kjo), 1:43 to 2:32 |
| Truck at a plant gate | [GIF](demo/casos/camion_porton.gif) | [MP4](demo/casos/camion_porton.mp4) | [YouTube xSYAfvnfUg4](https://www.youtube.com/watch?v=xSYAfvnfUg4), first 7 s |

<img src="demo/casos/barrera_dia.gif" alt="A car stopped at a barrier with its plate read and its status, and the panel of entries and who is still inside" width="760">

## Limitations

- **One clip, seven vehicles, tuned on it.** The numbers show the pipeline works end to end on real Argentine plates; they are not an accuracy rate. Before promising a number to anyone, it has to be measured on their camera.
- **Plate size decides almost everything.** Here plates are 60 to 160 px wide in 4K. Below about 80 px, reading errors go up quickly. A dedicated plate camera (front-on, 3 to 8 m, infrared at night) matters more than any model change.
- **No real entries in the clip.** Every vehicle drives towards the camera, so the demo treats it as the exit lane; entries and the in/out cross-check are covered by tests and by loading a starting state.
- **Speed.** About 1.1 frames per second on a low-power laptop with 4K input; real time needs a 1080p stream, fewer frames per second or a GPU.
- **Plate detector weights have no stated license** (see [DATA.md](docs/DATA.md)). Code is MIT; confirm the weights or retrain before any commercial use.
- **Personal data.** Plates linked to people are personal data (Argentina: Ley 25.326). A real deployment needs signage, short retention and restricted access. No face recognition is used or needed.

## Repository

```text
src/platedemo/     formats, matching, voting, gate, stabilize, models, pipeline, live, draw, video, evaluate, cli
data/              invented access lists and starting states (one folder per test clip)
evaluation/        hand-made ground truth and clip settings
results/           saved outputs of the final offline run (events, readings, tracks, summary, crops)
demo/              demo.mp4 and demo.gif
figures/           README images
docs/              EVALUATION.md, LIVE.md, DATA.md
tests/             formats, tolerant matching, voting, in/out and register, stabilisation, live tracking
```

MIT license. Videos and datasets are not included; see [docs/DATA.md](docs/DATA.md).
