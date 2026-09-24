# datos/ — archivos pesados (no se suben a GitHub)

Esta carpeta está en `.gitignore`. Cada equipo coloca aquí sus propios archivos:

```
datos/
├── videos/                 Grabaciones de sesiones y pruebas (*.mp4)
│   └── 20250327_130515_referencia.mp4   ← video por defecto de los detectores
└── modelos/                Capas y parámetros de los modelos 3D por región
    └── costa_chica/        *.gpkg, parámetros de impresión (*.txt)
```

Los detectores de `escritorio/` abren por defecto `datos/videos/20250327_130515_referencia.mp4`
(se puede elegir cualquier otro video desde la interfaz).
