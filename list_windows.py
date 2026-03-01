import pygetwindow as gw
import ctypes
import os
import sys

def get_process_name(hwnd):
    try:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h_process = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
        if not h_process: return ""
        exe_name = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(260)
        psapi = ctypes.WinDLL('psapi')
        if psapi.GetModuleFileNameExW(h_process, None, exe_name, size.value):
            ctypes.windll.kernel32.CloseHandle(h_process)
            return os.path.basename(exe_name.value).lower()
        ctypes.windll.kernel32.CloseHandle(h_process)
    except Exception as e:
        print(f"Error for hwnd {hwnd}: {e}")
        pass
    return ""

def list_windows_and_procs():
    print("Enumerating visible windows with titles...")
    for w in gw.getAllWindows():
        if w.title and w.visible:
            proc_name = get_process_name(w._hWnd)
            print(f"[{proc_name}] -> {w.title}")

if __name__ == "__main__":
    list_windows_and_procs()
