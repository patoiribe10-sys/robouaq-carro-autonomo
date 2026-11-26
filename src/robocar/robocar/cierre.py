"""Ayuda para cerrar los nodos sin que una segunda senal interrumpa el cierre.

`ros2 launch` (y Ctrl+C en una terminal junto con el launch) mandan SIGINT DOS veces a cada nodo:
la primera inicia el cierre ordenado y la segunda llega en mitad de ese cierre y lo corta con un
KeyboardInterrupt (el nodo muere con codigo -2 sin terminar de despedirse). Tras la primera senal
se ignoran las siguientes: si el cierre se trabara, `ros2 launch` escala solo a SIGTERM y luego SIGKILL.
"""
import signal


def ignorar_senales_de_cierre():
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, signal.SIG_IGN)
        except (ValueError, OSError):      # fuera del hilo principal
            pass
