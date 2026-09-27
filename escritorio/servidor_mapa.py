# -*- coding: utf-8 -*-
"""
Servidor local para el mapa web y la app del celular.

Lo usa el botón "Abrir mapa" de los detectores. También se puede correr solo para
usar la plataforma sin internet en la laptop:

    python escritorio/servidor_mapa.py            # abre el mapa  (web/mapa/)
    python escritorio/servidor_mapa.py movil      # abre la app del celular (web/movil/)
"""

import functools
import http.server
import socket
import sys
import threading
import urllib.request
import webbrowser

from georreferencia import REPO_DIR

PUERTOS = range(8000, 8011)
_servidor = None


class _Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        # El mapa lee salidas/ cada segundo: sin caché para ver siempre lo último
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


def _es_nuestro(puerto):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{puerto}/config/georreferencia.json', timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _libre(puerto):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', puerto)) != 0


def iniciar_servidor():
    """Sirve la raíz del repositorio en el primer puerto disponible (8000-8010). Devuelve el puerto."""
    global _servidor
    if _servidor:
        return _servidor.server_address[1]
    for p in PUERTOS:
        if _es_nuestro(p):          # ya hay otra ventana del detector sirviendo el repositorio
            return p
        if _libre(p):
            manejador = functools.partial(_Silencioso, directory=REPO_DIR)
            _servidor = http.server.ThreadingHTTPServer(('127.0.0.1', p), manejador)
            threading.Thread(target=_servidor.serve_forever, daemon=True).start()
            return p
    raise RuntimeError('No hay puertos libres entre 8000 y 8010.')


def abrir(pagina='mapa'):
    """Inicia (si hace falta) el servidor y abre web/<pagina>/ en el navegador."""
    puerto = iniciar_servidor()
    url = f'http://localhost:{puerto}/web/{pagina}/'
    webbrowser.open(url)
    return url


if __name__ == '__main__':
    url = abrir(sys.argv[1] if len(sys.argv) > 1 else 'mapa')
    print(f'Cartografía sensorial en {url}  (Ctrl+C para terminar)')
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
