# License Plate Demo
### Lectura de patentes y control de accesos vehiculares

Un sistema pensado para la portería de un barrio o de una planta. Sigue cada vehículo que cruza, lee su patente en varios cuadros y la compara con listas de residentes, visitas y proveedores. Registra cada ingreso y egreso con hora y foto, y en todo momento sabe quién sigue adentro. Funciona sobre video grabado o en vivo, sobre la señal de un grabador de cámaras.

**Tomás González** · Proyecto individual · noviembre de 2026

[Cómo se evaluó](docs/EVALUATION.md) · [Modo en vivo](docs/LIVE.md) · [Datos y licencias](docs/DATA.md) · [Read in English](README.en.md)

**Resultado:** en una calle argentina filmada en 4K, detectó los 7 vehículos que cruzaron la portería, sin cruces falsos, y les asignó el estado correcto a los 7. En vivo, en una notebook sin placa de video, registró bien los autos que frenan en una barrera de día y entre 4 y 6 de 6 patentes legibles de noche.

<img src="demo/demo.gif" alt="Animación: cada vehículo tiene una caja, su patente en grande y un color según su estado; un panel al costado muestra los últimos eventos y quién sigue adentro" width="900">

<sub>Video: Santiago Balbuena, Pexels (licencia Pexels), con anotaciones agregadas. Los nombres de las listas son inventados.</sub>

## Recorrido

```text
 video / RTSP ──► RF-DETR ──► ByteTrack ──► detector de ──► fast-plate-ocr ──► votación entre cuadros
  (se guarda el   (vehículos  (un ID por    patentes (en    (texto + confianza   (por carácter,
   4K para la     en 1280 px) vehículo)     el recorte en   por carácter)          pesada por tamaño)
   patente)                                  4K del auto)                                │
   annotated.mp4 ◄── registro: quién sigue ◄── línea: entra / sale ◄── listas de acceso ◄┘
   events.csv          adentro (estado inicial    (cambio de lado,          (validación de formato,
   recortes/           + ingresos − egresos;       confirmado, estabilizado  comparación tolerante:
   resumen.txt         avisa salidas sin ingreso)  ante el movimiento)       O/0, I/1, B/8...)
```

### 1. Detectar y seguir cada vehículo

