# Data

No video or image dataset is stored in this repository. Everything below is downloaded to a folder outside the repo (by default `C:\Users\tomas\dev\external-data\plates`, or any folder you choose).

## Videos

| Clip | What it shows | Source | Author | License |
| --- | --- | --- | --- | --- |
| `pexels-38118760.mp4` (main demo) | Residential street in Argentina, fixed camera on a tripod, 3840x2160 at 24 fps, 58 s. Trucks, armoured vans, cars and a motorcycle drive towards the camera and turn; Argentine plates (old format and Mercosur) are legible in 4K. | [Pexels 38118760](https://www.pexels.com/video/scenic-urban-neighborhood-with-cargo-trucks-38118760/) | Santiago Balbuena | [Pexels License](https://www.pexels.com/license/): free to use and modify, no attribution required (given anyway). Not to be resold unaltered or presented as your own footage. |

### Videos that were checked and not used

Searched first for public videos with Argentine plates and a clear license:

| Video | License | Why it was not used |
| --- | --- | --- |
| "Puente Cespedes Autopista del Oeste, Haedo" ([YouTube JESLEyg-flM](https://www.youtube.com/watch?v=JESLEyg-flM)), Alejandro Alba | CC BY | Argentine plates, fixed camera, 1080p, but the plates are ~50 px wide and blurred by compression: below what a person can read reliably. Good as a future "hard case". |
| "Haedo. Colectora Autopista del oeste. Peaje Dolores Prats" ([YouTube Iif6BIWCLJc](https://www.youtube.com/watch?v=Iif6BIWCLJc)), Alejandro Alba | CC BY | Toll booth far from the camera; plates too small. |
| "Number plate logging from a neighborhood guardhouse project" ([YouTube CWoGfMMpRFM](https://www.youtube.com/watch?v=CWoGfMMpRFM)), Kai Hendry | CC BY | Exactly the topic (a gated community in Malaysia), but the barrier is filmed hand-held for a few seconds; the rest is a talk. |
| "DRC ANPR" ([YouTube itJGDBdOdZI](https://www.youtube.com/watch?v=itJGDBdOdZI)), Daniel Raines Communications | CC BY | Screen recording of another ANPR product, with its own overlay on top. |
| "ANPR India Detection Demo" ([YouTube dyf5c1iX5Xc](https://www.youtube.com/watch?v=dyf5c1iX5Xc)), SmartCow | CC BY | Already has another system's boxes drawn on it. |
| Pexels 12890469, Mar del Plata traffic (black and white, 4K) | Pexels License | Argentine plates, but traffic jam with constant occlusions; kept as a candidate for a second evaluation clip. |
| Wikimedia Commons | | No video of Argentine traffic with legible plates was found. |

Most YouTube videos about Argentine plate readers (municipal systems, vendor demos) have the standard YouTube license, not Creative Commons, so they were not used.

## Images: Argentine plate benchmark

| Set | Contents | Source | License |
| --- | --- | --- | --- |
| `arg_plate_benchmark` | 147 cropped plates from Argentina with their text: 68 Mercosur, 75 old format, 4 motorcycle plates | [fast-plate-ocr release `arg-plates`](https://github.com/ankandrew/fast-plate-ocr/releases/download/arg-plates/arg_plate_benchmark.zip), by ankandrew | **Not stated.** Used only to measure, never copied into this repo. |

Caveat: this set comes from the same author as the OCR models. It is published as a benchmark, separate from the training set, but it is likely the same kind of images (same cameras, same cropping), so it is a friendly test. Plates cropped by our own detector in a real video are harder; see [EVALUATION.md](EVALUATION.md).

## Models

Downloaded on first use into `~/.cache`, not into this repo.

| Model | Used for | Code license | Weights |
| --- | --- | --- | --- |
| RF-DETR Nano (COCO), ONNX via `open-image-models` | Vehicles | RF-DETR: Apache-2.0 (Roboflow); `open-image-models`: MIT | Apache-2.0 (Roboflow's COCO checkpoints) |
| `yolo-v9-t-640-license-plate-end2end` from `open-image-models` | Plates | MIT | **Not stated.** The model card cites the YOLOv9 paper but does not say which training code was used. The original YOLOv9 code is GPL-3.0; there is also an MIT re-implementation. Check with the author before any commercial use; a possible replacement is to fine-tune RF-DETR Nano (Apache-2.0) as a plate detector on an openly licensed dataset, a couple of hours on a free Colab GPU. |
| `cct-s-v2-global-model` from `fast-plate-ocr` | Reading | MIT | Not stated separately from the package. |
| ByteTrack, from Roboflow `trackers` | Tracking | Apache-2.0 | (no weights) |

Nothing from Ultralytics (AGPL-3.0) is used.

## Access lists

`data/listas/*.csv` are invented for the demo. The plates in them are the ones that appear in the public video, paired with made-up names ("Lote 14"); they do not say anything about the real owners.
