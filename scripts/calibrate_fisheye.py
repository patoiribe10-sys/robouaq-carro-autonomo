#!/usr/bin/env python3
"""
calibrate_fisheye.py — Calibración fisheye IMX219 para RoboUAQ 2026

Flujo:
  1. Presiona C (teclado) o clic FOTO para capturar un frame
  2. El script busca el tablero en ese frame
  3. Si lo encuentra → lo guarda para calibración (flash verde)
  4. Si no → muestra qué vio (flash rojo) para que ajustes
  5. Cuando tengas 20+, presiona Q o clic CALCULAR
"""

import cv2
import numpy as np
import yaml
import argparse
import os
import sys

BOARD_W   = 5
BOARD_H   = 7
SQUARE_MM = 25.0
MIN_IMAGES = 20

# ── Pipeline ──────────────────────────────────────────────────────────────────
def gstreamer_pipeline(sensor_id=0):
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM),width=(int)640,height=(int)480,"
        f"format=(string)NV12,framerate=(fraction)30/1 ! "
        f"nvvidconv ! video/x-raw,format=(string)BGRx ! "
        f"videoconvert ! video/x-raw,format=(string)BGR ! "
        f"appsink sync=false drop=true max-buffers=1"
    )

# ── Mouse ─────────────────────────────────────────────────────────────────────
clicks = {'foto': False, 'calcular': False, 'borrar': False}

BTNS = {
    'foto':     (10,  8, 150, 44),
    'borrar':   (170, 8, 150, 44),
    'calcular': (330, 8, 160, 44),
}

def mouse_cb(event, x, y, flags, param):
    if event != cv2.EVENT_LBUTTONDOWN:
        return
    for name, (bx, by, bw, bh) in BTNS.items():
        if bx <= x <= bx+bw and by <= y <= by+bh:
            clicks[name] = True

def btn(img, label, x, y, w, h, color):
    cv2.rectangle(img, (x,y), (x+w,y+h), color, -1)
    cv2.rectangle(img, (x,y), (x+w,y+h), (220,220,220), 1)
    sz = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)[0]
    cv2.putText(img, label, (x+(w-sz[0])//2, y+h//2+7),
                cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255,255,255), 2)

