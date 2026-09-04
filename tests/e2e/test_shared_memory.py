"""Live singleuser shared-memory verification."""

import json

from _process import kctl_out

EIGHT_GIBIBYTES = 8 * 1024**3
SHARED_MEMORY_PROBE_BYTES = 128 * 1024**2
SHARED_MEMORY_PROBE = f"""
from multiprocessing import shared_memory

size = {SHARED_MEMORY_PROBE_BYTES}
chunk = b"\\x01" * (1024**2)
segment = shared_memory.SharedMemory(create=True, size=size)
try:
    for offset in range(0, size, len(chunk)):
        segment.buf[offset : offset + len(chunk)] = chunk
    print(len(segment.buf))
finally:
    segment.close()
    segment.unlink()
""".strip()


def _get_pod(user):
    returncode, pod_json, error = kctl_out("get", "pod", user.pod, "-o", "json")
    assert returncode == 0, error
    return json.loads(pod_json)


def _shared_memory_capacity(user):
    returncode, output = user.exec(
        "python",
        "-c",
        "import os; s = os.statvfs('/dev/shm'); print(s.f_frsize * s.f_blocks)",
    )
    assert returncode == 0, output
    return int(output)


def _write_shared_memory_above_runtime_default(user):
    returncode, output = user.exec("python", "-c", SHARED_MEMORY_PROBE)
    assert returncode == 0, output
    assert output.splitlines()[-1] == str(SHARED_MEMORY_PROBE_BYTES)


def test_singleuser_has_memory_backed_shared_memory(spawn_user):
    user = spawn_user("alice-data")
    pod = _get_pod(user)

    dshm = next(volume for volume in pod["spec"]["volumes"] if volume["name"] == "dshm")
    assert dshm["emptyDir"] == {"medium": "Memory", "sizeLimit": "8Gi"}

    notebook = next(
        container
        for container in pod["spec"]["containers"]
        if container["name"] == "notebook"
    )
    mount = next(item for item in notebook["volumeMounts"] if item["name"] == "dshm")
    assert mount["mountPath"] == "/dev/shm"

    assert _shared_memory_capacity(user) == EIGHT_GIBIBYTES
    _write_shared_memory_above_runtime_default(user)
