"""Config Reader — Reads settings from Arduino control panel."""

import errno
import time

# The panel is optional hardware: the pipeline has to run on a dev machine with
# no board attached, and sometimes no pyserial at all. Mirrors serial_worker.py.
try:
    import serial
    PYSERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised only on machines without pyserial
    PYSERIAL_AVAILABLE = False

# Must match BAUD in firmware/control-panel/src/control_panel.cpp.
CONFIG_BAUD = 9600

# Must match PROTOCOL_VERSION there too.
PROTOCOL_VERSION = 1

# readline() blocks at most this long, which paces the worker loop.
READ_TIMEOUT_SECONDS = 1.0

# Only used when there is no port to block on — see _read_line().
IDLE_SLEEP_SECONDS = 1.0

# Protocol v1 modes. "none" means run nothing.
VALID_MODES = ("detection", "depth", "both", "none")

# The panel sends B:alive every 10 s. Silence past this is a fault, not a quiet
# panel: settings still in use may be arbitrarily stale.
ALIVE_TIMEOUT_SECONDS = 30.0

# ============================================================
# INTERFACE CONTRACT (do not change):
#   Input:  Serial text lines from Arduino ("S:key:value\n")
#   Output: Updates SystemConfig, triggers pipeline rebuild on mode change
# ============================================================

def config_reader_worker(user_data, config, port_path, app):
    """Main config reader loop."""

    port = _open_config_port(port_path)

    # Panel liveness. Armed as soon as a port actually opens, NOT on the first
    # heartbeat: a panel that never beats at all is the failure most worth
    # catching, and waiting for a beat before starting the clock means that case
    # is accepted in silence forever. With no port there is nothing to be late.
    last_alive = time.monotonic() if port is not None else None
    ever_alive = False
    panel_missing = False

    while not user_data.shutdown_event.is_set():
        line = _read_line(port)

        # Checked on every pass, including empty ones — a panel that has gone
        # quiet produces nothing to parse, which is exactly the case that must
        # still be noticed.
        if (last_alive is not None and not panel_missing
                and time.monotonic() - last_alive > ALIVE_TIMEOUT_SECONDS):
            panel_missing = True
            if ever_alive:
                print(f"[CONFIG] No heartbeat from the control panel for "
                      f"{ALIVE_TIMEOUT_SECONDS:.0f}s — panel may be unpowered "
                      f"or unplugged. Settings in use are the last it sent.")
            else:
                print(f"[CONFIG] Control panel has not sent a heartbeat in the "
                      f"first {ALIVE_TIMEOUT_SECONDS:.0f}s — the port opened but "
                      f"nothing is talking. Check the link wire and that the "
                      f"panel firmware is flashed.")

        if not line:
            continue
        
        try:
            parts = line.strip().split(":")
            prefix = parts[0]

            # Both branches act ONLY on a real change. The panel re-announces
            # its whole state every 10 s (HEARTBEAT_MS in control_panel.cpp),
            # because panel and Pi share the X1202 and boot together — the
            # panel's opening report lands before this worker has opened the
            # port. Acting unconditionally on that heartbeat would rebuild the
            # Hailo pipeline every 10 seconds.
            if prefix == "S" and len(parts) == 3:
                key, value = parts[1], _cast_value(parts[1], parts[2])
                if config.get(key) != value:
                    config.update(**{key: value})
                    # Re-read: update() silently ignores keys SystemConfig does
                    # not define, so this also stops an unknown key logging on
                    # every heartbeat.
                    if config.get(key) == value:
                        print(f"[CONFIG] Updated {key} = {value}")

            elif prefix == "M" and len(parts) == 2:
                new_mode = parts[1]
                if (new_mode in VALID_MODES
                        and config.get("pipeline_mode") != new_mode):
                    # tts_queue is a PriorityMailbox. Mode announcements carry no
                    # priority/tier, so item_priority()/item_tier() treat them as
                    # +inf / urgent — a physical mode switch is always heard.
                    user_data.tts_queue.offer({"announce": f"{new_mode} mode"})
                    config.update(pipeline_mode=new_mode)
                    if app:
                        app.trigger_rebuild()
                    print(f"[CONFIG] Mode: {parts[1]}")

            elif prefix == "B" and len(parts) == 2:
                if parts[1] == "alive":
                    if panel_missing:
                        print("[CONFIG] Control panel is back")
                    last_alive = time.monotonic()
                    ever_alive = True
                    panel_missing = False
                else:
                    print(f"[CONFIG] Button: {parts[1]}")

            elif prefix == "V" and len(parts) == 3 and parts[1] == "panel":
                if parts[2] != str(PROTOCOL_VERSION):
                    print(f"[CONFIG] Panel speaks protocol v{parts[2]}, this "
                          f"build expects v{PROTOCOL_VERSION} — expect "
                          f"disagreement")
                else:
                    print(f"[CONFIG] Panel booted, protocol v{parts[2]}")

        except Exception as e:
            print(f"[CONFIG] Parse error: {e}")

    _close_config_port(port)

