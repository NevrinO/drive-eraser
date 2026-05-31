# --- START OF FILE backend/system_metrics.py ---
import os

def get_ram_usage():
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        mem_total = 0
        mem_available = 0
        for line in lines:
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available = int(line.split()[1])
        if mem_total > 0:
            used = mem_total - mem_available
            return round((used / mem_total) * 100, 1)
    except Exception:
        pass
    return 0.0

def get_cpu_usage():
    try:
        load = os.getloadavg()[0]
        cores = os.cpu_count() or 1
        return round(min(100.0, (load / cores) * 100.0), 1)
    except Exception:
        pass
    return 0.0

def get_system_uptime():
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"
    except Exception:
        pass
    return "Unknown"
# --- END OF FILE backend/system_metrics.py ---
