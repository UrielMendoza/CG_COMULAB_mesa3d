L.drawLocal = {
    draw: {
        toolbar: {
            actions: {
                title: 'Cancelar dibujo',
                text: 'Cancelar'
            },
            finish: {
                title: 'Terminar dibujo',
                text: 'Terminar'
            },
            undo: {
                title: 'Eliminar último punto dibujado',
                text: 'Eliminar último punto'
            },
            buttons: {
                polyline: 'Dibujar una línea',
                polygon: 'Dibujar un polígono',
                rectangle: 'Dibujar un rectángulo',
                circle: 'Dibujar un círculo con radio personalizado',
                marker: 'Dibujar un marcador (punto)',
                circlemarker: 'Dibujar un marcador circular (10 metros)'
            }
        },
        handlers: {
            circle: {
                tooltip: {
                    start: 'Haz clic y arrastra para dibujar un círculo.'
                },
                radius: 'Radio'
            },
            circlemarker: {
                tooltip: {
                    start: 'Haz clic en el mapa para colocar un marcador circular de 10 metros de diámetro.'
                }
            },
            marker: {
                tooltip: {
                    start: 'Haz clic en el mapa para colocar un marcador.'
                }
            },
            polygon: {
                tooltip: {
                    start: 'Haz clic para empezar a dibujar la figura.',
                    cont: 'Haz clic para continuar dibujando la figura.',
                    end: 'Haz clic en el primer punto para cerrar esta figura.'
                }
            },
            polyline: {
                error: '<strong>Error:</strong> Una línea no puede cruzar sobre sí misma',
                tooltip: {
                    start: 'Haz clic para comenzar a dibujar la línea.',
                    cont: 'Haz clic para continuar dibujando la línea.',
                    end: 'Haz clic en el último punto para terminar la línea.'
                }
            },
            rectangle: {
                tooltip: {
                    start: 'Haz clic y arrastra para dibujar un rectángulo.'
                }
            },
            simpleshape: {
                tooltip: {
                    end: 'Suelta el mpuse para terminar de dibujar.'
                }
            }
        }
    },
    edit: {
        toolbar: {
            actions: {
                save: {
                    title: 'Guardar cambios',
                    text: 'Guardar'
                },
                cancel: {
                    title: 'Cancelar edición, descarta todos los cambios',
                    text: 'Cancelar'
                },
                clearAll: {
                    title: 'Limpiar todas las figuras',
                    text: 'Limpiar todo lo dibujado'
                }
            },
            buttons: {
                edit: 'Editar figuras',
                editDisabled: 'No hay figuras para editar',
                remove: 'Eliminar figuras',
                removeDisabled: 'No hay figuras para eliminar'
            }
        },
        handlers: {
            edit: {
                tooltip: {
                    text: 'Arrastra los marcadores para editar los elementos.',
                    subtext: 'Haz clic en cancelar para deshacer los cambios.'
                }
            },
            remove: {
                tooltip: {
                    text: 'Haz clic en un elemento para eliminarlo.'
                }
            }
        }
    }
};