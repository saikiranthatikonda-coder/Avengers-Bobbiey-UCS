"""Fast process snapshot for the PROCESS MONITOR.

psutil reads each process separately and, for every process Windows won't open
(services, system), falls back to listing ALL processes again — O(n²): ~2 s of
CPU per sweep on a 350-process PC, enough to starve the server when the
dashboard polls. On Windows we instead read every process in ONE
NtQuerySystemInformation(SystemProcessInformation) call (milliseconds) and
compute CPU % from the CPU-time delta between two snapshots. Other OSes use
psutil. Results are aggregated by name (chrome ×46 → one row).
"""

import ctypes
import os
import sys
import time

_IS_WIN = sys.platform == "win32" and ctypes.sizeof(ctypes.c_void_p) == 8
_NCPU = os.cpu_count() or 1
_prev: dict[int, tuple[int, float]] = {}          # pid -> (cpu time in 100 ns, wall time)
SKIP = {"System Idle Process", "Idle", "System", "Registry", "?", "smss", "Memory Compression", ""}


def _raw_windows() -> list[tuple[int, str, int, int]]:
    """[(pid, name, cpu_time_100ns, working_set_bytes)] from one kernel call."""
    ntdll = ctypes.WinDLL("ntdll")
    size = ctypes.c_ulong(1 << 20)
    while True:
        buf = ctypes.create_string_buffer(size.value)
        st = ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(size))   # SystemProcessInformation
        if st & 0xFFFFFFFF == 0xC0000004:            # STATUS_INFO_LENGTH_MISMATCH → grow and retry
            size = ctypes.c_ulong(size.value + (1 << 16))
            continue
        if st != 0:
            raise OSError(f"NtQuerySystemInformation failed: 0x{st & 0xFFFFFFFF:08x}")
        break
    base = ctypes.addressof(buf)
    out, off = [], 0
    u32 = lambda o: ctypes.c_uint32.from_address(base + o).value          # noqa: E731
    i64 = lambda o: ctypes.c_int64.from_address(base + o).value           # noqa: E731
    u64 = lambda o: ctypes.c_uint64.from_address(base + o).value          # noqa: E731
    while True:
        # SYSTEM_PROCESS_INFORMATION (x64): UserTime 0x28, KernelTime 0x30,
        # ImageName UNICODE_STRING 0x38 (Length u16, Buffer ptr @0x40),
        # UniqueProcessId 0x50, WorkingSetSize 0x90.
        name_len = ctypes.c_uint16.from_address(base + off + 0x38).value
        name_ptr = u64(off + 0x40)
        name = ctypes.wstring_at(name_ptr, name_len // 2) if name_ptr and name_len else ""
        out.append((u64(off + 0x50), name, i64(off + 0x28) + i64(off + 0x30), u64(off + 0x90)))
        nxt = u32(off)
        if nxt == 0:
            break
        off += nxt
    return out


def snapshot() -> list[dict]:
    """Aggregated rows: name, cpu (% of the whole machine, like Task Manager × cores), mem_mb, count."""
    now = time.perf_counter()
    agg: dict[str, dict] = {}
    if _IS_WIN:
        seen = {}
        for pid, name, cpu100ns, ws in _raw_windows():
            name = name[:-4] if name.lower().endswith(".exe") else name
            seen[pid] = (cpu100ns, now)
            prev = _prev.get(pid)
            cpu = 0.0
            if prev and now > prev[1]:
                cpu = (cpu100ns - prev[0]) / 1e7 / (now - prev[1]) * 100.0      # % of one core
            a = agg.setdefault(name[:26], {"name": name[:26], "cpu": 0.0, "mem_mb": 0.0, "count": 0})
            a["cpu"] += max(0.0, cpu)
            a["mem_mb"] += ws / 1048576
            a["count"] += 1
        _prev.clear(); _prev.update(seen)
    else:
        import psutil
        for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
            try:
                info = p.info
                name = (info.get("name") or "?")[:26]
                mem = info.get("memory_info")
                a = agg.setdefault(name, {"name": name, "cpu": 0.0, "mem_mb": 0.0, "count": 0})
                a["cpu"] += info.get("cpu_percent") or 0.0
                a["mem_mb"] += (mem.rss / 1048576) if mem else 0.0
                a["count"] += 1
            except Exception:
                continue
    return [r for r in agg.values() if r["name"] not in SKIP]
