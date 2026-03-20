#!/usr/bin/env python3
"""
MAVLINK BOAT EMULATOR
─────────────────────
Dépendances : pip install PyQt6
(pas besoin de PyQtWebEngine)

Au lancement, la fenêtre PyQt6 s'ouvre avec les 6 cartes drones.
La carte OpenStreetMap s'ouvre automatiquement dans votre navigateur
et se met à jour en temps réel via WebSocket.
"""

import sys, os, socket, struct, threading, time, math, json, random
import http.server, webbrowser
from dataclasses import dataclass, field
from typing import List

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QComboBox, QPushButton, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon

# ──────────────────────────────────────────────────────────
#  COULEURS & STYLE
# ──────────────────────────────────────────────────────────

COLORS = ["#38BDF8", "#FB923C", "#4ADE80", "#F472B6", "#A78BFA", "#FACC15"]

QSS = """
* {
    background: #0B0F1C;
    color: #CBD5E1;
}
QWidget {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
}
QMainWindow { background: #0B0F1C; }
QFrame#card {
    background: #111827;
    border: 1px solid #1E293B;
    border-radius: 8px;
}
QComboBox {
    background: #1E293B;
    color: #94A3B8;
    border: 1px solid #334155;
    border-radius: 3px;
    padding: 2px 6px;
    min-height: 20px;
}
QComboBox::drop-down { border: none; width: 14px; }
QComboBox QAbstractItemView {
    background: #1E293B;
    color: #94A3B8;
    selection-background-color: #273549;
}
QPushButton {
    background: #1E293B;
    color: #94A3B8;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 3px 8px;
}
QPushButton:hover  { background: #273549; color: #E2E8F0; }
QPushButton:pressed { background: #0F172A; }
QSlider::groove:vertical {
    background: #1E293B;
    border: 1px solid #334155;
    width: 6px;
    border-radius: 3px;
}
QSlider::sub-page:vertical { border-radius: 3px; }
QSlider::handle:vertical {
    background: #E2E8F0;
    border: 2px solid #0B0F1C;
    height: 14px;
    width: 14px;
    margin: 0 -4px;
    border-radius: 7px;
}
"""

# ──────────────────────────────────────────────────────────
#  PAGE HTML CARTE (Leaflet + WebSocket client)
# ──────────────────────────────────────────────────────────

MAP_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>MAVLink Boat Emulator — Carte</title>
<style>
  html, body { margin:0; padding:0; background:#0B0F1C; height:100%; font-family:monospace; }
  #map { width:100%; height:100vh; }
  #status {
    position:fixed; top:10px; right:10px; z-index:9999;
    background:#111827cc; border:1px solid #1E293B; border-radius:6px;
    padding:6px 12px; color:#94A3B8; font-size:11px; pointer-events:none;
  }
  #status.ok { color:#4ADE80; border-color:#166534; }
  .leaflet-tile-pane { filter: brightness(0.78) saturate(0.6) hue-rotate(195deg); }
  .leaflet-control-attribution { display:none; }
  .drone-tip {
    background:#111827 !important;
    border:1px solid #334155 !important;
    color:#CBD5E1 !important;
    font-family:monospace !important;
    font-size:11px !important;
    padding:3px 7px !important;
    border-radius:4px !important;
    white-space:nowrap !important;
    box-shadow: none !important;
  }
  .drone-tip::before { display:none !important; }
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
</head>
<body>
<div id="map"></div>
<div id="status">⚡ Connexion...</div>
<script>
const COLORS  = ["#38BDF8","#FB923C","#4ADE80","#F472B6","#A78BFA","#FACC15"];
const WS_PORT = 8765;

const map = L.map('map', {
  center: [43.255, 5.348],
  zoom: 12,
  zoomControl: true,
  attributionControl: false,
});
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
}).addTo(map);

