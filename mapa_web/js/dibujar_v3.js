// ==========================
// 1. Funciones de dibujo
// ==========================

// Función auxiliar para obtener el tipo de figura
function getLayerTypeName(layer) {
    if (layer instanceof L.Marker) return "Punto";
    if (layer instanceof L.CircleMarker) return "Círculo";
    if (layer instanceof L.Polygon) return "Polígono";
    if (layer instanceof L.Polyline) return "Línea";
    return "Figura"; // Este caso no debería ocurrir, lo incluyo para verificar errores en los nombres. 
}

// Función principal para dibujar figuras
map.on(L.Draw.Event.CREATED, function(event) {
    var layer = event.layer;
    var geojson = layer.toGeoJSON();
    var wkt = Terraformer.WKT.convert(geojson.geometry);

    var name = getLayerTypeName(layer);
    var attributes = { 
        nombre: null,  
        descripcion: '' 
    };

    if (layer instanceof L.Polygon) {
        var latlngs = layer.getLatLngs();
        var area = L.GeometryUtil.geodesicArea(latlngs[0]);
        attributes.area = L.GeometryUtil.readableArea(area, true);
    } else if (layer instanceof L.Polyline) {
        var latlngs = layer.getLatLngs(), distance = 0;
        for (var i = 0; i < latlngs.length - 1; i++) {
            distance += latlngs[i].distanceTo(latlngs[i + 1]);
        }
        attributes.distance = _round(distance, 2);
    }

    if (layer instanceof L.CircleMarker) {
        var center = layer.getLatLng();
        var radius = layer.getRadius();
        geojson = createCircularPolygon(center, radius);
        wkt = Terraformer.WKT.convert(geojson.geometry);
        attributes.radius = _round(radius, 2);
    }

    geojson.properties = { ...geojson.properties, ...attributes };
    layer.feature = geojson;

    var content = getPopupContent(layer, geojson, wkt, name);
    if (content !== null) {
        layer.bindPopup(content);
    }

    drawnItems.addLayer(layer);
});

// Función para editar figuras
map.on(L.Draw.Event.EDITED, function(event) {
    var layers = event.layers;
    layers.eachLayer(function(layer) {
        if (layer instanceof L.CircleMarker) {
            var radius = layer.getRadius();
            var center = layer.getLatLng();

            var geojsonPolygon = createCircularPolygon(center, radius);
            var wkt = Terraformer.WKT.convert(geojsonPolygon.geometry);

            geojsonPolygon.properties = { 
                ...layer.feature.properties,
                radius: _round(radius, 2)  
            };
            layer.feature = geojsonPolygon;

            // Actualizar popup con los nuevos datos
            var name = getLayerTypeName(layer);
            var content = getPopupContent(layer, geojsonPolygon, wkt, name);
            if (content !== null) {
                layer.setPopupContent(content);
            }
        } else {
            // Manejo normal para otras figuras
            var geojson = layer.toGeoJSON();
            var wkt = Terraformer.WKT.convert(geojson.geometry);
            if (layer.feature && layer.feature.properties) {
                geojson.properties = { ...geojson.properties, ...layer.feature.properties };
            }
            layer.feature = geojson;
            var name = getLayerTypeName(layer);
            var content = getPopupContent(layer, geojson, wkt, name);
            if (content !== null) {
                layer.setPopupContent(content);
            }
        }
    });
});

// ==========================
// 2. Funciones complementarias
// ==========================

// Función para convertir el marcador de círculo en polígono (El circleMarker original de leaflet.draw, o en general de leaflet, es muy limitado para aplicaciones sociales, sobre todo en gestión de riesgos)
function createCircularPolygon(center, radius) {
    var numPoints = 36, coordinates = [];
    for (var i = 0; i < numPoints; i++) {
        var angle = (i * 360) / numPoints;
        var point = L.latLng(
            center.lat + (radius / 6378137) * Math.sin(angle * Math.PI / 180) * (180 / Math.PI),
            center.lng + (radius / 6378137) * Math.cos(angle * Math.PI / 180) * (180 / Math.PI) / Math.cos(center.lat * Math.PI / 180)
        );
        coordinates.push([point.lng, point.lat]);
    }
    coordinates.push(coordinates[0]);
    return {
        type: "Feature",
        geometry: {
            type: "Polygon",
            coordinates: [coordinates]
        },
        properties: {}
    };
}

