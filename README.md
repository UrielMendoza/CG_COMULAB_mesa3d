# Mesa 3D — Mapeo participativo con plastilina en tiempo real

Plataforma del proyecto **COMULAB** de **CentroGeo** para mapeo participativo en comunidades. Las personas colocan **plastilina de colores** sobre un modelo de elevación 3D, un mapa impreso o una imagen satelital. Una cámara reconoce cada pieza y la dibuja al instante en un **mapa georreferenciado**. Ese mapa se enriquece con los datos de **OpenStreetMap**, se anota y se exporta.

![Ejemplo de detección: video original, detecciones y máscaras de color](docs/img/ejemplo_1.png)

*Primera versión probada en campo: video original, detecciones clasificadas y máscaras de color. Pronto habrá videos demostrativos.*

---

## Cómo funciona

1. En las 4 esquinas del área de trabajo se colocan **cruces azules**.
2. Se indican las coordenadas de esas esquinas: se elige un área predefinida o se escriben a mano.
3. Con la cámara fija sobre la mesa, se presiona **Calibrar**. El sistema calcula una *homografía* entre las cruces de la imagen y las esquinas reales.
4. La plastilina se reconoce por su color y su forma, y aparece en el mapa en coordenadas geográficas.

| Plastilina | Mapeo libre | Juego |
|---|---|---|
| Amarilla | punto, línea o polígono | — |
| Verde | punto, línea o polígono | punto (la respuesta de cada participante) |
| Cruces azules | calibración (no se mapean) | calibración (no se mapean) |

### Dos modos

- **Mapeo libre.** Solo se dan las esquinas y se empieza a mapear. Lo que aparece en vivo se **guarda** con un toque y se **anota**: nombre, nota y color. También se puede **dibujar a mano** sobre el mapa.
- **Juego.** Una dinámica genérica para cualquier territorio. Se registran los participantes (personas o equipos) y se definen **puntos de control**: a mano sobre el mapa, desde un archivo o como **reto con localidades reales de OpenStreetMap**. Los puntos quedan ocultos. Cada participante los ubica con plastilina verde y el sistema mide la distancia y asigna puntos.

### Dos versiones, el mismo sistema

| | Versión laptop | Versión celular |
|---|---|---|
| Qué se necesita | Laptop + cámara (celular con DroidCam, webcam o capturadora) | **Solo un celular** |
| Detección | Python + OpenCV (`escritorio/`) | OpenCV.js en el navegador (`web/movil/`) |
| Mapa | `web/mapa/` en el navegador de la laptop | El mismo mapa, en la pantalla del celular |
| Instalación | `pip install -r requirements.txt` | Ninguna: se abre una página (se puede instalar como app) |

