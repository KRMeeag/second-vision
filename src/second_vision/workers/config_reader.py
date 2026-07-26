"""Config Reader — Reads settings from Arduino control panel."""

import time

# ============================================================
# INTERFACE CONTRACT (do not change):
#   Input:  Serial text lines from Arduino ("S:key:value\n")
#   Output: Updates SystemConfig, triggers pipeline rebuild on mode change
# ============================================================

def config_reader_worker(user_data, config, port_path, app):
    """Main config reader loop."""

    port = _open_config_port(port_path)

    while not user_data.shutdown_event.is_set():
        line = _read_line(port)
        if not line:
            continue
        
        try:
            parts = line.strip().split(":")
            prefix = parts[0]

            if prefix == "S" and len(parts) == 3:
                key, value = parts[1], _cast_value(parts[1], parts[2])
                config.udpate(**{key: value})
                print(f"[CONFIG] Updated {key} = {value}")

            elif prefix == "M" and len(parts) == 2:
                new_mode = parts[1]
                if new_mode in ("detection", "depth", "both"):
                    # tts_queue is a PriorityMailbox. Mode announcements carry no
                    # priority/tier, so item_priority()/item_tier() treat them as
                    # +inf / urgent — a physical mode switch is always heard.
                    user_data.tts_queue.offer({"announce": f"{new_mode} mode"})
                    config.update(pipeline_mode=new_mode)
                    if app:
                        app.trigger_rebuild()
                    print(f"[CONFIG] Mode: {parts[1]}")

            elif prefix == "B" and len(parts) == 2:
                print(f"[CONFIG] Button: {parts[1]}")

        except Exception as e:
            print(f"[CONFIG] Parse error: {e}")

    _close_config_port(port)

def _cast_value(key, value_str):
    """Convert string value to appropriate Python type."""
    bool_keys = {"tts_enabled", "depth_enabled", "detection_enabled", "hazard_detection"}
    float_keys = {"motor_strength", "cooldown_seconds"}

    if key in bool_keys:
        return value_str.strip() in ("1","true","True", "yes", "YES", "on", "ON", "enabled", "ENABLED")
    elif key in float_keys:
        return float(value_str)
    return value_str

# ==================== STUBS BELOW ====================

# ==================== STUBS BELOW ====================
def _open_config_port(port_path):
    """
    STUB — Replace with:
        import serial
        return serial.Serial(port_path, baudrate=9600, timeout=1.0)
    """
    print(f"[CONFIG STUB] Port {port_path} opened (mock)")
    return None
    
def _read_line(port) -> str:
    """
    STUB — Replace with:
        if port:
            return port.readline().decode(errors="ignore").strip()
        return ""
    """
    time.sleep(1.0)  # Don't busy-loop
    return ""

def _close_config_port(port):
    """
    STUB — Replace with:
        if port:
            port.close()
    """
    pass

