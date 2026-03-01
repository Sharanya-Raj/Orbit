import tkinter as tk
import ctypes
from ctypes import c_int, sizeof, POINTER, pointer, Structure

class ACCENTPOLICY(Structure):
    _fields_ = [
        ("AccentState", c_int),
        ("AccentFlags", c_int),
        ("GradientColor", c_int),
        ("AnimationId", c_int)
    ]

class WINDOWCOMPOSITIONATTRIBDATA(Structure):
    _fields_ = [
        ("Attribute", c_int),
        ("Data", POINTER(ACCENTPOLICY)),
        ("SizeOfData", c_int)
    ]

def apply_blur(hwnd):
    # ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
    # ACCENT_ENABLE_BLURBEHIND = 3
    policy = ACCENTPOLICY()
    policy.AccentState = 4 # ACCENT_ENABLE_ACRYLICBLURBEHIND
    # Color format is ABGR: 0x01000000 (alpha=1, rgb=0)
    # Let's try a dark semi-transparent color: alpha=120, R=24, G=24, B=28
    # Hex: 0x781C1818
    policy.GradientColor = 0x781C1818 
    
    data = WINDOWCOMPOSITIONATTRIBDATA()
    data.Attribute = 19 # WCA_ACCENT_POLICY
    data.Data = pointer(policy)
    data.SizeOfData = sizeof(policy)
    
    ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, pointer(data))

def apply_rounded_corners(hwnd):
    # DWMWA_WINDOW_CORNER_PREFERENCE = 33
    # DWMWCP_ROUND = 2, DWMWCP_ROUNDSMALL = 3
    preference = c_int(2)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, pointer(preference), sizeof(preference))

root = tk.Tk()
root.overrideredirect(True)
root.geometry("300x100+100+100")
root.attributes("-topmost", True)

# Translucent inner frame technique using standard Tkinter color
# To get acrylic working on borderless window in tkinter, we usually need layered window attributes.
root.attributes('-transparentcolor', '#000001')
root.configure(bg='#000001')

frame = tk.Frame(root, bg='#000001')
frame.pack(fill=tk.BOTH, expand=True)

lbl = tk.Label(frame, text="Hello Acrylic Liquid Glass", bg='#000001', fg="white", font=("Segoe UI", 16))
lbl.pack(pady=30)

root.update_idletasks()
hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
apply_blur(hwnd)
apply_rounded_corners(hwnd)

root.after(3000, root.destroy)
root.mainloop()
