# OS drive detection — extracted from disk_ops.py (A70)
# Leaf module: no dependencies on other new modules

import os
import logging
import subprocess
import threading

# Cached OS drive lookup (OS drive cannot change while the service runs)
# Cached indefinitely until service restart since the OS drive is a static property
_OS_BY_PATH_CACHE = {'data': None}
_OS_BY_PATH_LOCK = threading.Lock()


def get_os_parent_device():
    try:
        st = os.stat("/")
        major = os.major(st.st_dev)
        minor = os.minor(st.st_dev)

        uevent_path = f"/sys/dev/block/{major}:{minor}/uevent"
        devname = None
        try:
            with open(uevent_path, "r") as f:
                for line in f.read().splitlines():
                    if line.startswith("DEVNAME="):
                        devname = line.strip().split("=")[1]
                        break
        except (FileNotFoundError, OSError):
            pass

        if not devname:
            try:
                res = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"], capture_output=True, text=True, timeout=5, shell=False)
                if res.returncode == 0 and res.stdout.strip():
                    src = res.stdout.strip()
                    if src.startswith("/dev/"):
                        devname = src[5:]
            except Exception:
                pass

        if not devname:
            try:
                with open("/proc/mounts", "r") as f:
                    for line in f.read().splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == "/":
                            src = parts[0]
                            if src.startswith("/dev/"):
                                devname = src[5:]
                                break
            except (FileNotFoundError, OSError):
                pass

        if not devname:
            return None

        def resolve_leaf_parent(name):
            sys_path = f"/sys/class/block/{name}"
            real_path = os.path.realpath(sys_path)
            if "/block/" in real_path:
                parts = real_path.split("/block/")
                if len(parts) > 1:
                    subparts = parts[1].split("/")
                    if len(subparts) > 0:
                        return subparts[0]
            return name

        if devname.startswith("dm-"):
            slaves_dir = f"/sys/class/block/{devname}/slaves"
            if os.path.isdir(slaves_dir):
                slaves = os.listdir(slaves_dir)
                if slaves:
                    return resolve_leaf_parent(slaves[0])

        return resolve_leaf_parent(devname)
    except Exception as e:
        logging.getLogger(__name__).warning(f"OS drive detection failed: {e}")
        return None


def get_os_by_path():
    parent_name = get_os_parent_device()
    if not parent_name:
        return None, None

    dev_node = f"/dev/{parent_name}"
    by_path_dir = "/dev/disk/by-path/"
    if not os.path.exists(by_path_dir):
        return dev_node, None
    try:
        for entry in os.listdir(by_path_dir):
            full_path = os.path.join(by_path_dir, entry)
            if os.path.islink(full_path):
                if "-part" in entry:
                    continue
                if os.path.realpath(full_path) == os.path.realpath(dev_node):
                    return dev_node, entry
    except (FileNotFoundError, OSError):
        pass

    return dev_node, None


def _get_os_by_path_cached():
    """Cached wrapper around get_os_by_path(). Cached indefinitely until service restart since the OS drive is a static property."""
    with _OS_BY_PATH_LOCK:
        if _OS_BY_PATH_CACHE['data'] is not None:
            return _OS_BY_PATH_CACHE['data']
        data = get_os_by_path()
        if data and data[0]:
            _OS_BY_PATH_CACHE['data'] = data
        return data