function boatSVG(color, heading) {
  const angle = (heading + 180) % 360;
  return L.divIcon({
    className: '',
    html: `<div style="transform:rotate(${angle}deg);width:24px;height:24px;">
      <svg width="24" height="24" viewBox="-12 -12 24 24" xmlns="http://www.w3.org/2000/svg">
        <polygon points="0,-10 7,8 0,4 -7,8"
          fill="${color}" stroke="#0B0F1C" stroke-width="1.5" stroke-linejoin="round"/>
      </svg></div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });
}

const BASE_LAT = 43.2730, BASE_LON = 5.3150, LON_STEP = 0.0070;
const markers = [], polylines = [];

for (let i = 0; i < 6; i++) {
  const m = L.marker([BASE_LAT, BASE_LON + i * LON_STEP],
    { icon: boatSVG(COLORS[i], 0) }).addTo(map);
  m.bindTooltip(`Drone ${i+1}`, {
    permanent: false, direction: 'top', className: 'drone-tip',
  });
  markers.push(m);
  polylines.push(L.polyline([], {
    color: COLORS[i], weight: 2.5, opacity: 0.85, smoothFactor: 1,
  }).addTo(map));
}

// ── WebSocket ─────────────────────────────────────────────
const statusEl = document.getElementById('status');
let ws, reconnectTimer;

function connect() {
  ws = new WebSocket(`ws://127.0.0.1:${WS_PORT}`);

  ws.onopen = () => {
    statusEl.textContent = '● Connecté';
    statusEl.className = 'ok';
    clearTimeout(reconnectTimer);
  };

  ws.onmessage = (evt) => {
    try {
      const data = JSON.parse(evt.data);
      for (let i = 0; i < 6; i++) {
        const d = data[i];
        if (!d) continue;
        markers[i].setLatLng([d.lat, d.lon]);
        markers[i].setIcon(boatSVG(COLORS[i], d.heading));
        const trail = d.trail_lat.map((lt, j) => [lt, d.trail_lon[j]]);
        polylines[i].setLatLngs(trail);
      }
    } catch(e) {}
  };

  ws.onclose = () => {
    statusEl.textContent = '○ Déconnecté — reconnexion...';
    statusEl.className = '';
    reconnectTimer = setTimeout(connect, 2000);
  };

  ws.onerror = () => ws.close();
}
connect();
</script>
</body>
</html>"""

# ──────────────────────────────────────────────────────────
#  MAVLINK v2
# ──────────────────────────────────────────────────────────

def _crc_x25(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        tmp = b ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc

_CRC_EXTRA = {0: 50, 33: 104, 74: 20, 147: 154}

def _frame(sysid: int, msgid: int, payload: bytes) -> bytes:
    seq = random.randint(0, 255)
    hdr = struct.pack("<BBBBBBBBBB",
        0xFD, len(payload), 0, 0, seq, sysid, 1,
        msgid & 0xFF, (msgid >> 8) & 0xFF, (msgid >> 16) & 0xFF)
    crc_d = hdr[1:] + payload + bytes([_CRC_EXTRA.get(msgid, 0)])
    return hdr + payload + struct.pack("<H", _crc_x25(crc_d))

def mav_heartbeat(sid):
    return _frame(sid, 0, struct.pack("<IBBBBB", 0, 10, 8, 0, 4, 3))

def mav_position(sid, lat, lon, hdg, spd):
    t = int(time.time() * 1000) & 0xFFFFFFFF
    vx = int(spd * math.sin(math.radians(hdg)) * 100)
    vy = int(spd * math.cos(math.radians(hdg)) * 100)
    return _frame(sid, 33, struct.pack("<IiiiiihhHH",
        t, int(lat*1e7), int(lon*1e7), 0, 0, vx, vy, 0,
        int(hdg*100) % 36000, 0))

def mav_vfr_hud(sid, airspeed, groundspeed, heading, throttle, alt, climb):
    """MAVLink VFR_HUD (#74) — vitesse, cap, throttle."""
    return _frame(sid, 74, struct.pack("<ffffhH",
        float(airspeed),                 # airspeed m/s
        float(groundspeed),              # groundspeed m/s
        float(alt),                      # alt m
        float(climb),                    # climb m/s
        int(heading) % 360,              # heading deg (int16)
        int(max(0, min(100, throttle))), # throttle % (uint16)
    ))

def mav_battery(sid, mv, ma, pct):
    # BATTERY_STATUS: id, battery_function, type, temperature, voltages[10],
    #                 current_battery, current_consumed, energy_consumed, battery_remaining
    vols = [int(mv)] + [0xFFFF]*9   # 10 valeurs
    return _frame(sid, 147, struct.pack("<BBBhHHHHHHHHHHhiib",
        0,           # id
        0,           # battery_function
        0,           # type
        -1,          # temperature (invalide)
        *vols,       # voltages[10]  → 10 × H
        int(ma/10),  # current_battery (cA)
        -1,          # current_consumed (mAh) invalide
        -1,          # energy_consumed (hJ) invalide
        int(pct),    # battery_remaining (%)
    ))

# ──────────────────────────────────────────────────────────
#  SERVEUR MAVLink UDP/TCP
# ──────────────────────────────────────────────────────────

class MavServer:
    """
    Trois modes :
      UDP        — bind local, attend que le GCS envoie un paquet (Mission Planner)
      UDP OUT    — pousse directement vers target_ip:target_port (serveur Python udpin)
      TCP        — serveur TCP, attend les connexions entrantes
    """
    def __init__(self, port: int, proto: str = "UDP",
                 target_ip: str = "127.0.0.1", target_port: int = 0):
        self.port        = port
        self.proto       = proto
        self.target_ip   = target_ip
        self.target_port = target_port or port
        self.sock        = None
        self.active      = False
        self.clients     = []
        self._lock       = threading.Lock()
        self.frames_sent = 0
        self.last_error  = ""

    def start(self) -> bool:
        self.stop()
        try:
            if self.proto == "UDP OUT":
                # Pas de bind — on crée juste un socket émetteur
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.settimeout(0.3)
                with self._lock:
                    self.clients = [(self.target_ip, self.target_port)]
                self.active = True

            elif self.proto == "UDP":
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.sock.bind(("0.0.0.0", self.port))
                self.sock.settimeout(0.3)
                self.active = True
                threading.Thread(target=self._udp_rx, daemon=True).start()

            else:  # TCP
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.sock.bind(("0.0.0.0", self.port))
                self.sock.listen(5)
                self.sock.settimeout(0.3)
                self.active = True
                threading.Thread(target=self._tcp_rx, daemon=True).start()

            return True
        except Exception as e:
            self.last_error = str(e)[:40]
            return False

    def stop(self):
        self.active = False
        with self._lock:
            if self.proto != "UDP OUT":
                for c in self.clients:
                    try: c.close()
                    except: pass
            self.clients.clear()
        if self.sock:
            try: self.sock.close()
            except: pass
        self.sock = None

    def _udp_rx(self):
        while self.active:
            try:
                _, addr = self.sock.recvfrom(512)
                with self._lock:
                    if addr not in self.clients: self.clients.append(addr)
            except: pass

    def _tcp_rx(self):
        while self.active:
            try:
                conn, _ = self.sock.accept()
                conn.settimeout(5)
                with self._lock: self.clients.append(conn)
            except: pass

    def send(self, data: bytes):
        if not self.active: return
        with self._lock:
            dead = []
            if self.proto in ("UDP", "UDP OUT"):
                for a in self.clients:
                    try: self.sock.sendto(data, a); self.frames_sent += 1
                    except: dead.append(a)
                if self.proto == "UDP":   # en UDP OUT la cible est fixe
                    for d in dead:
                        if d in self.clients: self.clients.remove(d)
            else:
                for c in self.clients:
                    try: c.sendall(data); self.frames_sent += 1
                    except: dead.append(c)
                for d in dead:
                    if d in self.clients: self.clients.remove(d)

    @property
    def n_clients(self):
        with self._lock: return len(self.clients)

# ──────────────────────────────────────────────────────────
#  SERVEUR HTTP (sert la carte HTML)
# ──────────────────────────────────────────────────────────

MAP_HTML_BYTES = MAP_HTML.encode("utf-8")
HTTP_PORT = 8766

class _MapHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(MAP_HTML_BYTES)))
            self.end_headers()
            self.wfile.write(MAP_HTML_BYTES)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # navigateur a fermé la connexion — normal sous Windows
    def log_message(self, *args): pass  # silence
    def handle_error(self, request, client_address): pass  # silence erreurs réseau

def _start_http():
    srv = http.server.HTTPServer(("127.0.0.1", HTTP_PORT), _MapHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

# ──────────────────────────────────────────────────────────
#  SERVEUR WEBSOCKET (envoie la position des drones)
#  Implémentation minimale RFC 6455 sans dépendances
# ──────────────────────────────────────────────────────────

WS_PORT = 8765

import base64, hashlib

class _WSClient:
    def __init__(self, conn: socket.socket):
        self.conn = conn
        self.alive = True
        self._do_handshake()

    def _do_handshake(self):
        try:
            data = self.conn.recv(4096).decode("utf-8", errors="replace")
            key = ""
            for line in data.split("\r\n"):
                if "Sec-WebSocket-Key" in line:
                    key = line.split(": ", 1)[1].strip()
            magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept = base64.b64encode(
                hashlib.sha1((key + magic).encode()).digest()
            ).decode()
            resp = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            )
            self.conn.sendall(resp.encode())
        except:
            self.alive = False

    def send_text(self, text: str):
        if not self.alive: return
        try:
            payload = text.encode("utf-8")
            n = len(payload)
            if n < 126:
                header = struct.pack("BB", 0x81, n)
            elif n < 65536:
                header = struct.pack("!BBH", 0x81, 126, n)
            else:
                header = struct.pack("!BBQ", 0x81, 127, n)
            self.conn.sendall(header + payload)
        except:
            self.alive = False

    def close(self):
        self.alive = False
        try: self.conn.close()
        except: pass


class WSServer:
    def __init__(self):
        self.clients: List[_WSClient] = []
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", WS_PORT))
        self._sock.listen(10)
        self._sock.settimeout(1.0)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self._sock.accept()
                conn.settimeout(5)
                client = _WSClient(conn)
                if client.alive:
                    with self._lock: self.clients.append(client)
            except: pass

    def broadcast(self, text: str):
        with self._lock:
            dead = []
            for c in self.clients:
                c.send_text(text)
                if not c.alive: dead.append(c)
            for d in dead:
                d.close()
                self.clients.remove(d)

# ──────────────────────────────────────────────────────────
#  MODÈLE DRONE
# ──────────────────────────────────────────────────────────

_BASE_LAT  = 43.2730
_BASE_LON  = 5.3150
_LON_STEP  = 0.0070
_HEADING   = 0.0
_MAX_KN    = 15.0

@dataclass
class Drone:
    idx: int
    lat: float = 0.0
    lon: float = 0.0
    heading: float = _HEADING
    throttle: float = 0.0
    speed_ms: float = 0.0
    voltage_mv: float = 12600.0
    current_ma: float = 0.0
    battery_pct: float = 100.0
    running: bool = False
    trail_lat: List[float] = field(default_factory=list)
    trail_lon: List[float] = field(default_factory=list)

    def reset(self):
        self.lat = _BASE_LAT
        self.lon = _BASE_LON + self.idx * _LON_STEP
        self.speed_ms = 0.0; self.throttle = 0.0
        self.voltage_mv = 12600.0; self.current_ma = 0.0
        self.battery_pct = 100.0; self.running = False
        self.trail_lat = [self.lat]; self.trail_lon = [self.lon]

    def step(self, dt: float):
        max_ms = _MAX_KN * 0.5144
        target = self.throttle * max_ms if self.running else 0.0
        a = 0.08 if target > self.speed_ms else 0.18
        self.speed_ms += (target - self.speed_ms) * a

        if self.speed_ms > 0.05:
            d = self.speed_ms * dt
            self.lat += (d * math.cos(math.radians(self.heading))) / 111320.0
            self.lon += (d * math.sin(math.radians(self.heading))) / (
                111320.0 * math.cos(math.radians(self.lat)))
            if (not self.trail_lat
                    or abs(self.lat - self.trail_lat[-1]) > 0.000015
                    or abs(self.lon - self.trail_lon[-1]) > 0.000015):
                self.trail_lat.append(round(self.lat, 7))
                self.trail_lon.append(round(self.lon, 7))
                if len(self.trail_lat) > 600:
                    self.trail_lat.pop(0); self.trail_lon.pop(0)

        p = self.throttle ** 2
        self.current_ma = (50000 * p + 1500) if self.running else 1500
        self.battery_pct = max(0, self.battery_pct - 0.004 * p * dt * (1 if self.running else 0))
        self.voltage_mv = 12600 - (100 - self.battery_pct) * 38.0

    @property
    def speed_kn(self): return self.speed_ms * 1.94384

    @property
    def port(self): return 14551 + self.idx

# ──────────────────────────────────────────────────────────
#  CARTE DRONE (widget PyQt6)
# ──────────────────────────────────────────────────────────

class DroneCard(QFrame):
    def __init__(self, drone: Drone, server: MavServer):
        super().__init__()
        self.drone = drone; self.server = server
        self.setObjectName("card")
        self.setMinimumWidth(175)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._connected = False
        self._build()

    def _build(self):
        color = COLORS[self.drone.idx]
        n = self.drone.idx + 1
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        # Titre
        hdr = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color:#1E293B; font-size:9px;")
        lbl = QLabel(f"DRONE {n}")
        lbl.setStyleSheet(
            f"color:{color}; font-size:12px; font-weight:bold; letter-spacing:2px;")
        port_lbl = QLabel(f":{self.drone.port}")
        port_lbl.setStyleSheet("color:#334155; font-size:9px;")
        hdr.addWidget(self._dot); hdr.addWidget(lbl)
        hdr.addStretch(); hdr.addWidget(port_lbl)
        lay.addLayout(hdr)

        # Connexion
        conn = QHBoxLayout(); conn.setSpacing(4)
        self.proto_cb = QComboBox()
        self.proto_cb.addItems(["UDP OUT", "UDP", "TCP"])
        self.proto_cb.setFixedWidth(78)
        self.proto_cb.currentTextChanged.connect(self._on_proto_change)
        self.conn_btn = QPushButton("CONNECT")
        self.conn_btn.clicked.connect(self._toggle)
        conn.addWidget(self.proto_cb); conn.addWidget(self.conn_btn)
        lay.addLayout(conn)

        # IP cible (visible uniquement en mode UDP OUT)
        from PyQt6.QtWidgets import QLineEdit
        self.ip_row = QHBoxLayout(); self.ip_row.setSpacing(4)
        ip_lbl = QLabel("IP →"); ip_lbl.setStyleSheet("color:#475569; font-size:9px;")
        self.ip_edit = QLineEdit("127.0.0.1")
        self.ip_edit.setStyleSheet(
            "background:#1E293B; color:#94A3B8; border:1px solid #334155;"
            " border-radius:3px; padding:2px 5px; font-size:10px;")
        self.ip_edit.setFixedHeight(22)
        self.ip_row.addWidget(ip_lbl); self.ip_row.addWidget(self.ip_edit)
        lay.addLayout(self.ip_row)
        # Caché par défaut (visible seulement UDP OUT)
        ip_lbl.hide(); self.ip_edit.hide()
        self._ip_lbl = ip_lbl

        # Séparateur
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1); sep.setStyleSheet("background:#1E293B; border:none;")
        lay.addWidget(sep)

        # Levier + infos
        thr = QHBoxLayout(); thr.setSpacing(10)

        # Slider
        sc = QVBoxLayout(); sc.setAlignment(Qt.AlignmentFlag.AlignHCenter); sc.setSpacing(2)
        lmax = QLabel("MAX"); lmax.setStyleSheet("color:#334155; font-size:8px;")
        lmax.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, 100); self.slider.setValue(0)
        self.slider.setMinimumHeight(110); self.slider.setFixedWidth(26)
        self.slider.setStyleSheet(
            f"QSlider::sub-page:vertical {{ background:{color}; border-radius:3px; }}"
            f"QSlider::handle:vertical {{ background:{color}; border:2px solid #0B0F1C;"
            f" height:14px; width:14px; margin:0 -4px; border-radius:7px; }}")
        self.slider.valueChanged.connect(self._on_throttle)

        lmin = QLabel("0"); lmin.setStyleSheet("color:#334155; font-size:8px;")
        lmin.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        sc.addWidget(lmax)
        sc.addWidget(self.slider, alignment=Qt.AlignmentFlag.AlignHCenter)
        sc.addWidget(lmin)
        thr.addLayout(sc)

        # Stats
        ic = QVBoxLayout(); ic.setSpacing(4); ic.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.pct_lbl = QLabel("0%")
        self.pct_lbl.setStyleSheet(f"color:{color}; font-size:18px; font-weight:bold;")
        self.spd_lbl = QLabel("0.0 kn"); self.spd_lbl.setStyleSheet("color:#475569; font-size:10px;")
        self.amp_lbl = QLabel("0.0 A");  self.amp_lbl.setStyleSheet("color:#475569; font-size:10px;")
        self.bat_lbl = QLabel("🔋 100%"); self.bat_lbl.setStyleSheet("color:#4ADE80; font-size:10px;")
        for w in (self.pct_lbl, self.spd_lbl, self.amp_lbl, self.bat_lbl):
            ic.addWidget(w)
        ic.addStretch()
        thr.addLayout(ic)
        lay.addLayout(thr)

    def _on_proto_change(self, proto: str):
        visible = (proto == "UDP OUT")
        self._ip_lbl.setVisible(visible)
        self.ip_edit.setVisible(visible)

    def _toggle(self):
        if not self._connected:
            proto = self.proto_cb.currentText()
            self.server.proto = proto
            self.server.port  = self.drone.port
            if proto == "UDP OUT":
                self.server.target_ip   = self.ip_edit.text().strip() or "127.0.0.1"
                self.server.target_port = self.drone.port
            if self.server.start():
                self._connected = True
                self.conn_btn.setText("DISCONNECT")
                self.conn_btn.setStyleSheet(
                    "background:#064E3B; color:#34D399; border:1px solid #065F46;"
                    " border-radius:4px; padding:3px 8px;")
            else:
                self.conn_btn.setText("ERR")
                self.conn_btn.setToolTip(self.server.last_error)
        else:
            self.server.stop(); self._connected = False
            self.conn_btn.setText("CONNECT"); self.conn_btn.setStyleSheet("")

    def _on_throttle(self, val: int):
        self.drone.throttle = val / 100.0
        self.pct_lbl.setText(f"{val}%")

    def refresh(self):
        d = self.drone
        color = COLORS[d.idx]
        self.spd_lbl.setText(f"{d.speed_kn:.1f} kn")
        self.amp_lbl.setText(f"{d.current_ma/1000:.1f} A")
        bat = d.battery_pct
        bc = "#4ADE80" if bat > 50 else "#FACC15" if bat > 20 else "#F87171"
        self.bat_lbl.setText(f"🔋 {bat:.0f}%")
        self.bat_lbl.setStyleSheet(f"color:{bc}; font-size:10px;")
        # Dot actif
        c = color if (d.running and d.speed_ms > 0.1) else "#1E293B"
        self._dot.setStyleSheet(f"color:{c}; font-size:9px;")

