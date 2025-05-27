const map = L.map('map', {
    center: [5.9013, -76.1432],
    zoom: 14,
    maxZoom: 22
});

// Capas base
const googleMapsLayer = L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
    attribution: '&copy; Google Maps',
    maxZoom: 22
});

googleMapsLayer.addTo(map);

drawnItems = L.featureGroup().addTo(map);

map.addControl(new L.Control.Draw({
    edit: {
        featureGroup: drawnItems,
        poly: {
            allowIntersection: false
        }
    },
    draw: {
        polygon: {
            allowIntersection: false,
            showArea: true
        }
    }
}));