var _round = function(num, len) {
    return Math.round(num * (Math.pow(10, len))) / (Math.pow(10, len));
};

var strLatLng = function(latlng) {
    return "(" + _round(latlng.lat, 6) + ", " + _round(latlng.lng, 6) + ")";
};

// ==========================
// 3. Funciones para el popup
// ==========================
var getPopupContent = function(layer, geojson, wkt, name) {
    let content = "";

    // Nombre y descripción 
    let nombre = geojson.properties?.nombre || null; 
    let descripcion = geojson.properties?.descripcion || '';
    content += `<strong>Nombre:</strong> <span id="nombre-display">${nombre === null ? '' : nombre}</span><br>`;
    content += `<strong>Descripción:</strong> <span id="descripcion-display">${descripcion}</span><br>`;
    // Información específica del tipo de figura
    if (layer instanceof L.CircleMarker) {
        var center = layer.getLatLng();
        var radius = layer.getRadius();
        content += "<strong>Centro: </strong>" + strLatLng(center) + "<br><strong>Radio: </strong>" + _round(radius, 2) + " m<br>";
    } else if (layer instanceof L.Polygon) {
        var latlngs = layer.getLatLngs();
        var area = L.GeometryUtil.geodesicArea(latlngs[0]);
        content += "<strong>Área: </strong>" + L.GeometryUtil.readableArea(area, true) + "<br>";
    } else if (layer instanceof L.Polyline) {
        var latlngs = layer.getLatLngs(), distance = 0;
        for (var i = 0; i < latlngs.length - 1; i++) {
            distance += latlngs[i].distanceTo(latlngs[i + 1]);
        }
        content += "<strong>Distancia: </strong>" + _round(distance, 2) + " m<br>";
    }

    // Botón de edición
    content += `<button 
        onclick='openNombreModal(${L.stamp(layer)})'
        onmouseover='this.style.backgroundColor="rgb(150, 150, 150)"' 
        onmouseout='this.style.backgroundColor="rgb(169, 169, 169)"' 
        onmousedown='this.style.transform="scale(0.95)"' 
        onmouseup='this.style.transform="scale(1)"' 
        style='padding: 8px; background-color: rgb(169, 169, 169); color: white; border: none; border-radius: 4px; cursor: pointer; transition: all 0.2s ease;'>
        <i class="fa fa-edit" aria-hidden="true" style="margin-right: 6px;"></i>Editar
    </button><br>
    <hr style="border: none; border-top: 1px solid #ddd; margin: 12px 0;">`;

    // Contenedor WKT
    content += "<strong>Figura en WKT:</strong><br>";
    content += `<div class="wkt-container" id="wkt-container" style="max-height: 100px; overflow-y: auto; background-color: #f5f5f5; padding: 8px; border-radius: 4px; border: 1px solid #ddd;">${wkt}</div><br>`;

    content += `<button 
        onclick='copyWKTtoClipboard()'
        onmouseover='this.style.backgroundColor="rgb(150, 150, 150)"' 
        onmouseout='this.style.backgroundColor="rgb(169, 169, 169)"' 
        onmousedown='this.style.transform="scale(0.95)"' 
        onmouseup='this.style.transform="scale(1)"' 
        style='padding: 8px; background-color: rgb(169, 169, 169); color: white; border: none; border-radius: 4px; cursor: pointer; transition: all 0.2s ease;'>
        <i class="fa fa-copy" aria-hidden="true" style="margin-right: 6px;"></i>Copiar
    </button>`;
    let whatsappText = wkt;
    if (nombre !== null || descripcion !== '') {
        whatsappText = `*Nombre:* ${nombre !== null ? nombre : 'Sin nombre'}\n` +
                    `*Descripción:* ${descripcion}\n` +
                    `*WKT:*\n\n${wkt}`;
    }
    content += `<a href="https://wa.me/?text=${encodeURIComponent(whatsappText)}" target="_blank" style="text-decoration: none; margin-left: 10px;">
        <button 
            onmouseover='this.style.backgroundColor="rgb(150, 150, 150)"' 
            onmouseout='this.style.backgroundColor="rgb(169, 169, 169)"' 
            onmousedown='this.style.transform="scale(0.95)"' 
            onmouseup='this.style.transform="scale(1)"' 
            style="padding: 8px; background-color: rgb(169, 169, 169); color: white; border: none; border-radius: 4px; cursor: pointer; transition: all 0.2s ease;">
            <i class="fab fa-whatsapp" aria-hidden="true" style="margin-right: 6px;"></i>Compartir
        </button>
    </a>`;

    content += `<p style="font-size: 12px;">Para validar la figura, copia y pega el WKT en <a href="https://wktmap.com/" target="_blank">https://wktmap.com/</a></p> <hr style="border: none; border-top: 1px solid #ddd; margin: 12px 0;">`;

    // Sección de descarga
    content += `<div style="margin-top: 10px;"><strong>Descargar:</strong><br><br><div style="display: flex; gap: 10px;">`;
    
    // Se usa el nombre de las propiedades si existe y no es "Sin nombre", de lo contrario se usa el tipo de figura
    const displayName = (nombre !== null) ? nombre : name; 
    const safeName = displayName.replace(/"/g, '\\"');

    content += `<button 
        onclick='downloadGeoJSON(${JSON.stringify(geojson)}, "${safeName}")'
        onmouseover='this.style.backgroundColor="rgb(30, 140, 80)"' 
        onmouseout='this.style.backgroundColor="rgb(40, 167, 97)"' 
        onmousedown='this.style.transform="scale(0.95)"' 
        onmouseup='this.style.transform="scale(1)"' 
        style='padding: 8px; background-color: rgb(40, 167, 97); color: white; border: none; cursor: pointer; border-radius: 4px; transition: all 0.2s ease;'>
        <i class="fa fa-download" aria-hidden="true" style="margin-right: 6px;"></i>GeoJSON
    </button>`;
    content += `<button 
        onclick='downloadKML(${JSON.stringify(wkt)}, "${safeName}")'
        onmouseover='this.style.backgroundColor="rgb(0, 100, 220)"' 
        onmouseout='this.style.backgroundColor="rgb(0, 119, 255)"' 
        onmousedown='this.style.transform="scale(0.95)"' 
        onmouseup='this.style.transform="scale(1)"' 
        style='padding: 8px; background-color: rgb(0, 119, 255); color: white; border: none; cursor: pointer; border-radius: 4px; transition: all 0.2s ease;'>
        <i class="fa fa-download" aria-hidden="true" style="margin-right: 6px;"></i>KML
    </button>`;
   content += `<button 
        onclick='convertWKTtoCSV(${JSON.stringify(wkt)}, "${safeName}")'
        onmouseover='this.style.backgroundColor="rgb(120, 0, 255)"' 
        onmouseout='this.style.backgroundColor="rgb(138, 43, 226)"' 
        onmousedown='this.style.transform="scale(0.95)"' 
        onmouseup='this.style.transform="scale(1)"' 
        style='padding: 8px; background-color: rgb(138, 43, 226); color: white; border: none; cursor: pointer; border-radius: 4px; transition: all 0.2s ease;'>
        <i class="fa fa-download" aria-hidden="true" style="margin-right: 6px;"></i>CSV
    </button>`;

    content += `</div></div>`;

    return content;
};

// ==========================
// 4. Modal dinámico para editar nombre y descripción
// ==========================
function handleEscapeKey(e) {
    if (e.key === 'Escape') closeNombreModal();
}

// Función de la modal para editar figuras
function openNombreModal(layerId) {
    const layer = drawnItems.getLayer(layerId);
    if (!layer) {
        console.error("Capa no encontrada con ID:", layerId);
        return;
    }

    const props = layer.feature.properties || {};
    const currentNombre = props.nombre || "";
    const currentDescripcion = props.descripcion || "";
    const currentColor = layer.options.color || "#3388ff";
    const currentFillOpacity = layer.options.fillOpacity ?? 0.5;
    const currentWeight = layer.options.weight ?? 2;
    const isRegularMarker = layer instanceof L.Marker && !(layer instanceof L.CircleMarker);
    const isCircleMarker = layer instanceof L.CircleMarker;
    const isPolyline = layer instanceof L.Polyline && !(layer instanceof L.Polygon);
    const isPolygon = layer instanceof L.Polygon;

    const colorOptions = [
        '#ff0000', '#00cc00', '#0000ff', '#ffff00', '#ff00ff', '#00ffff',
        '#a52a2a', '#ff9900', '#3388ff', '#000000', '#ffffff'
    ];

    const isCustomColor = !colorOptions.some(c => c.toLowerCase() === currentColor.toLowerCase());

    const colorButtons = colorOptions.map(color => `
        <button class="color-btn" style="
            background-color:${color};
            width:24px; height:24px;
            border:1px solid #ccc;
            border-radius:50%;
            margin:2px;
            cursor:pointer;"
            data-color="${color}">
        </button>
    `).join('');

    const customColorButton = `
        <button id="customColorBtn" title="Color personalizado" style="
            background: ${isCustomColor ? currentColor : 'linear-gradient(45deg, red, yellow, green, cyan, blue, magenta)'};
            width:24px; height:24px;
            border:1px solid #ccc;
            border-radius:50%;
            margin:2px;
            cursor:pointer;">
        </button>
        <input type="color" id="customColorPicker" style="display:none;" value="${isCustomColor ? currentColor : '#3388ff'}" />
    `;

    const opacityControls = isRegularMarker || isPolyline ? '' : `
        <label for="opacityRange" style="font-weight:600;">
            Transparencia: <span id="opacityValue">${currentFillOpacity}</span>
        </label>
        <input id="opacityRange" type="range" min="0" max="1" step="0.1" 
            value="${currentFillOpacity}" style="width:100%; margin-bottom:16px;" />
    `;

    const weightControls = isRegularMarker ? '' : `
        <label for="weightRange" style="font-weight:600;">
            Grosor de línea: <span id="weightValue">${currentWeight}</span>
        </label>
        <input id="weightRange" type="range" min="1" max="10" step="1"
            value="${currentWeight}" data-original="${currentWeight}" style="width:100%; margin-bottom:20px;" />
    `;

    // Inputs ocultos para mantener compatibilidad con saveNombre
    const hiddenInputs = `
        ${isRegularMarker || isPolyline ? `<input type="hidden" id="opacityRange" value="${currentFillOpacity}">` : ''}
        ${isRegularMarker ? `<input type="hidden" id="weightRange" value="${currentWeight}">` : ''}
    `;

    const modalHtml = `
    <div id="nombreModal" style="position:fixed;top:0;left:0;width:100%;height:100%;background-color:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:9999;">
        <div style="position:relative;background:#f9f9f9;padding:24px;border-radius:12px;width:320px;box-shadow:0 4px 16px rgba(0,0,0,0.2);font-family:sans-serif;color:#333;">
            <button onclick="closeNombreModal()" title="Cerrar" style="position:absolute;top:12px;right:12px;background:transparent;border:none;font-size:20px;font-weight:bold;color:#999;cursor:pointer;">&times;</button>
            <h3 style="margin-top:0;margin-bottom:16px;font-size:18px;color:#222;">
                ${isRegularMarker ? 'Editar punto' : 
                  isCircleMarker ? 'Editar círculo' : 
                  isPolyline ? 'Editar línea' : 'Editar polígono'}
            </h3>
            <label for="nombreInput">Nombre:</label>
            <input id="nombreInput" type="text" value="${currentNombre}" style="width:100%;padding:10px;margin-bottom:12px;border:1px solid #ccc;border-radius:6px;box-sizing:border-box;" />
            <label for="descripcionInput">Descripción:</label>
            <textarea id="descripcionInput" rows="3" style="width:100%;padding:10px;margin-bottom:12px;border:1px solid #ccc;border-radius:6px;box-sizing:border-box;resize:vertical;">${currentDescripcion}</textarea>

            <label style="font-weight:600;">Color de figura:</label>
            <div id="colorButtons" style="margin:8px 0 16px;display:flex;flex-wrap:wrap;gap:6px;">
                ${colorButtons}${customColorButton}
            </div>

            ${opacityControls}
            ${weightControls}
            ${hiddenInputs}

            <div style="text-align:right;">
                <button onclick="closeNombreModal()" style="background:#eee;color:#333;border:none;padding:6px 12px;margin-right:8px;border-radius:6px;cursor:pointer;">Cancelar</button>
                <button onclick="saveNombre(${layerId})" style="background:#3388ff;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;">Guardar</button>
            </div>
        </div>
    </div>`;

    document.body.insertAdjacentHTML('beforeend', modalHtml);

    const colorButtonsContainer = document.getElementById('colorButtons');

    // Función para seleccionar color
    function selectColor(color) {
        document.querySelectorAll('.color-btn').forEach(b => b.style.outline = 'none');
        document.getElementById('customColorBtn').style.outline = 'none';
        colorButtonsContainer.setAttribute('data-selected-color', color);

        const customBtn = document.getElementById('customColorBtn');
        if (color === customBtn.style.backgroundColor) {
            customBtn.style.outline = '2px solid #000';
        } else {
            const matched = Array.from(document.querySelectorAll('.color-btn'))
                .find(btn => btn.dataset.color.toLowerCase() === color.toLowerCase());
            if (matched) matched.style.outline = '2px solid #000';
        }
    }

    // Seleccionar color inicial
    selectColor(currentColor);

    // Configurar eventos
    document.querySelectorAll('.color-btn').forEach(btn => {
        btn.addEventListener('click', () => selectColor(btn.dataset.color));
    });

    const colorPicker = document.getElementById('customColorPicker');
    const customBtn = document.getElementById('customColorBtn');

    customBtn.addEventListener('click', () => colorPicker.click());
    colorPicker.addEventListener('input', (e) => {
        const selectedColor = e.target.value;
        customBtn.style.background = selectedColor;
        selectColor(selectedColor);
    });

    // Configurar eventos para sliders visibles
    const visibleOpacityRange = document.querySelector('input[id="opacityRange"]:not([type="hidden"])');
    if (visibleOpacityRange) {
        visibleOpacityRange.addEventListener('input', e => {
            document.getElementById('opacityValue').textContent = e.target.value;
        });
    }

    const visibleWeightRange = document.querySelector('input[id="weightRange"]:not([type="hidden"])');
    if (visibleWeightRange) {
        visibleWeightRange.addEventListener('input', e => {
            document.getElementById('weightValue').textContent = e.target.value;
        });
    }

    document.addEventListener('keydown', handleEscapeKey);
}

function closeNombreModal() {
    const modal = document.getElementById('nombreModal');
    if (modal) modal.remove();
    document.removeEventListener('keydown', handleEscapeKey);
}

// Función para actualizar los nombres de las figuras
function saveNombre(layerId) {
    const newNombre = document.getElementById('nombreInput').value;
    const newDescripcion = document.getElementById('descripcionInput').value;
    const selectedColor = document.getElementById('colorButtons').getAttribute('data-selected-color') || "#3388ff";
    const newOpacity = parseFloat(document.getElementById('opacityRange').value);
    const weightInput = document.getElementById('weightRange');
    const newWeight = weightInput ? parseInt(weightInput.value) : undefined;
    const originalWeight = weightInput ? parseInt(weightInput.getAttribute('data-original')) : undefined;

    const layer = drawnItems.getLayer(layerId);

    // Actualizar propiedades
    if (!layer.feature.properties) layer.feature.properties = {};
    layer.feature.properties.nombre = newNombre.trim() === '' ? null : newNombre;
    layer.feature.properties.descripcion = newDescripcion;

    let geojson, wkt;

    if (layer instanceof L.CircleMarker) {
        const center = layer.getLatLng();
        const radius = layer.getRadius();
        // Crear el polígono GeoJSON sólo para almacenar (no reemplazar layer)
        geojson = createCircularPolygon(center, radius);
        geojson.properties = {
            ...geojson.properties,
            nombre: newNombre,
            descripcion: newDescripcion,
            radius: Math.round(radius * 100) / 100
        };
        wkt = Terraformer.WKT.convert(geojson.geometry);
        // No reemplazar layer.feature, solo actualizar propiedades originales
        layer.feature.properties = geojson.properties; 
    } else {
        geojson = layer.toGeoJSON();
        geojson.properties = {
            ...geojson.properties,
            nombre: newNombre,
            descripcion: newDescripcion
        };
        wkt = Terraformer.WKT.convert(geojson.geometry);
        layer.feature = geojson;
    }

    // Aplicar estilos
    if (layer instanceof L.Marker && !(layer instanceof L.CircleMarker)) {
        // Marcador regular: actualizar icono con color
        const newIcon = L.divIcon({
            className: 'custom-marker',
            html: `<div style="background-color:${selectedColor}; width:12px; height:12px; border-radius:50%; border:2px solid white; box-shadow:0 0 5px rgba(0,0,0,0.3);"></div>`,
            iconSize: [16, 16],
            iconAnchor: [8, 8]
        });
        layer.setIcon(newIcon);
        layer.options.color = selectedColor;
    } else if (layer.setStyle) {
        // Polígono, línea, círculo: setStyle
        const style = {
            color: selectedColor,
            weight: (newWeight !== undefined && newWeight !== originalWeight) ? newWeight : layer.options.weight,
            opacity: 1,
            fillColor: selectedColor,
            fillOpacity: newOpacity
        };
        layer.setStyle(style);
    }

    // Actualizar popup
    const name = getLayerTypeName(layer);
    const content = getPopupContent(layer, geojson, wkt, name);
    layer.bindPopup(content).openPopup();

    closeNombreModal();
}

// ==========================
// 5. Funciones de descarga
// ==========================

// Función para nombrar los archivos al momento de descargarlos
function obtenerNombreArchivo(layer) {
    const nombre = layer?.feature?.properties?.nombre;
    const tipoFigura = getLayerTypeName(layer);
    return (nombre && nombre !== "Sin nombre") ? nombre : tipoFigura;
}

// Descarga de KML
function downloadKML(wkt, name) {
    let geojson;
    try {
        geojson = Terraformer.WKT.parse(wkt);
    } catch (error) {
        console.error("Error al convertir WKT a GeoJSON:", error);
        return;
    }

    // Buscar la capa correspondiente para obtener propiedades y estilos
    let featureLayer = drawnItems.getLayers().find(layer => {
        // Si es un CircleMarker convertido a polígono, comparar centro y radio
        if (layer instanceof L.CircleMarker) {
            const layerWKT = Terraformer.WKT.convert(layer.feature.geometry);
            return layerWKT === wkt;
        }
        return Terraformer.WKT.convert(layer.toGeoJSON().geometry) === wkt;
    });

    let nombre = name || null;
    let descripcion = "";
    let tipoFigura = "Figura";
    let color = "#3388ff"; // Color por defecto
    let opacity = 1; // Opacidad por defecto
    let fillOpacity = 0.5; // Opacidad de relleno por defecto
    let weight = 2; // Grosor por defecto

    if (featureLayer) {
        // Obtener tipo real aunque se haya convertido a polígono
        tipoFigura = getLayerTypeName(featureLayer);

        // Obtener nombre y descripción desde properties si existen
        if (featureLayer.feature && featureLayer.feature.properties) {
            nombre = featureLayer.feature.properties.nombre || nombre;
            descripcion = featureLayer.feature.properties.descripcion || descripcion;

            // Obtener estilos de las propiedades si existen
            color = featureLayer.feature.properties.color || color;
            fillOpacity = featureLayer.feature.properties.fillOpacity || fillOpacity;
            weight = featureLayer.feature.properties.weight || weight;
        }

        // Si es CircleMarker, obtener el estilo del marcador original
        if (featureLayer instanceof L.CircleMarker) {
            color = featureLayer.options.color || color;
            opacity = featureLayer.options.opacity !== undefined ? featureLayer.options.opacity : opacity;
            fillOpacity = featureLayer.options.fillOpacity !== undefined ? featureLayer.options.fillOpacity : fillOpacity;
            weight = featureLayer.options.weight !== undefined ? featureLayer.options.weight : weight;
        } else {
            // Manejo normal para otras capas
            if (featureLayer.options) {
                color = featureLayer.options.color || featureLayer.options.fillColor || color;
                opacity = featureLayer.options.opacity !== undefined ? featureLayer.options.opacity : opacity;
                fillOpacity = featureLayer.options.fillOpacity !== undefined ? featureLayer.options.fillOpacity : fillOpacity;
                weight = featureLayer.options.weight !== undefined ? featureLayer.options.weight : weight;

                // Para marcadores regulares con icono personalizado
                if (featureLayer instanceof L.Marker && featureLayer.options.icon && 
                    featureLayer.options.icon.options) {
                    const iconOptions = featureLayer.options.icon.options;
                    if (iconOptions.html) {
                        const colorMatch = iconOptions.html.match(/background-color:([^;]+)/);
                        if (colorMatch) color = colorMatch[1].trim();
                    }
                }
            }
        }
    }

    // Determinar nombre de archivo
    const nombreArchivo = (nombre !== null) ? nombre : tipoFigura;
    const nombreParaKML = (nombre !== null) ? nombre : "Sin nombre";

    // Convertir color hexadecimal a formato KML (AABBGGRR)
    const kmlColor = hexToKMLColor(color, opacity);

    let coordinatesKML = "";
    if (geojson.type === 'Point') {
        coordinatesKML = `<Point><coordinates>${geojson.coordinates[0]},${geojson.coordinates[1]},0</coordinates></Point>`;
    } else if (geojson.type === 'Polygon') {
        coordinatesKML = `<Polygon><outerBoundaryIs><LinearRing><coordinates>`;
        geojson.coordinates[0].forEach(coord => {
            coordinatesKML += `${coord[0]},${coord[1]},0 `;
        });
        coordinatesKML += `</coordinates></LinearRing></outerBoundaryIs></Polygon>`;
    } else if (geojson.type === 'LineString') {
        coordinatesKML = `<LineString><coordinates>`;
        geojson.coordinates.forEach(coord => {
            coordinatesKML += `${coord[0]},${coord[1]},0 `;
        });
        coordinatesKML += `</coordinates></LineString>`;
    } else {
        console.warn("Tipo de geometría no soportado para KML:", geojson.type);
        return;
    }

    // Estilos KML
    let styleKML = '';
    if (geojson.type === 'Polygon') {
        styleKML = `
        <Style>
            <LineStyle>
                <color>${kmlColor}</color>
                <width>${weight}</width>
            </LineStyle>
            <PolyStyle>
                <color>${hexToKMLColor(color, fillOpacity)}</color>
                <fill>1</fill>
                <outline>1</outline>
            </PolyStyle>
        </Style>`;
    } else if (geojson.type === 'LineString') {
        styleKML = `
        <Style>
            <LineStyle>
                <color>${kmlColor}</color>
                <width>${weight}</width>
            </LineStyle>
        </Style>`;
    } else if (geojson.type === 'Point') {
        styleKML = `
        <Style id="pointStyle">
            <IconStyle>
                <color>${kmlColor}</color>
                <scale>1.0</scale>
                <Icon>
                    <href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href>
                </Icon>
                <hotSpot x="0.5" y="0.5" xunits="fraction" yunits="fraction"/>
            </IconStyle>
            <LabelStyle>
                <scale>0.8</scale>
            </LabelStyle>
            <BalloonStyle>
                <text><![CDATA[<b>$[name]</b><br/>$[description]]></text>
            </BalloonStyle>
        </Style>`;
    }

    // Construcción del KML - Asegurarse de incluir la descripción
    const kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>${nombreParaKML}</name>
      <description><![CDATA[${descripcion}]]></description>
      ${styleKML}
      ${coordinatesKML}
    </Placemark>
  </Document>
</kml>`;

    const blob = new Blob([kml], { type: 'application/vnd.google-earth.kml+xml' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = nombreArchivo + '.kml';
    link.click();
}


// Función auxiliar para convertir color hexadecimal a formato KML (AABBGGRR)
function hexToKMLColor(hex, opacity = 1) {
    if (!hex || !/^#([0-9A-F]{3}){1,2}$/i.test(hex)) {
        hex = '#3388ff'; 
    }
    
    // Convertir opacidad (0-1) a hexadecimal (00-ff)
    const alpha = Math.round(opacity * 255).toString(16).padStart(2, '0');
    
    // Expandir formato abreviado (#RGB => #RRGGBB)
    const fullHex = hex.length === 4 ? 
        `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}` : hex;
    
    // Extraer componentes RGB
    const r = fullHex.substring(1, 3);
    const g = fullHex.substring(3, 5);
    const b = fullHex.substring(5, 7);
    
    // KML usa formato AABBGGRR (alpha, blue, green, red)
    return alpha + b + g + r;
}

// Descarga de GeoJSON
function downloadGeoJSON(geojson, name) {
    let nombre = name || null;
    let tipoFigura = "Figura";
    
    if (!name) {
        let feature = drawnItems.getLayers().find(layer => {
            return JSON.stringify(layer.toGeoJSON()) === JSON.stringify(geojson);
        });

        if (feature) {
            tipoFigura = getLayerTypeName(feature);
            if (feature.feature && feature.feature.properties) {
                nombre = feature.feature.properties.nombre || null;
            }
        }
    }

    // Usar tipoFigura si nombre es null
    const nombreArchivo = (nombre !== null) ? nombre : tipoFigura;
    const geojsonString = JSON.stringify(geojson, null, 2);
    const blob = new Blob([geojsonString], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = nombreArchivo + '.geojson';
    link.click();
}

// Conversión y descarga de CSV
function convertWKTtoCSV(wkt, name) {
    try {
        const geojson = Terraformer.WKT.parse(wkt);
        
        let nombre = name || null;
        let descripcion = "";
        let tipoFigura = "Figura";
        
        // Buscar la capa correspondiente para obtener propiedades
        let feature = drawnItems.getLayers().find(layer => {
            // Caso especial para CircleMarker
            if (layer instanceof L.CircleMarker) {
                const layerWKT = Terraformer.WKT.convert(layer.feature.geometry);
                return layerWKT === wkt;
            }
            return Terraformer.WKT.convert(layer.toGeoJSON().geometry) === wkt;
        });

        if (feature) {
            tipoFigura = getLayerTypeName(feature);
            if (feature.feature && feature.feature.properties) {
                nombre = feature.feature.properties.nombre || null;
                descripcion = feature.feature.properties.descripcion || "";
            }
        }

        // Usar tipoFigura si nombre es null
        const nombreArchivo = (nombre !== null) ? nombre : tipoFigura;
        // Mostrar "Sin nombre" en el CSV si es null
        const nombreParaCSV = (nombre !== null) ? nombre : "Sin nombre";
        
        // Encabezados del CSV incluyendo descripción
        let csv = "type,x,y,nombre,descripcion\n";
        const escapeCSV = str => `"${String(str).replace(/"/g, '""')}"`;

        if (geojson.type === 'Point') {
            const [x, y] = geojson.coordinates;
            csv += `Point,${x},${y},${escapeCSV(nombreParaCSV)},${escapeCSV(descripcion)}\n`;
        } else if (geojson.type === 'Polygon') {
            // Para CircleMarker convertido a polígono, usar solo el primer punto (centro)
            const isCircleMarker = feature instanceof L.CircleMarker;
            if (isCircleMarker && geojson.coordinates[0].length > 0) {
                const [x, y] = geojson.coordinates[0][0];
                csv += `Circle,${x},${y},${escapeCSV(nombreParaCSV)},${escapeCSV(descripcion)}\n`;
            } else {
                geojson.coordinates[0].forEach(coord => {
                    const [x, y] = coord;
                    csv += `Polygon,${x},${y},${escapeCSV(nombreParaCSV)},${escapeCSV(descripcion)}\n`;
                });
            }
        } else if (geojson.type === 'LineString') {
            geojson.coordinates.forEach(coord => {
                const [x, y] = coord;
                csv += `Line,${x},${y},${escapeCSV(nombreParaCSV)},${escapeCSV(descripcion)}\n`;
            });
        } else {
            console.warn("Tipo de geometría no soportado para CSV:", geojson.type);
            return;
        }

        const blob = new Blob([csv], { type: 'text/csv' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = nombreArchivo + '.csv';
        link.click();
    } catch (error) {
        console.error("Error al convertir WKT a CSV:", error);
    }
}

function copyWKTtoClipboard() {
    var wktContainer = document.getElementById('wkt-container');
    var textToCopy = wktContainer.textContent || wktContainer.innerText;
    navigator.clipboard.writeText(textToCopy).catch(function(err) {
        console.error("Error al copiar al portapapeles", err);
    });
}