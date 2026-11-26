#!/usr/bin/env python3
"""
dashboard.py — Web dashboard RoboUAQ 2026
Flask + SocketIO en puerto 8080

Uso:  python3 scripts/dashboard.py
Web:  http://<ip-jetson>:8080
"""

import threading
import time
import queue
import sys

try:
    import cv2
    import numpy as np
except ImportError:
    print("ERROR: OpenCV no disponible. Usar el OpenCV del sistema (no pip install opencv-python).")
    sys.exit(1)

try:
    from flask import Flask, Response, render_template_string
    from flask_socketio import SocketIO
except ImportError:
    print("ERROR: pip3 install flask flask-socketio --break-system-packages")
    sys.exit(1)

try:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import Float32, Bool, String, Int32
    from sensor_msgs.msg import CompressedImage
except ImportError:
    print("ERROR: rclpy no disponible. Ejecutar con ROS 2 Humble activado.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Estado compartido — escrituras atómicas de Python son thread-safe para tipos
# simples; las listas se reemplazan completas (no append in-place en hot paths)
# ─────────────────────────────────────────────────────────────────────────────
class _State:
    mode: str = 'manual'
    estop: bool = False
    steering_error: float = 0.0
    lane_detected: bool = False
    crosswalk: bool = False
    sign: str = ''
    tag_id: int = -1
    distance: float = 0.0
    odom_vel: float = 0.0
    fsm_state: str = 'IDLE'
    decision_log: list = []

state = _State()

lane_q: queue.Queue = queue.Queue(maxsize=2)
detect_q: queue.Queue = queue.Queue(maxsize=2)

# Publishers asignados cuando el nodo ROS arranca
_pub: dict = {}


def _put_frame(q: queue.Queue, frame) -> None:
    """Inserta frame descartando el viejo si la cola está llena."""
    try:
        q.put_nowait(frame)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(frame)
        except queue.Full:
            pass


def _decode_compressed(msg) -> object:
    try:
        arr = np.frombuffer(msg.data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _r3(x) -> float:
    return round(float(x), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Nodo ROS 2
# ─────────────────────────────────────────────────────────────────────────────
class DashNode(Node):
    def __init__(self):
        super().__init__('dashboard_node')

        _pub['mode']  = self.create_publisher(String, '/mode',       10)
        _pub['estop'] = self.create_publisher(Bool,   '/estop',      10)
        _pub['start'] = self.create_publisher(Bool,   '/nav/start',  10)
        _pub['reset'] = self.create_publisher(Bool,   '/nav/reset',  10)

        def _s(attr, fn=None):
            if fn:
                return lambda msg: setattr(state, attr, fn(msg.data))
            return lambda msg: setattr(state, attr, msg.data)

        self.create_subscription(CompressedImage, '/lane/debug_image',
                                 self._lane_img, 10)
        self.create_subscription(CompressedImage, '/detection/debug_image',
                                 self._det_img, 10)
        self.create_subscription(Float32, '/lane/steering_error',
                                 _s('steering_error', _r3), 10)
        self.create_subscription(Bool,    '/lane/detected',
                                 _s('lane_detected'), 10)
        self.create_subscription(Bool,    '/lane/crosswalk_detected',
                                 _s('crosswalk'), 10)
        self.create_subscription(String,  '/detection/sign',
                                 _s('sign'), 10)
        self.create_subscription(Int32,   '/detection/tag_id',
                                 _s('tag_id'), 10)
        self.create_subscription(Float32, '/detection/distance',
                                 _s('distance', _r3), 10)
        self.create_subscription(Float32, '/odom_vel',
                                 _s('odom_vel', _r3), 10)
        self.create_subscription(String,  '/mode',
                                 _s('mode'), 10)
        self.create_subscription(Bool,    '/estop',
                                 _s('estop'), 10)
        self.create_subscription(String,  '/nav/state',
                                 _s('fsm_state'), 10)
        self.create_subscription(String,  '/nav/decision_log',
                                 self._log_cb, 10)

        self.get_logger().info('dashboard_node listo — http://0.0.0.0:8080')

    def _lane_img(self, msg):
        frame = _decode_compressed(msg)
        if frame is not None:
            _put_frame(lane_q, frame)

    def _det_img(self, msg):
        frame = _decode_compressed(msg)
        if frame is not None:
            _put_frame(detect_q, frame)

    def _log_cb(self, msg):
        log = state.decision_log[-99:] + [msg.data]
        state.decision_log = log


# ─────────────────────────────────────────────────────────────────────────────
# Flask + SocketIO
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'robocar2026'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')


# ─────────────────────────────────────────────────────────────────────────────
# HTML embebido
# ─────────────────────────────────────────────────────────────────────────────
_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>RoboUAQ 2026</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#00ff88;font-family:'Courier New',monospace;padding:8px;font-size:13px}
h1{text-align:center;color:#00ff88;letter-spacing:4px;padding:5px 0;border-bottom:1px solid #143d25;margin-bottom:7px;font-size:1.05em}
.row{display:flex;gap:7px;margin-bottom:7px;align-items:flex-start}
.cameras{justify-content:center;flex-wrap:wrap}
.cam-wrap{border:1px solid #143d25;background:#050505}
.cam-label{text-align:center;font-size:0.7em;color:#446655;padding:2px 4px;border-bottom:1px solid #111}
.cam-wrap img{display:block;width:580px;height:435px;object-fit:contain;background:#000}
.controls{justify-content:center;flex-wrap:wrap;gap:6px;padding:2px 0}
button{font-family:'Courier New',monospace;font-size:0.85em;padding:6px 16px;border:2px solid;cursor:pointer;background:#0d0d0d;text-transform:uppercase;letter-spacing:1px;transition:filter .1s}
button:hover{filter:brightness(1.5)}
button:active{filter:brightness(0.7)}
.b-manual{color:#777;border-color:#777}
.b-observe{color:#00aaff;border-color:#00aaff}
.b-auto{color:#00ff88;border-color:#00ff88}
.b-estop{color:#ff2222;border-color:#ff2222}
.b-start{color:#ffee00;border-color:#ffee00}
.b-reset{color:#ff8800;border-color:#ff8800}
.b-active{filter:brightness(2.2)!important;text-decoration:underline}
.b-estop-on{background:#2a0000!important}
.telem{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;flex:1;min-width:0}
.t{background:#0f0f0f;border:1px solid #143d25;padding:4px 7px}
.tl{color:#336644;font-size:0.62em;text-transform:uppercase;letter-spacing:1px}
.tv{font-size:0.95em;color:#00ff88;margin-top:1px;word-break:break-all}
.tv.w{color:#ffaa00}
.tv.a{color:#ff3333}
.log-wrap{width:320px;flex-shrink:0}
.log-hdr{background:#0f0f0f;border:1px solid #143d25;border-bottom:none;padding:3px 7px;font-size:0.62em;color:#336644;text-transform:uppercase;letter-spacing:1px}
.log-box{background:#070707;border:1px solid #143d25;height:148px;overflow-y:auto;padding:4px 6px}
.ll{color:#2a5c40;font-size:0.72em;padding:1px 0;border-bottom:1px solid #0f0f0f}
.ll:last-child{color:#00ff88}
.sbar{text-align:center;font-size:0.65em;color:#2a3d30;margin-top:3px}
</style>
</head>
<body>
<h1>[ RoboUAQ 2026 &mdash; DASHBOARD ]</h1>

<div class="row controls">
  <button class="b-manual"  id="btn-manual"  onclick="sendMode('manual')">MANUAL</button>
  <button class="b-observe" id="btn-observe" onclick="sendMode('observe')">OBSERVE</button>
  <button class="b-auto"    id="btn-auto"    onclick="sendMode('auto')">AUTO</button>
  <span style="border-left:1px solid #222;margin:0 2px"></span>
  <button class="b-estop" id="btn-estop" onclick="sendEstop()">ESTOP</button>
  <button class="b-start" onclick="sendStart()">START</button>
  <button class="b-reset" onclick="sendReset()">RESET</button>
</div>

<div class="row cameras">
  <div class="cam-wrap">
    <div class="cam-label">cam0 &mdash; LANE (bird&#39;s eye)</div>
    <img src="/video/lane" alt="lane">
  </div>
  <div class="cam-wrap">
    <div class="cam-label">cam1 &mdash; DETECT (AprilTags)</div>
    <img src="/video/detect" alt="detect">
  </div>
</div>

<div class="row">
  <div class="telem">
    <div class="t"><div class="tl">MODE</div><div class="tv" id="t-mode">---</div></div>
    <div class="t"><div class="tl">FSM STATE</div><div class="tv" id="t-fsm">---</div></div>
    <div class="t"><div class="tl">ESTOP</div><div class="tv" id="t-estop">---</div></div>
    <div class="t"><div class="tl">ODOM VEL</div><div class="tv" id="t-vel">---</div></div>
    <div class="t"><div class="tl">STEER ERR</div><div class="tv" id="t-err">---</div></div>
    <div class="t"><div class="tl">LANE</div><div class="tv" id="t-lane">---</div></div>
    <div class="t"><div class="tl">CROSSWALK</div><div class="tv" id="t-cw">---</div></div>
    <div class="t"><div class="tl">SIGN</div><div class="tv" id="t-sign">---</div></div>
    <div class="t"><div class="tl">TAG ID</div><div class="tv" id="t-tag">---</div></div>
    <div class="t"><div class="tl">DIST</div><div class="tv" id="t-dist">---</div></div>
  </div>
  <div class="log-wrap">
    <div class="log-hdr">decision log</div>
    <div class="log-box" id="log-box"><div class="ll">[ esperando... ]</div></div>
  </div>
</div>

<div class="sbar" id="sbar">conectando...</div>

<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"
        crossorigin="anonymous"></script>
<script>
var socket = io({reconnection:true, reconnectionDelay:1500});
var estopOn = false;

socket.on('connect', function(){
  document.getElementById('sbar').textContent =
    '● conectado  ' + new Date().toLocaleTimeString();
});
socket.on('disconnect', function(){
  document.getElementById('sbar').textContent = '○ desconectado';
});

socket.on('telemetry', function(d){
  set('t-mode',  d.mode);
  set('t-fsm',   d.fsm_state);
  setC('t-estop', d.estop ? 'ACTIVE' : 'OFF',      d.estop ? 'a' : '');
  setC('t-vel',   d.odom_vel + ' m/s',              Math.abs(d.odom_vel) < 0.01 ? 'w' : '');
  setC('t-err',   (d.steering_error >= 0 ? '+' : '') + d.steering_error,
                  Math.abs(d.steering_error) > 0.5 ? 'w' : '');
  setC('t-lane',  d.lane_detected ? 'YES' : 'NO',  d.lane_detected ? '' : 'w');
  setC('t-cw',    d.crosswalk ? 'YES' : 'NO',       d.crosswalk ? 'w' : '');
  set('t-sign',   d.sign || '—');
  set('t-tag',    d.tag_id >= 0 ? d.tag_id : '—');
  set('t-dist',   d.distance > 0 ? d.distance + ' m' : '—');

  estopOn = d.estop;
  var eb = document.getElementById('btn-estop');
  eb.textContent = d.estop ? 'RELEASE' : 'ESTOP';
  eb.classList.toggle('b-estop-on', d.estop);

  ['manual','observe','auto'].forEach(function(m){
    document.getElementById('btn-' + m).classList.toggle('b-active', d.mode === m);
  });

  if(d.log && d.log.length){
    var box = document.getElementById('log-box');
    box.innerHTML = d.log.map(function(l){
      return '<div class="ll">' + esc(l) + '</div>';
    }).join('');
    box.scrollTop = box.scrollHeight;
  }
});

function set(id, v){
  var e = document.getElementById(id);
  if(e){ e.textContent = v; e.className = 'tv'; }
}
function setC(id, v, cls){
  var e = document.getElementById(id);
  if(e){ e.textContent = v; e.className = 'tv' + (cls ? ' ' + cls : ''); }
}
function esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function sendMode(m) { socket.emit('cmd_mode',  {mode: m}); }
function sendEstop()  { socket.emit('cmd_estop', {active: !estopOn}); }
function sendStart()  { socket.emit('cmd_start'); }
function sendReset()  { socket.emit('cmd_reset'); }
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Rutas Flask
# ─────────────────────────────────────────────────────────────────────────────
def _placeholder(text: str, w: int = 580, h: int = 435):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.putText(img, text, (20, h // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (40, 40, 40), 1)
    return img


def _mjpeg(q: queue.Queue, offline_label: str):
    ph = _placeholder(offline_label)
    while True:
        try:
            frame = q.get(timeout=1.0)
        except queue.Empty:
            frame = ph
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ok:
            continue
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
               + buf.tobytes() + b'\r\n')


@app.route('/')
def index():
    return render_template_string(_HTML)


@app.route('/video/lane')
def video_lane():
    return Response(_mjpeg(lane_q, 'lane_node offline'),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/video/detect')
def video_detect():
    return Response(_mjpeg(detect_q, 'detect_node offline'),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# ─────────────────────────────────────────────────────────────────────────────
# Eventos SocketIO (comandos del navegador → ROS)
# ─────────────────────────────────────────────────────────────────────────────
@socketio.on('cmd_mode')
def on_mode(data):
    if 'mode' not in _pub:
        return
    msg = String()
    msg.data = str(data.get('mode', 'manual'))
    _pub['mode'].publish(msg)


@socketio.on('cmd_estop')
def on_estop(data):
    if 'estop' not in _pub:
        return
    msg = Bool()
    msg.data = bool(data.get('active', False))
    _pub['estop'].publish(msg)


@socketio.on('cmd_start')
def on_start():
    if 'start' not in _pub:
        return
    msg = Bool()
    msg.data = True
    _pub['start'].publish(msg)


@socketio.on('cmd_reset')
def on_reset():
    if 'reset' not in _pub:
        return
    msg = Bool()
    msg.data = True
    _pub['reset'].publish(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast telemetría a 5 Hz
# ─────────────────────────────────────────────────────────────────────────────
def _telem_loop():
    while True:
        try:
            socketio.emit('telemetry', {
                'mode':           state.mode,
                'estop':          state.estop,
                'steering_error': state.steering_error,
                'lane_detected':  state.lane_detected,
                'crosswalk':      state.crosswalk,
                'sign':           state.sign,
                'tag_id':         state.tag_id,
                'distance':       state.distance,
                'odom_vel':       state.odom_vel,
                'fsm_state':      state.fsm_state,
                'log':            state.decision_log[-25:],
            })
        except Exception:
            pass
        time.sleep(0.2)


# ─────────────────────────────────────────────────────────────────────────────
# Thread ROS 2
# ─────────────────────────────────────────────────────────────────────────────
def _ros_spin():
    rclpy.init()
    node = DashNode()
    try:
        rclpy.spin(node)
    except Exception:
        pass
    finally:
        try:
            node.destroy_node()
        except Exception:
            pass
        rclpy.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=_ros_spin, daemon=True).start()
    time.sleep(0.8)

    threading.Thread(target=_telem_loop, daemon=True).start()

    print("Dashboard → http://0.0.0.0:8080")
    try:
        socketio.run(app, host='0.0.0.0', port=8080,
                     use_reloader=False, allow_unsafe_werkzeug=True)
    except TypeError:
        socketio.run(app, host='0.0.0.0', port=8080, use_reloader=False)