# ──────────────────────────────────────────────────────────
#  FENÊTRE PRINCIPALE
# ──────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, ws_server: WSServer):
        super().__init__()
        self.ws = ws_server
        self.setWindowTitle("MAVLink Boat Emulator — Rade Sud de Marseille")
        self.resize(1300, 420)
        self.setStyleSheet(QSS)
        # Icône fenêtre
        import os
        _ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icone.ico')
        if os.path.exists(_ico):
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(_ico))

        self.drones  = [Drone(i) for i in range(6)]
        self.servers = [MavServer(d.port) for d in self.drones]
        for d in self.drones: d.reset()

        self._sim_time = 0.0
        self._build_ui()

        self._t_phys = QTimer(); self._t_phys.setInterval(100)
        self._t_phys.timeout.connect(self._tick_physics)

        self._t_ui = QTimer(); self._t_ui.setInterval(200)
        self._t_ui.timeout.connect(self._tick_ui); self._t_ui.start()

        self._t_mav = QTimer(); self._t_mav.setInterval(100)
        self._t_mav.timeout.connect(self._tick_mav); self._t_mav.start()

        # Ouvrir la carte dans le navigateur
        QTimer.singleShot(800, lambda: webbrowser.open(f"http://127.0.0.1:{HTTP_PORT}"))

    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(6)

        # Barre titre
        bar = QHBoxLayout()
        title = QLabel("⚓  MAVLink Boat Emulator")
        title.setStyleSheet(
            "color:#38BDF8; font-size:14px; font-weight:bold; letter-spacing:2px;")
        sub = QLabel("RADE SUD DE MARSEILLE  ·  6 DRONES  ·  MAVLink v2")
        sub.setStyleSheet("color:#334155; font-size:9px; letter-spacing:1px;")

        self.time_lbl = QLabel("00:00")
        self.time_lbl.setStyleSheet("color:#334155; font-size:11px; min-width:40px;")

        self.start_btn = QPushButton("▶  DÉMARRER TOUS")
        self.start_btn.setStyleSheet(
            "background:#14532D; color:#4ADE80; border:1px solid #166534;"
            " border-radius:4px; padding:4px 14px; font-weight:bold;")
        self.start_btn.clicked.connect(self._start)

        self.stop_btn = QPushButton("■  ARRÊTER")
        self.stop_btn.setStyleSheet(
            "background:#1E293B; color:#334155; border:1px solid #1E293B;"
            " border-radius:4px; padding:4px 12px; font-weight:bold;")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)

        self.reset_btn = QPushButton("↺  RESET")
        self.reset_btn.clicked.connect(self._reset)

        map_btn = QPushButton("🗺  OUVRIR CARTE")
        map_btn.setStyleSheet(
            "background:#1E293B; color:#38BDF8; border:1px solid #334155;"
            " border-radius:4px; padding:4px 12px;")
        map_btn.clicked.connect(lambda: webbrowser.open(f"http://127.0.0.1:{HTTP_PORT}"))

        bar.addWidget(title); bar.addSpacing(8); bar.addWidget(sub)
        bar.addStretch()
        bar.addWidget(self.time_lbl); bar.addSpacing(6)
        bar.addWidget(map_btn)
        bar.addWidget(self.start_btn); bar.addWidget(self.stop_btn)
        bar.addWidget(self.reset_btn)
        lay.addLayout(bar)

        # Séparateur
        div = QFrame(); div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1); div.setStyleSheet("background:#1E293B; border:none;")
        lay.addWidget(div)

        # 6 cartes drones
        cards_row = QHBoxLayout(); cards_row.setSpacing(6)
        self.cards: List[DroneCard] = []
        for d, s in zip(self.drones, self.servers):
            c = DroneCard(d, s); cards_row.addWidget(c); self.cards.append(c)
        lay.addLayout(cards_row)

        # Note carte
        note = QLabel(f"🗺  La carte s'ouvre dans votre navigateur  ·  "
                      f"WebSocket ws://127.0.0.1:{WS_PORT}  ·  "
                      f"HTTP http://127.0.0.1:{HTTP_PORT}")
        note.setStyleSheet("color:#1E293B; font-size:9px;")
        lay.addWidget(note)

    def _start(self):
        for d in self.drones: d.running = True
        self._t_phys.start()
        self.start_btn.setEnabled(False)
        self.start_btn.setStyleSheet(
            "background:#1E293B; color:#334155; border:1px solid #1E293B;"
            " border-radius:4px; padding:4px 14px;")
        self.stop_btn.setEnabled(True)
        self.stop_btn.setStyleSheet(
            "background:#7F1D1D; color:#FCA5A5; border:1px solid #991B1B;"
            " border-radius:4px; padding:4px 12px; font-weight:bold;")

    def _stop(self):
        for d in self.drones: d.running = False
        self._t_phys.stop()
        self.start_btn.setEnabled(True)
        self.start_btn.setStyleSheet(
            "background:#14532D; color:#4ADE80; border:1px solid #166534;"
            " border-radius:4px; padding:4px 14px; font-weight:bold;")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "background:#1E293B; color:#334155; border:1px solid #1E293B;"
            " border-radius:4px; padding:4px 12px;")

    def _reset(self):
        self._stop(); self._sim_time = 0.0
        for d, c in zip(self.drones, self.cards):
            d.reset(); c.slider.setValue(0)
        self._push_map()

    def _tick_physics(self):
        self._sim_time += 0.10
        for d in self.drones: d.step(0.10)

    def _tick_ui(self):
        for c in self.cards: c.refresh()
        self._push_map()
        m = int(self._sim_time) // 60; s = int(self._sim_time) % 60
        self.time_lbl.setText(f"{m:02d}:{s:02d}")

    def _push_map(self):
        payload = json.dumps([{
            "lat": d.lat, "lon": d.lon,
            "heading": d.heading,
            "trail_lat": d.trail_lat[-300:],
            "trail_lon": d.trail_lon[-300:],
        } for d in self.drones])
        self.ws.broadcast(payload)

    def _tick_mav(self):
        t = time.time()
        for d, srv in zip(self.drones, self.servers):
            if not srv.active: continue
            srv.send(mav_position(d.idx+1, d.lat, d.lon, d.heading, d.speed_ms))
            srv.send(mav_vfr_hud(d.idx+1, d.speed_ms, d.speed_ms,
                                  int(d.heading), int(d.throttle*100), 0.0, 0.0))
            if int(t * 10) % 10 == 0: srv.send(mav_heartbeat(d.idx+1))
            if int(t * 10) % 20 == 0:
                srv.send(mav_battery(d.idx+1, d.voltage_mv, d.current_ma, d.battery_pct))

    def closeEvent(self, _):
        self._stop()
        for srv in self.servers: srv.stop()

# ──────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Démarrage des serveurs locaux...")
    _start_http()
    print(f"  HTTP  : http://127.0.0.1:{HTTP_PORT}")
    ws_srv = WSServer()
    print(f"  WS    : ws://127.0.0.1:{WS_PORT}")
    print("Lancement de l'interface...")

    app = QApplication(sys.argv)
    win = MainWindow(ws_srv)
    win.show()
    sys.exit(app.exec())