[RF-DETR](https://github.com/roboflow/rf-detr) Nano (Apache-2.0, pesos de COCO, en ONNX) busca vehículos en una copia de 1280 px de cada cuadro, y ByteTrack, de [trackers](https://github.com/roboflow/trackers) de Roboflow, le da a cada uno un ID estable. Son los mismos modelos de mi [demo de conteo](https://github.com/TomaasGonzalezzz/Object-Counting-Demo).

### 2. Encontrar y leer la patente

La patente se busca en el recorte del vehículo **en resolución completa**, con el detector de [open-image-models](https://github.com/ankandrew/open-image-models), y se lee con [fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr). Sobre 147 patentes argentinas recortadas de un set aparte, el lector acierta **140 (95,2 %)**: 68 de 68 Mercosur y 72 de 79 del formato viejo y de moto.

### 3. Votar entre cuadros y validar el formato

Cada patente se lee en muchos cuadros. Cada lectura pesa según su confianza al cuadrado y el tamaño de la patente, y las 12 mejores votan carácter por carácter: una lectura nítida de cerca le gana a varias borrosas de lejos. Después se valida el formato (autos `ABC 123` y `AB 123 CD`, motos `123 ABC` y `A 123 BCD`) y se corrigen por posición las confusiones típicas, como una `O` leída donde va un número. Otros países se configuran con `--format "LLL NNNN"`.

### 4. Comparar con las listas

La comparación tolera errores: cambiar dos caracteres parecidos (`O`/`0`, `I`/`1`, `B`/`8`) cuesta 0,25 en vez de 1, y hay coincidencia si la distancia total es 1 o menos. Así, una patente con un carácter mal leído igual queda vinculada a la persona correcta. Las visitas que llegan fuera de su horario se marcan como alerta.

<img src="figures/eventos.jpg" alt="Seis eventos del video: el recorte del vehículo, el de la patente, la lectura final y el estado que le asignó el sistema" width="760">

### 5. Decidir si entró o salió, y llevar el registro

El sentido sale del lado hacia el que la patente cruza la línea de la portería. Para evitar cruces falsos, el cruce se confirma en dos cuadros y 20 px pasada la línea, y si la caja de un vehículo salta de golpe (dos autos pegados, un acoplado que aparece y desaparece) se empieza de cero. `--stabilize` sigue los pequeños movimientos de la cámara para que la línea quede pegada a la calle. Cada ingreso suma al registro, cada egreso saca la patente más parecida, y el estado final se guarda para la próxima corrida.

### 6. Medirlo contra un conteo hecho a mano

Clip principal: una calle residencial argentina, 4K, cámara fija, 58 s, siete vehículos que cruzan la línea. Leí las patentes a mano en recortes ampliados **antes** de correr el modelo.

| | Resultado |
| --- | :---: |
| Cruces de la portería detectados | **7 de 7**, ninguno falso |
| Estado correcto (residente / visita / proveedor / desconocido / no leída) | **7 de 7** |
| Patentes legibles leídas exactas | 3 de 5 (30 de 32 caracteres) |
| Patentes con un carácter errado pero bien vinculadas a la lista | 2 (`GIE 291` por `GEE 291`, `HWE 273` por `HME 273`) |
| Patentes ilegibles (moto de frente, auto oscuro a contraluz) | 2 de 2 informadas como `no leída`, ninguna inventada |
| Velocidad (notebook de bajo consumo, i7-10510U, video 4K, 1 de cada 2 cuadros) | 1,1 cuadros/s |

**Es un solo clip con siete vehículos, y las reglas de seguimiento se ajustaron mirándolo.** La primera corrida, sin tocar nada, acertó 3 de 7 estados y ninguna patente completa; cada cambio y su efecto están en [EVALUATION.md](docs/EVALUATION.md).

### 7. Llevarlo a tiempo real

A 1,1 cuadros por segundo sirve para video grabado, no para una portería. El modo `--live` saca el detector de vehículos y busca la patente directamente en una zona cerca de la barrera; si no se mueve nada, no analiza, y siempre toma el cuadro más nuevo, descartando los que no llega a procesar, como pasa con una cámara. Cada evento se escribe en el momento.

Lo probé con videos públicos de porterías y barreras, pasando cada uno tres veces a su velocidad real en la misma notebook. Los resultados cambian entre corridas según qué cuadros alcanza a tomar, así que doy el rango:

| Video | Estado correcto | Cuadros analizados por segundo |
| --- | :---: | :---: |
| Barrera de estacionamiento, de día (los autos frenan) | 2 de 2 en todas las corridas | 2,6 a 7,2 |
| Noche con infrarrojo, autos esperando en el portón | 4 a 6 de 6 patentes legibles | 3,5 a 7,7 |
| Camión llegando al portón de una planta | 1 de 1 en todas las corridas | 4,4 a 7,5 |
| Calle argentina en 1080p (cámara general, patentes chicas) | 0 a 2 de 5 patentes legibles | 3,3 a 5,6 |

Con una cámara ubicada para leer patentes y autos que frenan, funciona en tiempo real en una notebook, con alguna pérdida ocasional de noche. Una cámara general de calle no alcanza. Comandos, cambios y qué no ve: [docs/LIVE.md](docs/LIVE.md).

## Conclusión

El sistema funciona de punta a punta con patentes argentinas reales y en tiempo real en una computadora común. Lo que más pesa en el resultado no es el modelo sino la cámara: con patentes de más de 80 px y un auto que frena, las lecturas son firmes; con patentes chicas o de noche con poca resolución, aparecen las pérdidas. Antes de prometer un número en una instalación real, hay que medirlo con la cámara del lugar.

## Cómo correrlo

Windows, macOS o Linux, Python 3.11 o más nuevo, unos 400 MB de dependencias (sin PyTorch).

```sh
git clone https://github.com/TomaasGonzalezzz/License-Plate-Demo && cd License-Plate-Demo
uv sync                      # o: python -m venv .venv && .venv\Scripts\pip install -e .
curl -L -o pexels-38118760.mp4 https://www.pexels.com/download/video/38118760/

# video grabado, con la línea de la portería
uv run python -m platedemo pexels-38118760.mp4 --line 0,480,1280,480 --flip --stabilize \
  --lists data/listas --inside data/listas/estado_inicial.csv --clock 10:15:00 --out out

# en vivo, sobre un grabador: un evento cuando la patente se lee dentro de la zona de la barrera
uv run python -m platedemo "rtsp://usuario:clave@192.168.0.20:554/..." --live --trigger zona --lane entra \
  --read-zone 100,250,1200,640 --lists data/listas_entrada --show --out out

uv run pytest -q
```

Los pesos de los modelos (unos 40 MB) se descargan en la primera corrida. `--device dml` usa cualquier placa de video DirectX 12 en Windows a través de ONNX Runtime DirectML y vuelve a la CPU si falla. Para comparar una corrida con la verdad de referencia: `uv run python -m platedemo.evaluate events out/events.csv evaluation/ground_truth/porteria_pexels.csv --lists data/listas`.

| Salida | Contenido |
| --- | --- |
| `annotated.mp4` / `live.mp4` | Cajas, la patente en grande con el color de su estado, la línea o la zona, los últimos eventos y quién sigue adentro |
| `events.csv` | hora, ID, patente, confianza, estado, sentido, nombre en la lista, nota, cantidad de lecturas, recortes |
| `alertas.csv` | desconocidos, no leídas, visitas fuera de horario y salidas sin ingreso (modo en vivo) |
| `recortes/` | el mejor recorte de patente (y de vehículo) por evento |
| `resumen.txt`, `estado_final.csv` | resumen al cierre y quién sigue adentro, para usar como `--inside` la próxima vez |

## Limitaciones

- **Pocos videos, ajustados sobre ellos.** Los números muestran que el sistema funciona; no son una tasa de acierto general.
- **El tamaño de la patente decide casi todo.** Por debajo de unos 80 px de ancho, los errores suben rápido. Una cámara dedicada a patentes (de frente, a 3 a 8 m, con infrarrojo de noche) pesa más que cualquier cambio de modelo.
- **Lo que el modo en vivo no ve:** un vehículo sin patente legible no genera evento, porque no hay patente para seguir. Correr el detector de vehículos cada tantos cuadros, o un sensor de presencia en la barrera, lo resolvería.
- **Los pesos del detector de patentes no declaran licencia** (ver [DATA.md](docs/DATA.md)). El código es MIT; hay que confirmarlos o reentrenar antes de cualquier uso comercial.
- **Datos personales.** Una patente asociable a una persona es un dato personal (Ley 25.326). Una instalación real necesita cartel de zona videovigilada, retención corta y acceso restringido. No se usa reconocimiento facial ni hace falta.

## Estructura

```text
src/platedemo/     formatos, comparación con listas, votación, línea y registro, estabilización,
                   modelos, pipeline grabado, modo en vivo, dibujo, video, evaluación, línea de comandos
data/              listas inventadas y estados iniciales (una carpeta por video de prueba)
evaluation/        verdad de referencia hecha a mano y configuración de cada video
results/           salidas guardadas de la corrida final sobre el video grabado
demo/              demo.mp4 y demo.gif
figures/           imágenes del README
docs/              EVALUATION.md, LIVE.md y DATA.md (en inglés)
tests/             formatos, comparación tolerante, votación, entradas y salidas, estabilización, seguimiento en vivo
```

Licencia MIT. Los videos y los datasets no están incluidos en el repositorio; las fuentes y sus licencias están en [docs/DATA.md](docs/DATA.md).
