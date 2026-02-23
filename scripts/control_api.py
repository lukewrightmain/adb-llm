#!/usr/bin/env python3
"""Lightweight control API for cellswarm ring management.

Runs on the coding server, controls phones via ADB.
Endpoints:
  GET  /api/phones   - list available phones + model info
  GET  /api/status   - current ring/relaunch status
  POST /api/relaunch - start relaunch with {"n_phones": N}
  POST /api/stop     - stop current ring

Usage: python3 scripts/control_api.py [--port 8081]
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import subprocess
import threading
import time
import os
import re
import random
import sys
import urllib.request

CONTROL_PORT = 8081
ADB = os.path.expanduser("~/.local/bin/adb")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(PROJECT, "bin")
MODEL_DIR = "/data/local/tmp/cellswarm/models"
BIN_REMOTE = "cellswarm/bin"

LAYER_MAP = {
    "deepseek-coder-33b": 62, "deepseek*33b": 62,
    "qwen2.5-coder-32b": 64, "qwen*32b": 64,
    "qwen2.5-coder-7b": 28,  "qwen*7b": 28,
    "llama-3.1-8b": 32,      "llama*8b": 32,
    "llama-3.1-70b": 80,
    "codellama-34b": 48,
    "mistral-7b": 32,
    "deepseek-coder-6.7b": 32,
    "deepseek-coder-1.3b": 24,
}

state = {
    "status": "idle",       # idle | relaunching | live | error
    "phase": "",
    "message": "",
    "master_ip": None,
    "n_phones": 0,
    "http_port": 8080,
    "available_phones": 0,
}
lock = threading.Lock()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def adb(ip, cmd, timeout=10):
    r = subprocess.run(
        [ADB, "-s", f"{ip}:5555", "shell", cmd],
        capture_output=True, text=True, timeout=timeout
    )
    return r.stdout.strip()


def discover():
    """Discover all ADB-connected ethernet phones."""
    r = subprocess.run([ADB, "devices", "-l"], capture_output=True, text=True, timeout=10)
    ips = sorted(set(re.findall(r'(10\.\d+\.\d+\.\d+)(?=:5555)', r.stdout)))

    live = []
    for ip in ips:
        try:
            if "ok" in adb(ip, "echo ok", timeout=5):
                live.append(ip)
        except Exception:
            pass
    return live


def scan_models(phones):
    """Return info for phones that have a main model (>5GB)."""
    result = []
    for ip in phones:
        try:
            raw = adb(ip, f"ls -lSL {MODEL_DIR}/*.gguf 2>/dev/null", timeout=10)
            if not raw:
                continue
            main_model = draft_model = None
            main_name = draft_name = ""
            for line in raw.split('\n'):
                parts = line.split()
                if len(parts) < 5:
                    continue
                try:
                    size = int(parts[4])
                except ValueError:
                    continue
                path = parts[-1].strip()
                name = os.path.basename(path)
                if not main_model and size > 5_368_709_120:
                    main_model = path
                    main_name = name
                if not draft_model and size < 2_147_483_648:
                    draft_model = path
                    draft_name = name
            if main_model:
                result.append({
                    "ip": ip,
                    "model": main_model, "model_name": main_name,
                    "draft": draft_model or "", "draft_name": draft_name,
                })
        except Exception:
            pass
    return result


def detect_layers(model_name):
    name_lower = model_name.lower()
    for key, layers in LAYER_MAP.items():
        if key in name_lower:
            return layers
    return 62


def calc_layer_weights(n_phones, total_layers):
    rank0_reduction = 6
    rank0 = max(5, total_layers // n_phones - rank0_reduction)
    remaining = total_layers - rank0
    others = n_phones - 1
    per_other = remaining // others
    leftover = remaining % others
    weights = [rank0]
    for i in range(others):
        weights.append(per_other + (1 if i < leftover else 0))
    return weights


def kill_ring(phones):
    """Kill all cellswarm processes on given phones."""
    for ip in phones:
        try:
            adb(ip, "for n in cellswarm-master cellswarm-worker cellswarm-worker-spec mdns-advertise; do "
                     "pids=$(pidof $n 2>/dev/null) && kill -9 $pids 2>/dev/null; done")
        except Exception:
            pass
    time.sleep(4)
    # Second pass
    for ip in phones:
        try:
            adb(ip, "for n in cellswarm-master cellswarm-worker; do "
                     "pids=$(pidof $n 2>/dev/null) && kill -9 $pids 2>/dev/null; done")
        except Exception:
            pass
    time.sleep(2)


def set_state(**kwargs):
    with lock:
        state.update(kwargs)


def do_relaunch(n_phones, draft_max=24, ctx=2048, http_port=8080, threads=4):
    data_port = 9000 + random.randint(0, 999)
    signal_port = data_port + 1000

    try:
        # Phase 1: Discover
        set_state(status="relaunching", phase="discover", message="Discovering phones...")
        log(f"Relaunch requested: {n_phones} phones")
        all_phones = discover()
        log(f"  Found {len(all_phones)} phones")

        if len(all_phones) < n_phones:
            set_state(status="error", message=f"Only {len(all_phones)} phones available, need {n_phones}")
            return

        # Phase 2: Scan models
        set_state(phase="scan", message=f"Scanning models on {len(all_phones)} phones...")
        phone_info = scan_models(all_phones)
        log(f"  {len(phone_info)} phones have models")

        if len(phone_info) < n_phones:
            set_state(status="error", message=f"Only {len(phone_info)} phones have models, need {n_phones}")
            return

        # Prefer phones with draft model for master election
        with_draft = [p for p in phone_info if p["draft"]]
        without_draft = [p for p in phone_info if not p["draft"]]
        ordered = with_draft + without_draft
        selected = ordered[:n_phones]

        master = selected[0]
        master_ip = master["ip"]
        model_path = master["model"]
        draft_path = master["draft"]
        total_layers = detect_layers(master["model_name"])

        phones = [master_ip] + [p["ip"] for p in selected if p["ip"] != master_ip]
        lw = calc_layer_weights(n_phones, total_layers)
        lw_str = ",".join(str(w) for w in lw)

        log(f"  Master: {master_ip}, layers: {lw_str}, ports: {data_port}/{signal_port}")

        # Phase 3: Kill existing ring
        set_state(phase="kill", message="Stopping existing ring...")
        log("  Killing existing ring...")
        kill_ring(all_phones)

        # Phase 4: Start workers
        set_state(phase="workers", message=f"Starting {n_phones - 1} workers...")
        log(f"  Starting {n_phones - 1} workers...")

        for idx in range(1, n_phones):
            ip = phones[idx]
            next_idx = (idx + 1) % n_phones
            next_ip = phones[next_idx]
            pinfo = next((p for p in selected if p["ip"] == ip), None)
            if not pinfo:
                continue

            cmd = (
                f"cd /data/local/tmp && "
                f"taskset f0 ./{BIN_REMOTE}/cellswarm-worker "
                f"-m {pinfo['model']} "
                f"--world {n_phones} --rank {idx} "
                f"--master {master_ip} --next {next_ip} "
                f"--data-port {data_port} --signal-port {signal_port} "
                f"-lw {lw_str} -c {ctx} -n -1 -t {threads} -tb {threads} "
                f"--no-mmap --prefetch "
                f"> /data/local/tmp/cellswarm-worker.log 2>&1 &"
            )
            try:
                adb(ip, f"sh -c '{cmd}'", timeout=10)
            except Exception:
                pass
            set_state(message=f"Started worker {idx}/{n_phones - 1} ({ip})")

        # Phase 5: Wait for model load
        set_state(phase="loading", message="Loading model (0/120s)...")
        log("  Waiting for model load (120s)...")

        for i in range(120):
            time.sleep(1)
            set_state(message=f"Loading model ({i + 1}/120s)...")

        # Verify workers
        failed = []
        for idx in range(1, n_phones):
            ip = phones[idx]
            try:
                pid = adb(ip, "pidof cellswarm-worker", timeout=5)
                if not pid:
                    failed.append(ip)
            except Exception:
                failed.append(ip)

        if failed:
            set_state(status="error", message=f"Workers failed on: {', '.join(failed)}")
            log(f"  ERROR: Workers failed on {failed}")
            return

        log("  All workers running")

        # Phase 6: Start master
        set_state(phase="master", message="Starting master...")
        log("  Starting master...")
        next_ip = phones[1]
        draft_flags = ""
        if draft_path:
            draft_flags = f"--model-draft {draft_path} --draft-max {draft_max}"

        cmd = (
            f"cd /data/local/tmp && "
            f"SWARM_BATCH_PIPELINE=1 taskset f0 ./{BIN_REMOTE}/cellswarm-master "
            f"-m {model_path} {draft_flags} "
            f"--world {n_phones} --rank 0 "
            f"--master {master_ip} --next {next_ip} "
            f"--data-port {data_port} --signal-port {signal_port} "
            f"-lw {lw_str} -c {ctx} -t {threads} -tb {threads} "
            f"--no-mmap --prefetch "
            f"--host 0.0.0.0 --port {http_port} "
            f"-np 1 "
            f"> /data/local/tmp/cellswarm-master.log 2>&1 &"
        )
        try:
            adb(master_ip, f"sh -c '{cmd}'", timeout=10)
        except Exception:
            pass

        set_state(message="Waiting for HTTP server (15s)...")
        time.sleep(15)

        # Verify master
        try:
            pid = adb(master_ip, "pidof cellswarm-master", timeout=5)
            if not pid:
                set_state(status="error", message="Master failed to start — check logs")
                log("  ERROR: Master not running")
                return
        except Exception:
            set_state(status="error", message="Master unreachable after start")
            return

        # Start mDNS on phone
        try:
            adb(master_ip, "pkill -9 mdns-advertise", timeout=5)
        except Exception:
            pass
        try:
            adb(master_ip,
                f"sh -c 'cd /data/local/tmp && ./{BIN_REMOTE}/mdns-advertise cellswarm {master_ip} "
                f"> /data/local/tmp/mdns-advertise.log 2>&1 &'", timeout=5)
        except Exception:
            pass

        # Start cross-VLAN mDNS relay on host
        mdns_host = os.path.join(BIN, "mdns-advertise-host")
        if os.path.isfile(mdns_host):
            subprocess.run(["pkill", "-f", "mdns-advertise-host"], capture_output=True)
            time.sleep(0.5)
            import socket
            server_ip = socket.gethostbyname(socket.gethostname())
            # Fallback: parse hostname -I
            try:
                r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
                server_ip = r.stdout.strip().split()[0]
            except Exception:
                pass
            subprocess.Popen(
                [mdns_host, "cellswarm", master_ip, server_ip],
                stdout=open("/tmp/mdns-advertise-host.log", "w"),
                stderr=subprocess.STDOUT,
            )
            log(f"  mDNS relay: cellswarm.local -> {master_ip} (from {server_ip})")

        set_state(
            status="live",
            phase="done",
            message=f"Ring live: {n_phones} phones",
            master_ip=master_ip,
            n_phones=n_phones,
            http_port=http_port,
            available_phones=len(all_phones),
        )
        log(f"  Ring LIVE: {n_phones} phones, master={master_ip}:{http_port}")

    except Exception as e:
        set_state(status="error", message=str(e))
        log(f"  ERROR: {e}")


def do_stop():
    """Stop the current ring."""
    try:
        set_state(status="relaunching", phase="kill", message="Stopping ring...")
        log("Stop requested")
        all_phones = discover()
        kill_ring(all_phones)
        set_state(status="idle", phase="", message="Ring stopped", master_ip=None, n_phones=0)
        log("Ring stopped")
    except Exception as e:
        set_state(status="error", message=str(e))


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/phones":
            phones = discover()
            phone_info = scan_models(phones)
            self._json(200, {
                "total": len(phones),
                "with_model": len(phone_info),
                "phones": [{"ip": p["ip"], "model_name": p["model_name"],
                            "draft_name": p["draft_name"]} for p in phone_info],
            })
        elif self.path == "/api/status":
            with lock:
                self._json(200, dict(state))
        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/relaunch":
            with lock:
                if state["status"] == "relaunching":
                    self._json(409, {"error": "Relaunch already in progress"})
                    return

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            n_phones = int(body.get("n_phones", 12))
            draft_max = int(body.get("draft_max", 24))
            ctx = int(body.get("ctx", 2048))

            if n_phones < 2:
                self._json(400, {"error": "Need at least 2 phones"})
                return

            t = threading.Thread(target=do_relaunch, args=(n_phones, draft_max, ctx), daemon=True)
            t.start()
            self._json(202, {"status": "accepted", "n_phones": n_phones})

        elif self.path == "/api/stop":
            with lock:
                if state["status"] == "relaunching":
                    self._json(409, {"error": "Relaunch in progress, cannot stop"})
                    return

            t = threading.Thread(target=do_stop, daemon=True)
            t.start()
            self._json(202, {"status": "stopping"})

        else:
            self.send_response(404)
            self._cors()
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # quiet


def initial_probe():
    """Probe current ring state on startup."""
    phones = discover()
    phone_info = scan_models(phones)
    state["available_phones"] = len(phone_info)

    # Check if a master is already running
    for p in phone_info:
        try:
            pid = adb(p["ip"], "pidof cellswarm-master", timeout=5)
            if pid:
                # Found a running master — try to get ring info
                try:
                    r = urllib.request.urlopen(f"http://{p['ip']}:8080/api/ring", timeout=5)
                    ring = json.loads(r.read())
                    state.update(
                        status="live",
                        master_ip=p["ip"],
                        n_phones=ring.get("n_world", 0),
                        http_port=8080,
                        message=f"Ring detected: {ring.get('n_world', '?')} phones",
                    )
                    log(f"Detected running ring: {ring.get('n_world')} phones on {p['ip']}")
                    return
                except Exception:
                    state.update(status="live", master_ip=p["ip"], message="Master running (API unreachable)")
                    return
        except Exception:
            pass
    state["message"] = f"{len(phone_info)} phones available"
    log(f"No running ring found. {len(phone_info)} phones with models.")


if __name__ == "__main__":
    port = CONTROL_PORT
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])

    log("Probing current ring state...")
    initial_probe()

    server = HTTPServer(("0.0.0.0", port), Handler)
    log(f"Control API listening on http://0.0.0.0:{port}/")
    log(f"  GET  /api/phones   — list available phones")
    log(f"  GET  /api/status   — ring status")
    log(f"  POST /api/relaunch — {{\"n_phones\": N}}")
    log(f"  POST /api/stop     — stop ring")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down")