# ── Detección robusta del tablero ─────────────────────────────────────────────
def find_board(gray):
    """Intenta detectar el tablero con varias estrategias."""
    flags_list = [
        cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE,
        cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FILTER_QUADS,
        cv2.CALIB_CB_NORMALIZE_IMAGE,
        0,
    ]
    # CLAHE para mejorar contraste
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    gray_eq = clahe.apply(gray)

    for g in [gray, gray_eq]:
        for flags in flags_list:
            found, corners = cv2.findChessboardCorners(g, (BOARD_W, BOARD_H), flags)
            if found:
                criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
                corners = cv2.cornerSubPix(g, corners, (5,5), (-1,-1), criteria)
                return True, corners
    return False, None

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sensor-id',  type=int, default=0)
    parser.add_argument('--output',     type=str, default='camera_fisheye.yaml')
    parser.add_argument('--no-fisheye', action='store_true', default=False,
                        help='Calibración estándar (cam1) en vez de fisheye (cam0)')
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    if args.no_fisheye:
        objp = np.zeros((BOARD_W * BOARD_H, 3), np.float32)
        objp[:, :2] = np.mgrid[0:BOARD_W, 0:BOARD_H].T.reshape(-1, 2) * SQUARE_MM
    else:
        objp = np.zeros((BOARD_W * BOARD_H, 1, 3), dtype=np.float64)
        objp[:, 0, :2] = np.mgrid[0:BOARD_W, 0:BOARD_H].T.reshape(-1, 2) * SQUARE_MM

    objpoints, imgpoints = [], []

    print(f"Abriendo camara {args.sensor_id}...")
    cap = cv2.VideoCapture(gstreamer_pipeline(args.sensor_id), cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        print("ERROR: no abre la camara")
        sys.exit(1)

    # Warm-up
    for _ in range(20):
        cap.read()
    print("Lista. NO muevas la ventana — usa los botones.")
    print("  C = tomar foto y buscar tablero")
    print("  Q = calcular calibracion")

    WIN = "Calibracion Fisheye  [NO mover ventana]"
    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, mouse_cb)

    # Overlay de resultado
    overlay_frame = None
    overlay_timer = 0
    overlay_ok     = True   # True=verde(ok), False=rojo(no encontro)
    last_frame     = None

    while True:
        ret, frame = cap.read()
        if not ret:
            continue
        last_frame = frame.copy()

        display = frame.copy()

        # Overlay flash resultado
        if overlay_timer > 0 and overlay_frame is not None:
            alpha = min(1.0, overlay_timer / 6.0)
            cv2.addWeighted(overlay_frame, alpha*0.5, display, 1-alpha*0.3, 0, display)
            overlay_timer -= 1

        # Barra superior
        bar = display.copy()
        cv2.rectangle(bar, (0,0), (640,60), (25,25,25), -1)
        cv2.addWeighted(bar, 0.75, display, 0.25, 0, display)

        n = len(objpoints)
        btn(display, "FOTO (C)",  *BTNS['foto'],     (0,130,0))
        btn(display, "BORRAR",    *BTNS['borrar'],   (0,70,160))
        btn(display, "CALCULAR",  *BTNS['calcular'], (160,0,0))

        color_n = (0,255,0) if n >= MIN_IMAGES else (0,200,255)
        cv2.putText(display, f"{n}/{MIN_IMAGES}", (510,42),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, color_n, 2)

        hint = "C=foto  Q=calcular  — NO muevas la ventana"
        cv2.putText(display, hint, (8, 475),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180,180,180), 1)

        cv2.imshow(WIN, display)
        key = cv2.waitKey(1) & 0xFF

        # ── Tomar foto y buscar tablero ───────────────────────────────────────
        do_foto = (key == ord('c')) or clicks['foto']
        clicks['foto'] = False

        if do_foto and last_frame is not None:
            gray = cv2.cvtColor(last_frame, cv2.COLOR_BGR2GRAY)
            found, corners = find_board(gray)

            if found:
                objpoints.append(objp)
                imgpoints.append(corners)
                overlay_frame = last_frame.copy()
                cv2.drawChessboardCorners(overlay_frame, (BOARD_W,BOARD_H), corners, True)
                # Flash verde
                green = overlay_frame.copy()
                cv2.rectangle(green, (0,0), (640,480), (0,200,0), -1)
                cv2.addWeighted(green, 0.25, overlay_frame, 0.75, 0, overlay_frame)
                overlay_ok    = True
                overlay_timer = 12
                print(f"  OK #{len(objpoints):02d} — tablero encontrado")
            else:
                overlay_frame = last_frame.copy()
                # Flash rojo
                red = overlay_frame.copy()
                cv2.rectangle(red, (0,0), (640,480), (0,0,200), -1)
                cv2.addWeighted(red, 0.35, overlay_frame, 0.65, 0, overlay_frame)
                cv2.putText(overlay_frame, "NO ENCONTRADO", (140,260),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0,0,255), 3)
                overlay_ok    = False
                overlay_timer = 18
                print(f"  No encontrado. Tips:")
                print(f"    - Asegurate que todo el tablero sea visible")
                print(f"    - Mejor iluminacion, sin brillos")
                print(f"    - El tablero debe ser 9x6 esquinas interiores")

        # ── Borrar ────────────────────────────────────────────────────────────
        if (key == ord('d')) or clicks['borrar']:
            clicks['borrar'] = False
            if objpoints:
                objpoints.pop(); imgpoints.pop()
                print(f"  Borrada. Total: {len(objpoints)}")

        # ── Calcular ──────────────────────────────────────────────────────────
        do_calc = (key == ord('q')) or clicks['calcular']
        clicks['calcular'] = False
        if do_calc:
            if len(objpoints) < 5:
                print(f"  Muy pocas capturas ({len(objpoints)}). Minimo 5.")
            else:
                break

    cap.release()
    cv2.destroyAllWindows()

    if not objpoints:
        sys.exit(0)

    # ── Calibración ───────────────────────────────────────────────────────────
    print(f"\nCalculando con {len(objpoints)} imagenes...")

    try:
        if args.no_fisheye:
            rms, K, D, rvecs, tvecs = cv2.calibrateCamera(
                objpoints, imgpoints, (640, 480), None, None
            )
        else:
            K = np.zeros((3, 3))
            D = np.zeros((4, 1))
            rvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in objpoints]
            tvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in objpoints]
            rms, K, D, rvecs, tvecs = cv2.fisheye.calibrate(
                objpoints, imgpoints, (640, 480), K, D, rvecs, tvecs,
                cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC + cv2.fisheye.CALIB_FIX_SKEW
            )
    except cv2.error as e:
        print(f"ERROR calibracion: {e}")
        print("Intenta con mas imagenes desde angulos muy variados.")
        sys.exit(1)

    quality = "BUENA" if rms < 0.5 else "ACEPTABLE" if rms < 1.0 else "ALTA — toma mas imagenes"
    print(f"  RMS error: {rms:.4f} px  [{quality}]")
    print(f"  K:\n{K}")
    print(f"  D: {D.flatten()}")

    d_flat = D.flatten()
    data = {
        'image_width': 640, 'image_height': 480,
        'rms_error': float(rms),
        'model': 'standard' if args.no_fisheye else 'fisheye',
        'camera_matrix':           {'rows': 3, 'cols': 3, 'data': K.flatten().tolist()},
        'distortion_coefficients': {'rows': 1, 'cols': len(d_flat), 'data': d_flat.tolist()},
    }
    with open(args.output, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)

    print(f"\nGuardado en: {args.output}")
    print("Calibracion completa!")

if __name__ == '__main__':
    main()
