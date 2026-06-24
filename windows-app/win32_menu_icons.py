"""Win32 菜单图标注入 — 程序绘制功能性图标并 patch pystray Windows 后端。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from pathlib import Path

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Win32 结构
# ---------------------------------------------------------------------------

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          wt.DWORD),
        ("biWidth",         wt.LONG),
        ("biHeight",        wt.LONG),
        ("biPlanes",        wt.WORD),
        ("biBitCount",      wt.WORD),
        ("biCompression",   wt.DWORD),
        ("biSizeImage",     wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed",       wt.DWORD),
        ("biClrImportant",  wt.DWORD),
    ]

_gdi32  = ctypes.windll.gdi32
_user32 = ctypes.windll.user32
_SM_CXSMICON = 49
_SM_CYSMICON = 50

# ---------------------------------------------------------------------------
# PIL Image → 预乘 alpha HBITMAP
# ---------------------------------------------------------------------------

def pil_to_hbitmap(img: Image.Image, size: tuple[int, int] | None = None) -> int | None:
    if size is None:
        w = _user32.GetSystemMetrics(_SM_CXSMICON) or 16
        h = _user32.GetSystemMetrics(_SM_CYSMICON) or 16
        size = (w, h)

    img = img.resize(size, Image.LANCZOS).convert("RGBA")
    width, height = img.size

    try:
        import numpy as np
        arr = np.array(img, dtype=np.float32)
        alpha = arr[:, :, 3:4] / 255.0
        arr[:, :, :3] *= alpha
        arr = arr.astype(np.uint8)
        data = arr[:, :, [2, 1, 0, 3]].copy().tobytes()
    except ImportError:
        pixels = img.load()
        buf = bytearray(width * height * 4)
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                i = (y * width + x) * 4
                buf[i], buf[i+1], buf[i+2], buf[i+3] = b*a//255, g*a//255, r*a//255, a
        data = bytes(buf)

    header = _BITMAPINFOHEADER(
        biSize=ctypes.sizeof(_BITMAPINFOHEADER),
        biWidth=width, biHeight=-height,
        biPlanes=1, biBitCount=32, biCompression=0,
    )
    bits_ptr = ctypes.c_void_p()
    hdc = _user32.GetDC(None)
    hbitmap = _gdi32.CreateDIBSection(hdc, ctypes.byref(header), 0,
                                       ctypes.byref(bits_ptr), None, 0)
    _user32.ReleaseDC(None, hdc)
    if not hbitmap or not bits_ptr:
        return None
    ctypes.memmove(bits_ptr, data, len(data))
    return hbitmap

# ---------------------------------------------------------------------------
# 程序绘制各功能图标（64×64，缩放到系统菜单尺寸）
# ---------------------------------------------------------------------------

def _draw_play(size: int) -> Image.Image:
    """绿色三角 — 启动"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 8
    pts = [(m*2, m), (m*2, size-m), (size-m, size//2)]
    d.polygon(pts, fill=(34, 197, 94, 255))
    return img

def _draw_stop_square(size: int) -> Image.Image:
    """红色方块 — 停止"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 5
    d.rectangle([m, m, size-m, size-m], fill=(239, 68, 68, 255))
    return img

def _draw_globe(size: int) -> Image.Image:
    """蓝色地球圆圈 — 打开页面"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 8
    lw = max(2, size // 16)
    d.ellipse([m, m, size-m, size-m], outline=(59, 130, 246, 255), width=lw)
    cx = size // 2
    d.line([(cx, m), (cx, size-m)], fill=(59, 130, 246, 255), width=lw)
    d.arc([m*2, size//3, size-m*2, size*2//3], 0, 180, fill=(59, 130, 246, 255), width=lw)
    d.arc([m*2, size//3, size-m*2, size*2//3], 180, 360, fill=(59, 130, 246, 255), width=lw)
    return img

def _draw_magnifier(size: int) -> Image.Image:
    """橙色放大镜 — 诊断"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 8
    lw = max(2, size // 14)
    r = size // 3
    cx, cy = size // 2 - m // 2, size // 2 - m // 2
    d.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(251, 146, 60, 255), width=lw)
    end = int(cx + r * 0.7)
    d.line([(end, end), (size-m, size-m)], fill=(251, 146, 60, 255), width=lw+1)
    return img

def _draw_x(size: int) -> Image.Image:
    """灰色 X — 退出"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 5
    lw = max(2, size // 12)
    d.line([(m, m), (size-m, size-m)], fill=(156, 163, 175, 255), width=lw)
    d.line([(size-m, m), (m, size-m)], fill=(156, 163, 175, 255), width=lw)
    return img

def _draw_doc(size: int) -> Image.Image:
    """蓝色文档页 — API 文档"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 6
    lw = max(1, size // 20)
    fold = size // 4
    d.polygon(
        [(m, m), (size-m-fold, m), (size-m, m+fold), (size-m, size-m), (m, size-m)],
        fill=(219, 234, 254, 255), outline=(59, 130, 246, 255),
    )
    d.polygon(
        [(size-m-fold, m), (size-m-fold, m+fold), (size-m, m+fold)],
        fill=(147, 197, 253, 255),
    )
    lc = (59, 130, 246, 200)
    for i in range(3):
        y = m + fold + (size - m*2 - fold) * (i+1) // 4
        d.line([(m*2, y), (size-m*2, y)], fill=lc, width=lw)
    return img

_ICON_SIZE = 64  # 绘制尺寸，pil_to_hbitmap 再缩到系统值

_ICON_BUILDERS = {
    "start":  lambda: _draw_play(_ICON_SIZE),
    "stop":   lambda: _draw_stop_square(_ICON_SIZE),
    "globe":  lambda: _draw_globe(_ICON_SIZE),
    "doc":    lambda: _draw_doc(_ICON_SIZE),
    "search": lambda: _draw_magnifier(_ICON_SIZE),
    "quit":   lambda: _draw_x(_ICON_SIZE),
}

# ---------------------------------------------------------------------------
# 菜单项文字 → 图标 key
# ---------------------------------------------------------------------------

_TEXT_TO_KEY: list[tuple[str, str]] = [
    ("启动",  "start"),
    ("停止",  "stop"),
    ("控制台","globe"),
    ("API",   "doc"),
    ("文档",  "doc"),
    ("诊断",  "search"),
    ("退出",  "quit"),
]

def _icon_key(text: str) -> str | None:
    for keyword, key in _TEXT_TO_KEY:
        if keyword in text:
            return key
    return None

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def patch_menu_icons(assets_dir: Path | None = None) -> None:
    """
    Monkey-patch pystray Windows 后端，为菜单项注入 HBITMAP 图标。
    必须在 pystray.Icon 实例化之前调用。
    """
    try:
        import pystray._win32 as pw32
        from pystray._util import win32
        import pystray._base as _base
    except Exception:
        return

    icons: dict[str, int | None] = {}
    for key, builder in _ICON_BUILDERS.items():
        try:
            icons[key] = pil_to_hbitmap(builder())
        except Exception:
            icons[key] = None

    MIIM_BITMAP = win32.MIIM_BITMAP
    _original = pw32.Icon._create_menu_item

    def _patched(self, descriptor, callbacks):
        info = _original(self, descriptor, callbacks)
        if descriptor is _base.Menu.SEPARATOR:
            return info
        key = _icon_key(str(descriptor.text))
        if key:
            hbmp = icons.get(key)
            if hbmp:
                info.fMask |= MIIM_BITMAP
                info.hbmpItem = hbmp
        return info

    pw32.Icon._create_menu_item = _patched