Las dos versiones usan los **mismos parámetros de color**, la **misma clasificación de formas**, la **misma georreferenciación** (`config/georreferencia.json`) y el **mismo mapa** (`web/comun/`). Con la misma imagen dan los mismos resultados (ver [Validación](#validación)).

---

## Estructura del repositorio

```
├── config/
│   └── georreferencia.json     Áreas predefinidas y orientaciones (Python y web)
├── escritorio/                 Versión laptop (Python)
│   ├── detector_libre.py       ▶ Detector de mapeo libre
│   ├── detector_juego.py       ▶ Detector para el juego
│   ├── georreferencia.py       Presets, orden de cruces y homografía (compartido)
│   ├── herramientas/probar_camara.py
│   └── legacy/                 Primeras versiones (referencia histórica)
├── web/
│   ├── comun/                  Mapa, OpenStreetMap, juego, paneles y estilos (compartido)
│   ├── mapa/                   ▶ Mapa para la versión laptop
│   └── movil/                  ▶ App del celular (cámara + detección + mapa)
├── ejemplos/geojson/           GeoJSON de sesiones anteriores para probar el mapa
├── docs/img/                   Imágenes del README
├── datos/          (local)     Videos y capas pesadas — NO se suben (ver datos/LEEME.md)
│   ├── videos/
│   └── modelos/costa_chica/
└── salidas/        (local)     Lo que escriben los detectores en tiempo real — NO se sube
```

Los videos (`*.mp4`), las capas geográficas (`*.gpkg`, `*.tif`) y las salidas en tiempo real están en `.gitignore`. Cada equipo coloca sus archivos en `datos/` siguiendo [`datos/LEEME.md`](datos/LEEME.md).

---

## Materiales

- Modelo 3D, mapa impreso o imagen satelital del área.
- **4 cruces azules** (papel, cinta o plastilina) en las esquinas, que deben coincidir con las coordenadas configuradas.
- Plastilina **verde** y **amarilla** de colores saturados.
- Soporte o tripié para dejar la cámara **fija, mirando hacia abajo**, con las 4 cruces a la vista.
- Luz pareja, sin sombras duras ni reflejos.

---

## Versión laptop

### Instalación

Requiere Python 3.10 o superior.

```bash
git clone https://github.com/UrielMendoza/CG_COMULAB_mesa3d.git
cd CG_COMULAB_mesa3d
pip install -r requirements.txt
```

> En Windows, si `geopandas` falla con pip, instálalo con conda: `conda install -c conda-forge geopandas`.

### Conectar la cámara del celular (DroidCam)

1. Instala **DroidCam** en el celular y conecta celular y laptop a la **misma red Wi‑Fi**.
2. DroidCam muestra una URL como `http://192.168.1.66:4747/video`. Pruébala:
   ```bash
   python escritorio/herramientas/probar_camara.py http://192.168.1.66:4747/video
   ```
   Con webcam, capturadora o el cliente de DroidCam para PC, usa el índice de la cámara: `python escritorio/herramientas/probar_camara.py 1`.

### Ejecutar

**1. El detector.** Usa uno de los dos:

```bash
python escritorio/detector_libre.py    # Mapeo libre
python escritorio/detector_juego.py    # Juego
```

En la ventana:
- Elige la fuente: archivo, URL (DroidCam/RTSP) o cámara USB.
- Elige el área y la **orientación**.
- Presiona **Iniciar**. Cuando se vean las 4 cruces, presiona **Recalibrar ahora**.

Mientras hay calibración, el detector escribe en `salidas/`:
- `detecciones_puntos.geojson`
- `detecciones_lineas.geojson`
- `detecciones_poligonos.geojson`
- `sesion.json`: modo y área, para que el mapa se sincronice solo.

**2. El mapa.** En otra terminal, desde la **raíz del repositorio**:

```bash
python -m http.server 8000
```

Abre <http://localhost:8000/web/mapa/>. El mapa toma el modo y el área del detector y muestra las piezas en vivo. El servidor local es necesario porque el navegador no deja leer archivos si se abre el HTML con doble clic.

---

## Versión celular (sin laptop)

Todo corre en el navegador del celular: la cámara trasera, la detección, la calibración y el mapa. El video **no se envía a ningún servidor**.

<p>
  <img src="docs/img/movil_libre.jpg" alt="Versión celular: detección calibrada y mapa en vivo" width="260">
  <img src="docs/img/movil_osm.jpg" alt="Versión celular: contexto de OpenStreetMap de una pieza" width="260">
</p>

### Publicarla (una sola vez)

El navegador solo permite usar la cámara en páginas **https://**. Lo más simple es GitHub Pages:

1. En GitHub: **Settings → Pages → Deploy from a branch → `main` / `(root)`** → *Save*.
2. Uno o dos minutos después, la app estará en **`https://urielmendoza.github.io/CG_COMULAB_mesa3d/web/movil/`** (en un fork, cambia el usuario). El mapa de laptop queda en `…/web/mapa/`.
3. Abre la URL en el celular (Chrome en Android, Safari en iPhone) y acepta el permiso de cámara.
4. Opcional: **Agregar a pantalla de inicio** para usarla como app.

> Para desarrollar en la laptop: `python -m http.server 8000` en la raíz y abre <http://localhost:8000/web/movil/>. `localhost` tiene permiso de cámara; una IP de la red local (`http://192.168…`) no.

### Uso

1. Arriba, elige **Libre** o **Juego**.
2. **Panel → Cámara → Área de trabajo**: elige el área (o escribe EPSG y esquinas) y la orientación.
3. **Usar la cámara** y fija el celular sobre la mesa. También puedes usar **Probar con un video**.
4. Cuando el indicador diga **Cruces 4/4**, el botón **Calibrar** se ilumina: presiónalo.
5. La plastilina aparece en el mapa. La barra inferior tiene:
   - **Pausa**: congela las detecciones.
   - **Vista**: cámara + mapa, solo cámara o solo mapa.
   - **Guardar**: pasa lo que está en vivo al mapeo guardado.
   - **Panel**: cámara, mapeo, juego y OSM.

Más detalles:
- **Proyectar**: usa la función de duplicar pantalla del celular (Smart View, Chromecast, AirPlay).
- **Sin internet**: después de la primera visita, la app funciona sin conexión. Los datos OSM de un área ya consultada también quedan guardados en el dispositivo. El mapa base necesita internet; sin él, carga la imagen satelital en **Mapeo → Imagen de fondo**.
- **Rendimiento**: en celulares modestos, baja la resolución de análisis (640 px) o los cuadros por segundo (5).
- **Copia local de OpenCV.js** (opcional): descarga `https://cdn.jsdelivr.net/npm/@techstark/opencv-js@4.10.0-release.1/dist/opencv.js` a `web/movil/vendor/opencv.js`.

---

## El mapa (laptop y celular)

![Mapa: mapeo guardado con nombre, nota y contexto de OpenStreetMap](docs/img/mapa_mapeo_osm.jpg)

- **En vivo**: lo que la cámara ve ahora; cambia cuando se mueve la plastilina.
- **Mapeo guardado**: lo que se decide conservar. Con **Guardar todo** o **Guardar y anotar** (en cada pieza), cada elemento recibe **nombre, nota y color**. Se guarda en el navegador y se exporta a GeoJSON.
- **Dibujo a mano**: la barra de la derecha dibuja puntos, líneas, polígonos, rectángulos y círculos, y también mueve, edita y borra. Lo dibujado entra al mapeo guardado y se anota igual.
- **Capas de apoyo**: carga GeoJSON, KML o GPX, o una **imagen de fondo** que se ajusta a las esquinas del área.
- **Exportar**: el GeoJSON incluye `nombre`, `nota`, `color`, `origen` y el **contexto OSM** de cada elemento (`osm_localidades`, `osm_localidades_km`, `osm_salud`, …).

## OpenStreetMap en el centro

OpenStreetMap (OSM) es el mapa colaborativo del mundo. Además de usarlo como mapa base, la plataforma **consulta su base de datos** (Overpass API) dentro del área de trabajo:

- **Localidades, salud, escuelas y agua** del área, con conteo y leyenda.
- En cada pieza de plastilina, **qué hay cerca según OSM**: localidad, servicio de salud, escuela y cuerpo de agua más cercanos, con su distancia.
- Un **buscador** de lugares del área.
- En el juego, **retos con localidades reales**: el sistema elige N localidades al azar y los participantes las ubican. Cualquier punto OSM se puede usar como punto de control.
- Los datos consultados se guardan en el dispositivo, así que un área ya consultada **funciona sin internet**.

Si algo de la comunidad falta en OSM, se puede [agregar en openstreetmap.org](https://www.openstreetmap.org/fixthemap). Datos © colaboradores de OpenStreetMap, licencia ODbL.

## El juego

![Juego: resultados de un turno con buffers y distancias](docs/img/mapa_juego.jpg)

1. **Participantes**: escribe los nombres (personas o equipos) y presiona Agregar.
2. **Puntos de control**:
   - **Colocar a mano**: toca el mapa. Si hay datos OSM, se sugiere el nombre de la localidad cercana.
   - **Desde archivo**: GeoJSON, KML o CSV con columnas `nombre, lat, lon`.
   - **Reto con OpenStreetMap**: N localidades reales del área al azar.

   Quedan ocultos hasta presionar **Mostrar**. Con **Exportar** se reutilizan en otra sesión.
3. **Radio de acierto** en km.
4. En su turno, el participante coloca plastilina verde donde cree que está cada lugar. Al presionar **Verificar turno**, cada punto se compara con la pieza más cercana:
   - dentro del radio: 50–150 puntos;
   - hasta el doble del radio: 10–40 puntos;
   - fuera: casi nada.
5. **Siguiente** pasa el turno y **Marcador** muestra la tabla.

---

## Georreferenciación

Las áreas están en [`config/georreferencia.json`](config/georreferencia.json). Las usan los detectores de Python y la web; agrega ahí las tuyas.

| Área | EPSG | xmin | ymin | xmax | ymax |
|---|---|---|---|---|---|
| Guerrero · Costa Chica | 6369 | 436770.3242 | 1832196.0532 | 506936.9275 | 1892877.8394 |
| Cuenca del Valle de México · Sentinel-2 | 32614 | 409907 | 2074280 | 612270 | 2184872 |
| Cuenca del Valle de México · modelo 3D | 32614 | 416316.969 | 2079317.310 | 617400.705 | 2256323.915 |

La web reconoce sin conexión las zonas UTM 11N a 16N (WGS84: 32611–32616; ITRF2008: 6366–6371). Cualquier otro EPSG lo obtiene de epsg.io.

**Orientación.** Indica a qué esquina del mapa corresponde cada cruz, según cómo esté la cámara: *Norte arriba*, *a la derecha*, *abajo*, *a la izquierda* o *Espejo*. **Prueba rápida**: pon una pieza junto a la cruz de arriba a la izquierda; debe aparecer en esa esquina del mapa.

## Ajuste de colores (HSV)

- El tono (H) va de 0 a 179: amarillo ≈ 15–30, verde ≈ 35–85, azul ≈ 85–135.
- Si la impresión tiene zonas del mismo color, **sube S bajo**: la plastilina es más saturada que el papel.
- Si no se ven las 4 cruces, revisa la **máscara azul** y amplía H o baja S y V.
- Las áreas mínimas están en píxeles del cuadro analizado. Si cambias la resolución, ajústalas.

## Validación

- **Celular vs. Python**: la detección de la app del celular se comparó en Chrome headless contra las funciones originales de Python, con una escena sintética (cruces en perspectiva; puntos, líneas y polígonos amarillos y verdes). Mismo tipo y color en todas las detecciones; coordenadas a < 0.1 m.
- **Reorganización de Python**: los detectores reorganizados dan resultados **idénticos** a las versiones anteriores (diferencia 0.0).
- **Flujos completos**, con cámara simulada y el detector de Python escribiendo en `salidas/`, sin errores:
  - sincronización del mapa;
  - consulta real a OpenStreetMap (Costa Chica: 305 localidades, 110 escuelas, 8 servicios de salud, 8 cuerpos de agua);
  - guardado y anotación;
  - dibujo;
  - juego con reto OSM y puntaje.

## Problemas conocidos

- **Orientación del detector del juego (laptop).** La versión anterior de `detector_juego.py` asociaba las cruces con un mapeo **en espejo** por un intercambio de variables al ordenar las esquinas. Para no cambiar lo que ya se probó en campo, ese detector arranca en *Espejo (transpuesta)*. Con una cámara que no invierte la imagen, lo correcto es *Norte arriba*; haz la prueba rápida y ajústalo en la ventana.
- Las áreas mínimas y máximas dependen de la resolución y la altura de la cámara; ajústalas en cada montaje.
- Los servidores públicos de OpenStreetMap limitan las consultas seguidas. Si falla, espera un minuto; la app prueba otros servidores.

## Créditos

Proyecto COMULAB — CentroGeo. Desarrollo: Uriel Mendoza, Luis Alejandro Rivera y colaboradores.
Mapa base y datos geográficos © colaboradores de [OpenStreetMap](https://www.openstreetmap.org/copyright).

Licencia [MIT](LICENSE).
