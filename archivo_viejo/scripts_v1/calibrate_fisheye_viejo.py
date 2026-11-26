#!/usr/bin/env python3
"""
calibrate_fisheye.py — Calibración de cámaras CSI IMX219

Uso:
  python3 calibrate_fisheye.py --sensor-id 0            # cam0 fisheye
  python3 calibrate_fisheye.py --sensor-id 1 --no-fisheye  # cam1 normal

Proceso:
  1. Muestra preview de la cámara
  2. Presiona SPACE para capturar un frame con el tablero visible
  3. Captura 20 imágenes con detección exitosa del tablero
  4. Calcula calibración y guarda en config/

Tablero recomendado: 9x6 esquinas internas, cuadros de 25mm
Imprimir en papel A4, pegar sobre superficie rígida y plana.
"""

import argparse
import os
import sys
import time

import cv2
import numpy as np
import yaml



def _open_camera(sensor_id):
    """Abre la cámara por índice directo; fallback a índice 0."""
    cap = cv2.VideoCapture(sensor_id)
    if not cap.isOpened():
        print(f'WARN: No se pudo abrir índice {sensor_id}, intentando índice 0...')
        cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('Y', 'U', 'Y', 'V'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
    return cap


def main():
    parser = argparse.ArgumentParser(description='Calibración cámara CSI RoboUAQ')
    parser.add_argument('--sensor-id', type=int, default=0,
                        help='sensor-id de la cámara (0=fisheye, 1=normal)')
    parser.add_argument('--no-fisheye', action='store_true',
                        help='Usar modelo de calibración estándar (no fisheye)')
    parser.add_argument('--cols', type=int, default=9,
                        help='Esquinas internas en columnas del tablero')
    parser.add_argument('--rows', type=int, default=6,
                        help='Esquinas internas en filas del tablero')
    parser.add_argument('--square-mm', type=float, default=25.0,
                        help='Tamaño del cuadro del tablero en mm')
    parser.add_argument('--count', type=int, default=20,
                        help='Número de capturas para calibrar')
    parser.add_argument('--output-dir', type=str,
                        default=os.path.expanduser(
                            '~/robocar_ws/src/robocar/config'
                        ),
                        help='Directorio de salida para yaml')
    parser.add_argument('--tune-birdseye', action='store_true',
                        help='Modo interactivo para ajustar puntos bird\'s eye')
    args = parser.parse_args()

    is_fisheye = not args.no_fisheye and args.sensor_id == 0

    print(f'Calibrando sensor-id={args.sensor_id} '
          f'({"fisheye" if is_fisheye else "normal"})')
    print(f'Tablero: {args.cols}x{args.rows} esquinas, cuadro={args.square_mm}mm')
    print(f'Objetivo: {args.count} capturas')
    print()
    print('Controles:')
    print('  SPACE = capturar frame')
    print('  R     = reiniciar capturas')
    print('  Q/ESC = salir')

    if args.tune_birdseye:
        _tune_birdseye(args)
        return

    # ── Abrir cámara ──
    cap = _open_camera(args.sensor_id)
    if cap is None:
        print(f'ERROR: No se pudo abrir cámara (índice {args.sensor_id} ni 0).')
        sys.exit(1)

    board_size = (args.cols, args.rows)
    sq = args.square_mm / 1000.0  # metros

    # Coordenadas 3D del tablero
    objp = np.zeros((args.cols * args.rows, 3), np.float32)
    objp[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2)
    objp *= sq

    obj_points = []   # 3D en espacio real
    img_points = []   # 2D en imagen
    captured = 0
    img_size = None

    print('\nApunta la cámara al tablero y presiona SPACE...\n')

    while captured < args.count:
        ret, frame = cap.read()
        if frame is not None:
            if frame.shape[2] == 3 and frame[:, :, 0].mean() < 1 and frame[:, :, 2].mean() < 1:
                frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_YUYV)
            frame = cv2.resize(frame, (640, 480))
        if not ret:
            print('Sin frame...')
            time.sleep(0.1)
            continue

        if img_size is None:
            img_size = (frame.shape[1], frame.shape[0])

        display = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Mostrar progreso
        cv2.putText(display, f'Capturas: {captured}/{args.count}',
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(display, 'SPACE=capturar  R=reset  Q=salir',
                    (10, img_size[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        # Detección previa del tablero (preview)
        found_preview, corners_preview = cv2.findChessboardCorners(
            gray, board_size,
            cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_FAST_CHECK
        )
        if found_preview:
            cv2.drawChessboardCorners(display, board_size, corners_preview, found_preview)
            cv2.putText(display, 'TABLERO OK — presiona SPACE',
                        (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        else:
            cv2.putText(display, 'Buscando tablero...',
                        (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        cv2.imshow(f'Calibracion cam{args.sensor_id}', display)
        key = cv2.waitKey(30) & 0xFF

        if key in (ord('q'), 27):  # Q o ESC
            break
        elif key == ord('r'):
            obj_points.clear()
            img_points.clear()
            captured = 0
            print('Capturas reiniciadas')
        elif key == ord(' '):
            # Intentar capturar este frame
            found, corners = cv2.findChessboardCorners(gray, board_size, None)
            if found:
                # Refinar esquinas
                criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
                corners_refined = cv2.cornerSubPix(
                    gray, corners, (11, 11), (-1, -1), criteria
                )
                obj_points.append(objp)
                img_points.append(corners_refined)
                captured += 1
                print(f'  Captura {captured}/{args.count} OK')

                # Flash verde
                flash = np.zeros_like(display)
                flash[:] = (0, 200, 0)
                blended = cv2.addWeighted(display, 0.5, flash, 0.5, 0)
                cv2.imshow(f'Calibracion cam{args.sensor_id}', blended)
                cv2.waitKey(200)
            else:
                print('  No se detectó el tablero — reintenta')

    cap.release()
    cv2.destroyAllWindows()

    if captured < 5:
        print(f'Pocas capturas ({captured}). Necesitas al menos 5. Abortando.')
        sys.exit(1)

    # ── Calibrar ──
    print(f'\nCalibrando con {captured} imágenes...')

    if is_fisheye:
        _calibrate_fisheye(obj_points, img_points, img_size, args)
    else:
        _calibrate_normal(obj_points, img_points, img_size, args)


def _calibrate_fisheye(obj_points, img_points, img_size, args):
    K = np.zeros((3, 3))
    D = np.zeros((4, 1))
    flags = (
        cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
        | cv2.fisheye.CALIB_CHECK_COND
        | cv2.fisheye.CALIB_FIX_SKEW
    )
    rvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in obj_points]
    tvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in obj_points]

    obj3d = [op.reshape(1, -1, 3) for op in obj_points]
    img2d = [ip.reshape(1, -1, 2) for ip in img_points]

    rms, K, D, rvecs, tvecs = cv2.fisheye.calibrate(
        obj3d, img2d, img_size, K, D, rvecs, tvecs, flags
    )
    print(f'RMS error fisheye: {rms:.4f}')
    print(f'K=\n{K}')
    print(f'D={D.T}')

    out_file = os.path.join(args.output_dir, 'camera_fisheye.yaml')
    _save_yaml(out_file, K, D.flatten(), img_size, fisheye=True)
    print(f'Guardado: {out_file}')


def _calibrate_normal(obj_points, img_points, img_size, args):
    rms, K, D, rvecs, tvecs = cv2.calibrateCamera(
        obj_points, img_points, img_size, None, None
    )
    print(f'RMS error: {rms:.4f}')
    print(f'K=\n{K}')
    print(f'D={D.flatten()}')

    out_file = os.path.join(args.output_dir, 'camera_normal.yaml')
    _save_yaml(out_file, K, D.flatten(), img_size, fisheye=False)
    print(f'Guardado: {out_file}')


def _save_yaml(filepath, K, D, img_size, fisheye):
    data = {
        'image_width': img_size[0],
        'image_height': img_size[1],
        'camera_matrix': {
            'rows': 3,
            'cols': 3,
            'data': K.flatten().tolist()
        },
        'distortion_coefficients': {
            'rows': 1,
            'cols': len(D),
            'data': D.tolist()
        },
        'calibrated': True,
    }
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)


def _tune_birdseye(args):
    """Modo interactivo para ajustar los 4 puntos src del bird's eye warp."""
    import json

    cap = _open_camera(args.sensor_id)
    if cap is None:
        print(f'ERROR: No se pudo abrir cámara (índice {args.sensor_id} ni 0).')
        return

    config_path = os.path.join(args.output_dir, 'bird_eye_params.yaml')

    # Puntos default
    src_pts = [[200, 200], [440, 200], [580, 420], [60, 420]]
    selected = [0]  # índice del punto seleccionado

    def mouse_cb(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            src_pts[selected[0]] = [x, y]
            selected[0] = (selected[0] + 1) % 4

    cv2.namedWindow('Tune BirdsEye')
    cv2.setMouseCallback('Tune BirdsEye', mouse_cb)

    print('Modo ajuste bird\'s eye:')
    print('  Click = colocar punto (en orden: TL, TR, BR, BL)')
    print('  S = guardar  |  Q = salir')
    print(f'  Puntos actuales: {src_pts}')

    while True:
        ret, frame = cap.read()
        if frame is not None:
            if frame.shape[2] == 3 and frame[:, :, 0].mean() < 1 and frame[:, :, 2].mean() < 1:
                frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_YUYV)
            frame = cv2.resize(frame, (640, 480))
        if not ret:
            continue

        display = frame.copy()
        colors = [(0, 255, 0), (0, 255, 255), (0, 165, 255), (255, 0, 0)]
        labels = ['TL', 'TR', 'BR', 'BL']

        for i, (pt, col, lbl) in enumerate(zip(src_pts, colors, labels)):
            cv2.circle(display, tuple(pt), 8, col, -1 if i == selected[0] else 2)
            cv2.putText(display, lbl, (pt[0] + 10, pt[1]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)

        # Dibujar trapecio
        pts = np.array(src_pts, dtype=np.int32)
        cv2.polylines(display, [pts], True, (255, 255, 255), 2)

        # Vista bird's eye en tiempo real
        dst_pts = np.float32([[50, 50], [350, 50], [350, 350], [50, 350]])
        M = cv2.getPerspectiveTransform(np.float32(src_pts), dst_pts)
        warped = cv2.warpPerspective(frame, M, (400, 400))
        display[0:200, frame.shape[1] - 200:] = cv2.resize(warped, (200, 200))

        cv2.putText(display,
                    f'Punto {selected[0]+1}/4: {labels[selected[0]]} — click para colocar',
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imshow('Tune BirdsEye', display)
        key = cv2.waitKey(30) & 0xFF

        if key in (ord('q'), 27):
            break
        elif key == ord('s'):
            if os.path.exists(config_path):
                with open(config_path) as f:
                    existing = yaml.safe_load(f)
            else:
                existing = {}
            existing['src_points'] = src_pts
            with open(config_path, 'w') as f:
                yaml.dump(existing, f, default_flow_style=False)
            print(f'Guardado en {config_path}')
            print(f'src_points: {src_pts}')

    cap.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
