#!/usr/bin/env python3
"""
dashboard.py — Dashboard web RoboUAQ 2026

Flask + SocketIO en puerto 8080.
Acceder desde la laptop: http://<IP_JETSON>:8080

Pestañas:
  Cam0    — stream MJPEG de /lane/debug_image (bird's eye + overlays)
  Cam1    — stream MJPEG de /detection/debug_image (AprilTags + señales)
  PID     — gráfica tiempo real vel_target vs vel_odom
  FSM     — estado actual + log de decisiones
  Control — botones MANUAL/OBSERVE/AUTO, ESTOP, START, RESET + estado actual
"""

import threading
import time
import sys
import os

# ROS 2
try:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import Float32, Int32, String, Bool
    from sensor_msgs.msg import CompressedImage
    ROS_OK = True
except ImportError:
    ROS_OK = False

# Flask + SocketIO
try:
    from flask import Flask, Response, render_template_string
    from flask_socketio import SocketIO, emit
    FLASK_OK = True
except ImportError:
    FLASK_OK = False
    print('ERROR: pip3 install flask flask-socketio --break-system-packages')
    sys.exit(1)

import base64


# ─────────────────────────────────────────────
#  HTML / JS del dashboard
# ─────────────────────────────────────────────
HTML = '''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>RoboUAQ 2026 Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #1a1a2e; color: #e0e0e0; font-family: monospace; }
.header { background: #16213e; padding: 12px 20px; display: flex; align-items: center; gap: 20px; }
.header h1 { font-size: 1.2em; color: #00b4d8; }
.mode-badge { padding: 4px 12px; border-radius: 4px; font-weight: bold; font-size: 0.9em; }
.mode-manual   { background: #666; color: #fff; }
.mode-observe  { background: #0077b6; color: #fff; }
.mode-auto     { background: #2d6a4f; color: #fff; }
.estop-badge   { background: #d00000; color: #fff; padding: 4px 12px; border-radius: 4px; font-weight: bold; display: none; }
.tabs { display: flex; background: #16213e; border-bottom: 2px solid #0f3460; }
.tab { padding: 10px 20px; cursor: pointer; color: #888; border-bottom: 3px solid transparent; }
.tab.active { color: #00b4d8; border-bottom-color: #00b4d8; }
.panel { display: none; padding: 16px; }
.panel.active { display: block; }
.cam-frame { width: 100%; max-width: 640px; border: 2px solid #0f3460; border-radius: 4px; background: #000; }
img.cam-stream { width: 100%; display: block; background: #111; }
.chart-container { height: 200px; background: #0f3460; border-radius: 4px; padding: 8px; position: relative; overflow: hidden; }
canvas#pidChart { width: 100%; height: 100%; }
.log-box { background: #0f3460; border-radius: 4px; padding: 12px; height: 300px; overflow-y: auto; font-size: 0.85em; line-height: 1.6; }
.log-entry { border-bottom: 1px solid #1a3a6e; padding: 2px 0; }
.fsm-state { font-size: 2em; font-weight: bold; color: #00b4d8; padding: 16px 0; }
.ctrl-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; max-width: 500px; }
button { padding: 12px 20px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 1em; }
.btn-manual  { background: #555; color: #fff; }
.btn-observe { background: #0077b6; color: #fff; }
.btn-auto    { background: #2d6a4f; color: #fff; }
.btn-estop   { background: #d00000; color: #fff; grid-column: 1/-1; font-size: 1.2em; }
.btn-start   { background: #2d6a4f; color: #fff; }
.btn-reset   { background: #7b2d8b; color: #fff; }
.telemetry { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 20px; }
.telem-card { background: #0f3460; border-radius: 6px; padding: 12px; text-align: center; }
.telem-val { font-size: 1.8em; font-weight: bold; color: #00b4d8; }
.telem-lbl { font-size: 0.75em; color: #888; margin-top: 4px; }
</style>
</head>
<body>
<div class="header">
  <h1>🤖 RoboUAQ 2026</h1>
  <span class="mode-badge mode-manual" id="modeBadge">MANUAL</span>
  <span class="estop-badge" id="estopBadge">!! ESTOP !!</span>
  <span style="margin-left:auto; font-size:0.8em; color:#888" id="connStatus">Conectando...</span>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('cam0')">Cam0 (Lane)</div>
  <div class="tab" onclick="showTab('cam1')">Cam1 (Detect)</div>
  <div class="tab" onclick="showTab('pid')">PID</div>
  <div class="tab" onclick="showTab('fsm')">FSM</div>
  <div class="tab" onclick="showTab('ctrl')">Control</div>
</div>

<div id="cam0" class="panel active">
  <div class="cam-frame">
    <img id="imgCam0" class="cam-stream" src="/stream/cam0" alt="Cam0 stream">
  </div>
</div>

<div id="cam1" class="panel">
  <div class="cam-frame">
    <img id="imgCam1" class="cam-stream" src="/stream/cam1" alt="Cam1 stream">
  </div>
</div>

<div id="pid" class="panel">
  <div class="chart-container">
    <canvas id="pidChart"></canvas>
  </div>
  <div class="telemetry">
    <div class="telem-card"><div class="telem-val" id="tvTarget">0.00</div><div class="telem-lbl">Target m/s</div></div>
    <div class="telem-card"><div class="telem-val" id="tvOdom">0.00</div><div class="telem-lbl">Odom m/s</div></div>
    <div class="telem-card"><div class="telem-val" id="tvSteer">1500</div><div class="telem-lbl">Steer µs</div></div>
    <div class="telem-card"><div class="telem-val" id="tvEncoder">0</div><div class="telem-lbl">Encoder ticks</div></div>
  </div>
</div>

<div id="fsm" class="panel">
  <div class="fsm-state" id="fsmState">IDLE</div>
  <div style="margin-bottom:8px; color:#888;">Log de decisiones:</div>
  <div class="log-box" id="decisionLog"></div>
</div>

<div id="ctrl" class="panel">
  <div class="ctrl-grid">
    <button class="btn-manual"  onclick="setMode('manual')">MANUAL</button>
    <button class="btn-observe" onclick="setMode('observe')">OBSERVE</button>
    <button class="btn-auto"    onclick="setMode('auto')">AUTO</button>
    <button class="btn-estop"   onclick="triggerEstop()">⚠ ESTOP</button>
    <button class="btn-start"   onclick="navStart()">▶ START</button>
    <button class="btn-reset"   onclick="navReset()">↺ RESET</button>
  </div>
  <div class="telemetry" style="margin-top:24px;">
    <div class="telem-card"><div class="telem-val" id="ctrlVel">0.00</div><div class="telem-lbl">Velocidad m/s</div></div>
    <div class="telem-card"><div class="telem-val" id="ctrlSteer">1500</div><div class="telem-lbl">Steering µs</div></div>
    <div class="telem-card"><div class="telem-val" id="ctrlMode">MANUAL</div><div class="telem-lbl">Modo</div></div>
    <div class="telem-card"><div class="telem-val" id="ctrlSign">—</div><div class="telem-lbl">Señal detectada</div></div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
const socket = io();
let velHistory = [], odomHistory = [];
const MAX_HIST = 100;
let estopActive = false;

socket.on('connect', () => {
  document.getElementById('connStatus').textContent = 'Conectado ✓';
});
socket.on('disconnect', () => {
  document.getElementById('connStatus').textContent = 'Sin conexión ✗';
});

socket.on('telemetry', data => {
  document.getElementById('tvTarget').textContent = data.target_vel.toFixed(2);
  document.getElementById('tvOdom').textContent = data.odom_vel.toFixed(2);
  document.getElementById('tvSteer').textContent = data.steer;
  document.getElementById('tvEncoder').textContent = data.encoder;
  document.getElementById('ctrlVel').textContent = data.odom_vel.toFixed(2);
  document.getElementById('ctrlSteer').textContent = data.steer;

  velHistory.push(data.target_vel);
  odomHistory.push(data.odom_vel);
  if (velHistory.length > MAX_HIST) { velHistory.shift(); odomHistory.shift(); }
  drawChart();
});

socket.on('mode', data => {
  const b = document.getElementById('modeBadge');
  b.textContent = data.mode.toUpperCase();
  b.className = 'mode-badge mode-' + data.mode;
  document.getElementById('ctrlMode').textContent = data.mode.toUpperCase();
});

socket.on('estop', data => {
  estopActive = data.active;
  document.getElementById('estopBadge').style.display = data.active ? 'inline' : 'none';
});

socket.on('fsm_state', data => {
  document.getElementById('fsmState').textContent = data.state;
});

socket.on('decision_log', data => {
  const box = document.getElementById('decisionLog');
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.textContent = new Date().toLocaleTimeString() + ' — ' + data.message;
  box.insertBefore(entry, box.firstChild);
  if (box.children.length > 50) box.removeChild(box.lastChild);
});

socket.on('sign', data => {
  document.getElementById('ctrlSign').textContent = data.sign || '—';
});

function showTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(name).classList.add('active');
  event.target.classList.add('active');
}

function setMode(m) { socket.emit('set_mode', {mode: m}); }
function triggerEstop() { socket.emit('set_estop', {active: !estopActive}); }
function navStart() { socket.emit('nav_start', {}); }
function navReset() { socket.emit('nav_reset', {}); }

function drawChart() {
  const canvas = document.getElementById('pidChart');
  const ctx = canvas.getContext('2d');
  canvas.width = canvas.offsetWidth;
  canvas.height = canvas.offsetHeight;
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const drawLine = (data, color) => {
    if (!data.length) return;
    const maxV = 1.0;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = (i / MAX_HIST) * W;
      const y = H - ((v + maxV) / (2 * maxV)) * H;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  };

  // Línea cero
  ctx.strokeStyle = '#444'; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(0, H/2); ctx.lineTo(W, H/2); ctx.stroke();
  ctx.setLineDash([]);

  drawLine(velHistory, '#00b4d8');
  drawLine(odomHistory, '#ff6b6b');

  ctx.fillStyle = '#00b4d8'; ctx.fillText('— Target', 10, 15);
  ctx.fillStyle = '#ff6b6b'; ctx.fillText('— Odom', 80, 15);
}
</script>
</body>
</html>
'''


