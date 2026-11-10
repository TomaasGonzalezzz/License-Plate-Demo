# Live mode (real time)

`--live` is the version meant for a camera at a gate: it keeps up with the video as it arrives and writes each event the moment it happens. Tested on three clips played at their real speed, as if they were the camera, on a low-power laptop CPU (Intel i7-10510U, 15 W, no GPU).

```sh
# barrier where cars stop: an event when the plate is read inside the zone
python -m platedemo video.mp4 --live --trigger zona --lane entra --read-zone 100,250,1200,640 --lists data/listas_entrada --out out

# gate line: in or out depending on the side the plate crosses to
python -m platedemo video.mp4 --live --line 0,500,1280,500 --flip --read-zone 0,300,1280,720 --lists data/listas --out out

# a recorder's camera (Hikvision example); --show opens a window with the annotated image
python -m platedemo "rtsp://user:pass@192.168.0.20:554/Streaming/Channels/101" --live --trigger zona --read-zone ... --show --out out
```

Each event is printed, appended to `events.csv` and, if it is unknown or unread, to `alertas.csv`. `live_run.json` has the speed figures; `plates.csv` every plate box and reading.

## What changes compared with the offline pipeline

| | Offline (`python -m platedemo ...`) | Live (`--live`) |
| --- | --- | --- |
| Vehicle detector (RF-DETR) | every analysed frame | **not used** (it was 0.5 to 1 s per frame) |
| Plate detector | on each vehicle crop | directly on a zone near the gate, full resolution |
| Frames | 1 of every N, all of them | the newest one each time; the rest are dropped, as with a camera |
| Nothing moving | still processed | skipped after a 1 ms check |
| Events | written at the end | written the moment the plate is decided |
| A vehicle whose plate is never found | reported as "no leída" | **not reported** (no plate box, nothing to follow) |

Two ways to decide the event:

- **`--trigger linea`**: the plate centre crossing a line, direction from the side. For a road where cars drive through.
- **`--trigger zona`**: the first firm reading of a plate that moved into the zone. One lane, one direction (`--lane entra` or `sale`). For a barrier, where cars slow down or stop in front of the camera: this is the typical gated-community setup.

Guards learned on these clips: with a line, a plate has to move 40 px before it counts, so parked cars never trigger (with a zone, draw the zone over the lane only); the same plate in the same direction within 20 s is one passage, not two (the tracker sometimes loses a plate for a moment); a scene cut or a moved camera closes everything being followed.

## Results

Each clip was played at its real speed **three times**, with nothing else running on the laptop. Results change between runs: which frames the loop manages to take depends on timing, and with few readings a plate (above all at night) is sometimes read firmly and sometimes not. The table gives the range.

| Clip | Camera | Vehicles | Logged | Plates right | Analysed frames per second | Kept up |
| --- | --- | :---: | :---: | :---: | :---: | :---: |
| **Entrance, day and night** (US) | LPR camera in front of a lane, cars slow down | 5 | 4 to 5 | **3 to 4 of 5** (one is a two-line plate, read `R824` for `RN 824`) | 6.5 to 11 | yes, 3 of 3 |
| **Exit from behind, day and night** (Canada) | rear view, a pickup leaving twice | 2 | 1 to 2 | **1 to 2 of 2** (the night pass is the one that can be missed) | 6.2 to 6.6 | yes, 3 of 3 |
| **Argentine street** (Pexels, downscaled to 1080p, line mode) | general street camera, plates 50 to 90 px | 7 | 1 to 4 | **0 to 2 of 5** legible | 3.3 to 5.6 | yes, 3 of 3 |

Plates were read by eye from enlarged crops; the lists are invented. Speed: with the plate detector at about 150 ms and the reading at about 50 ms per plate, the laptop analyses 3 to 11 frames per second while vehicles are in view and almost nothing when the scene is still. A gate needs 3 to 5. Events are written a median of 0.3 to 1.3 seconds after the plate is first read.

An earlier round of repeats, run while video encoding was using the CPU in the background, dropped to about 1 analysed frame per second and missed most vehicles: the live mode needs the machine for itself.

### What this says

