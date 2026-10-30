"""Owned Windows process trees, assigned while suspended before any child code runs.

Only runtime-selected Python entrypoints use this host process wrapper. Agent
commands still run exclusively inside the bounded Docker sandbox.
"""

import ctypes
import os
import subprocess
from ctypes import wintypes as w
from functools import cache
from pathlib import Path

from relayforge.workspaces.manager import reject_links


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("xsize", w.DWORD),
        ("ysize", w.DWORD),
        ("xchars", w.DWORD),
        ("ychars", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_bytes", ctypes.c_void_p),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", ctypes.c_void_p)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class BasicLimit(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", w.DWORD),
        ("minimum", ctypes.c_size_t),
        ("maximum", ctypes.c_size_t),
        ("active", w.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in [
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        ]
    ]


class ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimit),
        ("io", IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


@cache
def kernel():
    if os.name != "nt":
        raise RuntimeError("The local process supervisor currently requires Windows")
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        "InitializeProcThreadAttributeList": (
            [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)],
            w.BOOL,
        ),
        "UpdateProcThreadAttribute": (
            [
                ctypes.c_void_p,
                w.DWORD,
                ctypes.c_size_t,
                ctypes.c_void_p,
                ctypes.c_size_t,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ],
            w.BOOL,
        ),
        "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
        "CreateProcessW": (
            [
                w.LPCWSTR,
                w.LPWSTR,
                ctypes.c_void_p,
                ctypes.c_void_p,
                w.BOOL,
                w.DWORD,
                ctypes.c_void_p,
                w.LPCWSTR,
                ctypes.c_void_p,
                ctypes.POINTER(ProcessInfo),
            ],
            w.BOOL,
        ),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    return library


def check(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())


class OwnedProcess:
    def __init__(self, handle, pid):
        self.handle, self.pid = handle, pid

    def poll(self):
        status = kernel().WaitForSingleObject(self.handle, 0)
        if status == 0x102:
            return None
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = w.DWORD()
        check(kernel().GetExitCodeProcess(self.handle, ctypes.byref(code)))
        return code.value

    def close(self):
        if self.handle:
            kernel().CloseHandle(self.handle)
            self.handle = None


class WindowsJob:
    def __init__(self):
        self.api = kernel()
        self.handle = self.api.CreateJobObjectW(None, None)
        check(self.handle)
        limit = ExtendedLimit()
        limit.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        try:
            check(
                self.api.SetInformationJobObject(
                    self.handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)
                )
            )
        except OSError:
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise

    def spawn(self, arguments: list[str], cwd: Path, stdout: Path, stderr: Path) -> OwnedProcess:
        import msvcrt

        if not self.handle or not Path(arguments[0]).is_absolute():
            raise ValueError("An owned job and absolute executable are required")
        for path in [cwd, stdout, stderr]:
            reject_links(path)
        stdout.parent.mkdir(parents=True, exist_ok=True)
        stderr.parent.mkdir(parents=True, exist_ok=True)
        with (
            open(os.devnull, "rb") as incoming,
            stdout.open("ab", buffering=0) as outgoing,
            stderr.open("ab", buffering=0) as errors,
        ):
            handles = [
                msvcrt.get_osfhandle(stream.fileno()) for stream in [incoming, outgoing, errors]
            ]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            attribute_size = ctypes.c_size_t()
            self.api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
            attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
            attributes = ctypes.cast(attribute_buffer, ctypes.c_void_p)
            initialized = False
            information = ProcessInfo()
            try:
                check(
                    self.api.InitializeProcThreadAttributeList(
                        attributes, 1, 0, ctypes.byref(attribute_size)
                    )
                )
                initialized = True
                allowed = (w.HANDLE * len(handles))(*handles)
                check(
                    self.api.UpdateProcThreadAttribute(
                        attributes,
                        0,
                        0x20002,
                        ctypes.byref(allowed),
                        ctypes.sizeof(allowed),
                        None,
                        None,
                    )
                )
                startup = StartupInfoEx()
                startup.startup.cb = ctypes.sizeof(startup)
                startup.startup.flags = (
                    0x100 | 1
                )  # use only explicit standard handles; hide window.
                startup.startup.show = 0
                startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
                startup.attributes = attributes
                command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
                # The child cannot spawn grandchildren before its Job assignment.
                flags = (
                    0x4 | 0x08000000 | 0x00080000
                )  # SUSPENDED | NO_WINDOW | EXTENDED_STARTUPINFO
                check(
                    self.api.CreateProcessW(
                        arguments[0],
                        command,
                        None,
                        None,
                        True,
                        flags,
                        None,
                        str(cwd),
                        ctypes.byref(startup),
                        ctypes.byref(information),
                    )
                )
                try:
                    check(self.api.AssignProcessToJobObject(self.handle, information.process))
                    if self.api.ResumeThread(information.thread) == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                except OSError:
                    self.api.TerminateProcess(information.process, 1)
                    self.api.WaitForSingleObject(information.process, 3000)
                    self.api.CloseHandle(information.process)
                    raise
                return OwnedProcess(information.process, information.pid)
            finally:
                if information.thread:
                    self.api.CloseHandle(information.thread)
                if initialized:
                    self.api.DeleteProcThreadAttributeList(attributes)
                for handle in handles:
                    os.set_handle_inheritable(handle, False)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