def _cast_value(key, value_str):
    """Convert string value to appropriate Python type."""
    # vibration_enabled MUST be here. Without it the value stays the string "0",
    # and serial_worker's `if not config.get("vibration_enabled")` sees a
    # non-empty string — which is truthy — so the motors keep running with the
    # DEPTH rocker physically OFF. Every flag the panel can send belongs in one
    # of these sets; a missing key fails silently and in the unsafe direction.
    bool_keys = {"tts_enabled", "vibration_enabled",
                 "depth_enabled", "detection_enabled", "hazard_detection"}
    float_keys = {"motor_strength", "cooldown_seconds"}

    if key in bool_keys:
        return value_str.strip() in ("1","true","True", "yes", "YES", "on", "ON", "enabled", "ENABLED")
    elif key in float_keys:
        return float(value_str)
    return value_str

# ==================== SERIAL PLUMBING ====================

def _open_config_port(port_path):
    """
    Open the control-panel link, or return None to run without a panel.

    None is a supported outcome, not a failure path: no --config-port, no
    pyserial, device absent, or permission denied all end up here and all leave
    the rest of the pipeline running. Every None return prints WHY once, with
    the fix where there is one — same contract as _open_serial_port().
    """
    if not port_path:
        print("[CONFIG] No --config-port given — running without a control panel")
        return None

    if not PYSERIAL_AVAILABLE:
        print(f"[CONFIG] pyserial not installed — cannot open {port_path}. "
              "Fix: pip install pyserial")
        return None

    try:
        port = serial.Serial(port_path, baudrate=CONFIG_BAUD,
                             timeout=READ_TIMEOUT_SECONDS)
    except Exception as exc:
        # Dispatch on errno, NOT exception type: pyserial catches the kernel's
        # OSError and re-raises its own SerialException, so `except
        # PermissionError` never fires. The errno survives the re-wrap.
        code = getattr(exc, "errno", None)
        if code == errno.EACCES:
            print(f"[CONFIG] Permission denied opening {port_path} — running without a control panel. "
                  "Fix: sudo usermod -aG dialout $USER, then LOG OUT and back in "
                  "(check with `groups`)")
        elif code == errno.ENOENT:
            print(f"[CONFIG] {port_path} does not exist — running without a control panel. "
                  "Check the cable, or `ls /dev/tty*` to find the right device")
        else:
            print(f"[CONFIG] Could not open {port_path} ({exc!r}) — running without a control panel")
        return None

    # The panel's ROM bootloader chatters at 115200 on reset, and opening the
    # port resets most boards. Drop that burst so the first line we parse is
    # real panel output rather than garbage.
    try:
        port.reset_input_buffer()
    except Exception as exc:  # non-fatal: a usable port with a dirty buffer
        print(f"[CONFIG] Could not flush buffer on {port_path}: {exc!r}")

    print(f"[CONFIG] Connected to control panel on {port_path} at {CONFIG_BAUD} baud")
    return port


def _read_line(port) -> str:
    """
    One line from the panel, or "" when nothing arrived.

    With a port open, readline() blocks up to READ_TIMEOUT_SECONDS and paces the
    loop by itself. With no port there is nothing to block on, so sleep instead —
    the caller `continue`s on "", and without this the worker would spin a core
    for the life of the process.
    """
    if port is None:
        time.sleep(IDLE_SLEEP_SECONDS)
        return ""

    try:
        return port.readline().decode(errors="ignore").strip()
    except Exception as exc:
        # Unplugging the panel mid-run raises on every call, so sleep here too
        # rather than turning a yanked cable into a busy-loop.
        print(f"[CONFIG] Read failed on the control panel link: {exc!r}")
        time.sleep(IDLE_SLEEP_SECONDS)
        return ""


def _close_config_port(port):
    """Close the link if one was ever opened."""
    if port is None:
        return
    try:
        port.close()
    except Exception as exc:  # shutdown path — never worth raising from
        print(f"[CONFIG] Could not close the control panel port: {exc!r}")