# ─────────────────────────────────────────────
#  Bridge ROS 2 → SocketIO
# ─────────────────────────────────────────────
class RosBridge:
    """Lee topics ROS 2 y los reenvía por SocketIO al browser."""

    def __init__(self, socketio_instance):
        self.sio = socketio_instance
        self.latest_cam0 = None
        self.latest_cam1 = None
        self.target_vel = 0.0
        self.odom_vel = 0.0
        self.steer = 1500
        self.encoder = 0
        self.mode = 'manual'
        self.estop = False
        self.fsm_state = 'IDLE'
        self.sign = ''

        if not ROS_OK:
            return

        rclpy.init()
        self.node = rclpy.create_node('dashboard_bridge')

        # Subscribers de imágenes
        self.node.create_subscription(
            CompressedImage, '/lane/debug_image', self._cam0_cb, 10
        )
        self.node.create_subscription(
            CompressedImage, '/detection/debug_image', self._cam1_cb, 10
        )
        # Telemetría
        self.node.create_subscription(Float32, '/cmd_vel', self._cmdvel_cb, 10)
        self.node.create_subscription(Float32, '/odom_vel', self._odom_cb, 10)
        self.node.create_subscription(Int32, '/steer_status', self._steer_cb, 10)
        self.node.create_subscription(Int32, '/encoder_ticks', self._enc_cb, 10)
        # Estado
        self.node.create_subscription(String, '/mode', self._mode_cb, 10)
        self.node.create_subscription(Bool, '/estop', self._estop_cb, 10)
        self.node.create_subscription(String, '/nav/state', self._fsm_cb, 10)
        self.node.create_subscription(String, '/nav/decision_log', self._log_cb, 10)
        self.node.create_subscription(String, '/detection/sign', self._sign_cb, 10)

        # Publishers (para botones del dashboard)
        self.pub_mode = self.node.create_publisher(String, '/mode', 10)
        self.pub_estop = self.node.create_publisher(Bool, '/estop', 10)
        self.pub_start = self.node.create_publisher(Bool, '/nav/start', 10)
        self.pub_reset = self.node.create_publisher(Bool, '/nav/reset', 10)

        # Spin en thread separado
        self.spin_thread = threading.Thread(
            target=lambda: rclpy.spin(self.node), daemon=True
        )
        self.spin_thread.start()

        # Timer de telemetría a 10Hz
        self._telem_thread = threading.Thread(
            target=self._telem_loop, daemon=True
        )
        self._telem_thread.start()

    # ── Callbacks ──
    def _cam0_cb(self, msg):
        self.latest_cam0 = bytes(msg.data)

    def _cam1_cb(self, msg):
        self.latest_cam1 = bytes(msg.data)

    def _cmdvel_cb(self, msg):
        self.target_vel = msg.data

    def _odom_cb(self, msg):
        self.odom_vel = msg.data

    def _steer_cb(self, msg):
        self.steer = msg.data

    def _enc_cb(self, msg):
        self.encoder = msg.data

    def _mode_cb(self, msg):
        self.mode = msg.data
        self.sio.emit('mode', {'mode': self.mode})

    def _estop_cb(self, msg):
        self.estop = msg.data
        self.sio.emit('estop', {'active': self.estop})

    def _fsm_cb(self, msg):
        self.fsm_state = msg.data
        self.sio.emit('fsm_state', {'state': self.fsm_state})

    def _log_cb(self, msg):
        self.sio.emit('decision_log', {'message': msg.data})

    def _sign_cb(self, msg):
        self.sign = msg.data
        self.sio.emit('sign', {'sign': self.sign})

    def _telem_loop(self):
        while True:
            self.sio.emit('telemetry', {
                'target_vel': self.target_vel,
                'odom_vel': self.odom_vel,
                'steer': self.steer,
                'encoder': self.encoder,
            })
            time.sleep(0.1)

    # ── Comandos desde el dashboard ──
    def set_mode(self, mode_str):
        if not ROS_OK:
            return
        msg = String()
        msg.data = mode_str
        self.pub_mode.publish(msg)

    def set_estop(self, active):
        if not ROS_OK:
            return
        msg = Bool()
        msg.data = bool(active)
        self.pub_estop.publish(msg)

    def nav_start(self):
        if not ROS_OK:
            return
        msg = Bool()
        msg.data = True
        self.pub_start.publish(msg)

    def nav_reset(self):
        if not ROS_OK:
            return
        msg = Bool()
        msg.data = True
        self.pub_reset.publish(msg)


