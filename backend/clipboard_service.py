import ctypes
from ctypes import wintypes
import os

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

class DROPFILES(ctypes.Structure):
    _fields_ = [
        ('pFiles', wintypes.DWORD),
        ('pt', wintypes.POINT),
        ('fNC', wintypes.BOOL),
        ('fWide', wintypes.BOOL),
    ]

def copy_file_to_clipboard(filepath: str) -> bool:
    """Copies a local file to Windows clipboard as CF_HDROP, ready to paste in WeChat/QQ/Explorer."""
    filepath = os.path.abspath(filepath)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    files = filepath + '\0\0'
    files_bytes = files.encode('utf-16le')
    df = DROPFILES()
    df.pFiles = ctypes.sizeof(DROPFILES)
    df.pt.x = 0
    df.pt.y = 0
    df.fNC = False
    df.fWide = True
    total_data = bytes(df) + files_bytes

    GMEM_MOVEABLE = 0x0002
    h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(total_data))
    if not h_mem:
        return False

    ptr = kernel32.GlobalLock(h_mem)
    if not ptr:
        return False

    ctypes.memmove(ptr, total_data, len(total_data))
    kernel32.GlobalUnlock(h_mem)

    if not user32.OpenClipboard(None):
        return False

    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(15, h_mem)
        return True
    finally:
        user32.CloseClipboard()
