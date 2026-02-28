import ctypes
import os
import pygetwindow as gw

def get_process_name_from_hwnd(hwnd):
    try:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        h_process = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid.value)
        if not h_process:
            return ""
            
        MAX_PATH = 260
        exe_name = ctypes.create_unicode_buffer(MAX_PATH)
        size = ctypes.c_ulong(MAX_PATH)
        
        # Using GetModuleFileNameExW from psapi
        psapi = ctypes.WinDLL('psapi')
        if psapi.GetModuleFileNameExW(h_process, None, exe_name, size.value):
            ctypes.windll.kernel32.CloseHandle(h_process)
            return os.path.basename(exe_name.value)
            
        ctypes.windll.kernel32.CloseHandle(h_process)
    except Exception as e:
        print("Error:", e)
    return ""

for w in gw.getAllWindows():
    if w.title and w.visible:
        print(f"[{w.title}] -> {get_process_name_from_hwnd(w._hWnd)}")