# ─────────────────────────────────────────────
#  Flask app
# ─────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'robocar2026'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')
bridge = None  # se inicializa en __main__


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/stream/cam0')
def stream_cam0():
    def gen():
        while True:
            if bridge and bridge.latest_cam0:
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n'
                    + bridge.latest_cam0
                    + b'\r\n'
                )
            time.sleep(0.05)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/stream/cam1')
def stream_cam1():
    def gen():
        while True:
            if bridge and bridge.latest_cam1:
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n'
                    + bridge.latest_cam1
                    + b'\r\n'
                )
            time.sleep(0.05)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')


# SocketIO events del cliente
@socketio.on('set_mode')
def on_set_mode(data):
    if bridge:
        bridge.set_mode(data.get('mode', 'manual'))


@socketio.on('set_estop')
def on_set_estop(data):
    if bridge:
        bridge.set_estop(data.get('active', True))


@socketio.on('nav_start')
def on_nav_start(data):
    if bridge:
        bridge.nav_start()


@socketio.on('nav_reset')
def on_nav_reset(data):
    if bridge:
        bridge.nav_reset()


if __name__ == '__main__':
    bridge = RosBridge(socketio)
    print('Dashboard disponible en http://0.0.0.0:8080')
    socketio.run(app, host='0.0.0.0', port=8080, debug=False, use_reloader=False)