- **With a camera placed for plates, it works in real time on a laptop**, day and night, with occasional misses. The misses are timing: with 6 or 7 analysed frames per second, a car that stays briefly may give only one or two readings. A faster machine (the desktop with a discrete GPU, a mini PC with a recent CPU), a higher `--min-confidence` margin, or reading at two frames per decision would reduce them. A presence sensor at the barrier is the safety net for anything missed.
- **A general street camera is not enough.** On the Argentine clip downscaled to 1080p the plates are 50 to 90 px; the live mode reads 0 to 2 of 5, against 3 of 5 (and 7 of 7 statuses) for the offline pipeline on the original 4K. Same models: the difference is resolution and timing.
- **What the live mode does not see.** In the Argentine clip a motorcycle (no front plate), a dark car against the light and the Fiat Palio (its old black plate is seen only briefly) produce no event live. The offline pipeline, with the vehicle detector, reports them as "no leída". For a real deployment that matters: running the vehicle detector every few frames, or a presence sensor at the barrier, would catch vehicles without a readable plate.

## Second round: clips where vehicles stop (barrier style)

At a gated community the car stops for a few seconds in front of the guard. That is much easier than a street: the plate stays in view, big and still. Three more clips were chosen for that situation and run with `--trigger zona`:

```sh
python -m platedemo barrera_dia.mp4   --live --trigger zona --lane entra --format "LLL NNNN"  --lists data/listas_barrera --clock 11:20:00 --save-video --out out
python -m platedemo noche_ir.mp4      --live --trigger zona --lane entra --format "N LLL NNN" --lists data/listas_noche   --clock 20:31:00 --min-live-plate-width 55 --plate-threshold 0.15 --save-video --out out
python -m platedemo camion_porton.mp4 --live --trigger zona --lane entra --format "LLL NNNN"  --lists data/listas_camion  --clock 07:45:00 --cut-threshold 0 --save-video --out out
```

| Clip | Situation | Vehicles | Right status, per run | Analysed frames/s |
| --- | --- | :---: | :---: | :---: |
| **Barrier, day** (Malaysia, 22 s) | two cars stop at a parking barrier; hand-held phone, the view moves a little | 2 | **2 of 2** in every run; one early run added a false extra event (a misread of the same car) | 2.6 to 7.2 |
| **Night, infrared** (California, 49 s, 720p at 10 fps) | cars arrive and wait 5 to 15 s; plates glow under the IR light | 8 (6 legible, 1 doubtful, 1 never legible) | **4 to 6 of 6** legible plates; sometimes the blurry one is logged twice | 3.5 to 7.7 |
| **Truck at a plant gate** (promo clip, 7.5 s) | a truck drives up; the plate is small at first, big at the end; edited with zooms | 1 | **1 of 1** in every run | 4.4 to 7.5 |

Ground truth: `evaluation/ground_truth/live_casos.csv`, read by eye from enlarged crops. The lists are invented (a visit at night arrives outside its hours on purpose, to show the alert). Per-country formats are passed with `--format`, so look-alike characters are fixed by position (California `N LLL NNN`, Malaysia `LLL NNNN`). The clips were trimmed to the part with traffic; the settings above were tuned on these same clips, so they are a demonstration, not a blind test.

What changed in the code for this round (and why). The first-round table above was measured before these changes.

- **Motion gate.** It used the average change of the zone; a car far away in a wide view barely moves the average, so the second car at the barrier was never looked at. Now a small patch that changes clearly also counts, and plates are searched at least every 0.5 s anyway.
- **A car waiting at the barrier keeps its track for 3 s** without a detection (1.5 s with a line), so it is not logged twice; but a plate already decided is dropped after 1.5 s and does not stretch its reach, so the next car stopping on the same spot is not absorbed by it.
- **"No leída" waits.** A plate that disappears unread is logged only if no reading of the same car shows up nearby a moment later.
- **`--cut-threshold 0`** for edited footage with zooms (the truck clip), and **`--plate-threshold`** to let the plate detector report weaker boxes at night (IR-bloomed plates score 0.2 to 0.3).
- **The recorded video** only draws plates seen in the last 0.4 s (a track kept alive is not drawn where the car no longer is).

The night clip is the weak spot: compressed 720p at 10 fps, plates blown out by the infrared light. The misses grow when the laptop is busy with something else (one run at 3.5 analysed frames per second missed two cars). A dedicated plate camera with a short shutter and a machine that is not shared would do better; that has to be measured on site.

## Material

Apart from the Argentine Pexels clip described in [DATA.md](DATA.md), the live-mode tests use public YouTube videos with the standard YouTube license: "CCTV Camera World / Avalonix: Real Life Video Demo - Day & Night", "DeltaTech LPR day/night", and the videos `oJ1sAD7IoNs`, `Roc4YDz6kjo` (Lewis Surveillance) and `xSYAfvnfUg4`, trimmed to the part with traffic. They were only used for local testing: neither the videos, nor frames, nor crops from them are included in this repository. Only the hand-made ground truth (`evaluation/ground_truth/live_casos.csv`) and the invented lists are.
