"""Floating orb as a native Windows layered window: real per-pixel transparency (no white/black box).
Pure ctypes + numpy. Same idea as ui/orb.js: a living sphere of particles.

  left click  -> on_click()      drag -> move it      right click -> on_right()
  set(state, level)  set_theme(name)
"""
import ctypes, math, random, threading, time
from ctypes import wintypes

import numpy as np

user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32

THEMES = {   # main, deep, bright, warn  (0..1 RGB)
    "glass":   ([0.80, 0.90, 1.00], [0.45, 0.55, 0.70], [1.00, 1.00, 1.00], [1.00, 0.80, 0.40]),
    "emerald": ([0.18, 1.00, 0.67], [0.04, 0.75, 0.47], [0.75, 1.00, 0.88], [0.94, 0.78, 0.39]),
    "crimson": ([1.00, 0.28, 0.24], [0.82, 0.08, 0.20], [1.00, 0.75, 0.60], [1.00, 0.77, 0.35]),
}

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.HDC, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
for _f in ("ShowWindow", "SetTimer", "KillTimer", "DestroyWindow", "PostMessageW", "SetCapture", "GetWindowRect"):
    getattr(user32, _f).argtypes = None
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetCapture.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte), ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_byte)]


def _sprite(r):
    n = int(math.ceil(r * 2.5)) * 2 + 1
    c = n // 2
    y, x = np.mgrid[0:n, 0:n] - c
    return np.exp(-(x * x + y * y) / (2 * (r * 0.6) ** 2)).astype(np.float32)


class NativeOrb:
    def __init__(self, x, y, size=124, on_click=None, on_right=None, on_moved=None, theme="glass"):
        self.on_click, self.on_right, self.on_moved = on_click, on_right, on_moved
        self.x, self.y, self.size_css = x, y, size
        self.state, self.level, self.theme = "starting", 0.0, theme if theme in THEMES else "glass"
        self.hwnd, self.alive = None, False
        self._down, self._dragging = None, False
        n = 200
        self.pts = []
        for i in range(n):
            yy = 1 - (i / (n - 1)) * 2; r = math.sqrt(1 - yy * yy); th = i * 2.399963
            self.pts.append((math.cos(th) * r, yy, math.sin(th) * r, random.random() * 6.28, math.asin(yy)))
        self.P = np.array([p[:3] for p in self.pts], np.float32)
        self.seed = np.array([p[3] for p in self.pts], np.float32)
        self.lat = np.array([p[4] for p in self.pts], np.float32)
        self.t0 = time.time()
        self.sm = dict(lvl=0.0, energy=0.1, bands=0.0, gold=0.0, dim=0.45)
        self.col = np.array(THEMES[self.theme][0]); self.col2 = np.array(THEMES[self.theme][1])
        threading.Thread(target=self._run, daemon=True).start()

    # ---------------- public
    def set(self, state=None, level=None):
        if state:
            self.state = state
        if level is not None:
            self.level = float(level)

    def set_theme(self, name):
        if name in THEMES:
            self.theme = name

    def show(self, visible=True):
        self.visible = visible
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 4 if visible else 0)   # SW_SHOWNOACTIVATE / SW_HIDE

    def destroy(self):
        self.alive = False
        if self.hwnd:
            user32.PostMessageW(self.hwnd, 0x0010, 0, 0)   # WM_CLOSE

    # ---------------- window
    @staticmethod
    def _on_screen(x, y, S):
        """Keep the orb inside the work area of the nearest monitor (a saved spot can be off-screen after a monitor
        or scaling change - that's how the orb 'disappeared')."""
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        try:
            user32.MonitorFromPoint.restype = wintypes.HMONITOR
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
            mon = user32.MonitorFromPoint(wintypes.POINT(x + S // 2, y + S // 2), 2)   # MONITOR_DEFAULTTONEAREST
            mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                w = mi.rcWork
                x = max(w.left, min(x, w.right - S))
                y = max(w.top, min(y, w.bottom - S))
        except Exception:
            pass
        return x, y

    def _run(self):
        try:
            dpi = user32.GetDpiForSystem()
        except Exception:
            dpi = 96
        self.scale = dpi / 96.0
        self.S = S = int(self.size_css * self.scale) // 2 * 2
        self._prep()
        hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wndproc)
        wc = WNDCLASSEXW(); wc.cbSize = ctypes.sizeof(WNDCLASSEXW); wc.lpfnWndProc = self._proc; wc.hInstance = hinst
        wc.lpszClassName = "JarvisNativeOrb"; wc.hCursor = user32.LoadCursorW(None, 32649)   # hand
        user32.RegisterClassExW(ctypes.byref(wc))
        ex = 0x00080000 | 0x00000008 | 0x00000080 | 0x08000000   # LAYERED | TOPMOST | TOOLWINDOW | NOACTIVATE
        px, py = self._on_screen(int(self.x * self.scale), int(self.y * self.scale), S)
        self.hwnd = user32.CreateWindowExW(ex, "JarvisNativeOrb", "Jarvis orb", 0x80000000, px, py,
                                           S, S, None, None, hinst, None)
        # 32-bit premultiplied DIB
        bmi = BITMAPINFOHEADER(); bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER); bmi.biWidth = S; bmi.biHeight = -S
        bmi.biPlanes = 1; bmi.biBitCount = 32
        self.bits = ctypes.c_void_p()
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        self.dib = gdi32.CreateDIBSection(self.screen_dc, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        gdi32.SelectObject(self.mem_dc, self.dib)
        self.buf = np.ctypeslib.as_array(ctypes.cast(self.bits, ctypes.POINTER(ctypes.c_uint8)), shape=(S, S, 4))
        self.alive = True
        self._frame()
        user32.ShowWindow(self.hwnd, 4)
        user32.SetTimer(self.hwnd, 1, 40, None)   # ~25 fps (idle frames are skipped to ~12 fps)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg)); user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == 0x0113:                                   # WM_TIMER
                self._tick += 1
                # drawing runs in Python and competes with the ears for the CPU: idle = ~6 fps (slow drift looks the
                # same), hidden = nothing, busy states = full ~25 fps
                if not self.visible or (self.state in ("idle", "starting") and self._tick % 4):
                    return 0
                self._frame()
                return 0
            if msg == 0x0201:                                   # WM_LBUTTONDOWN
                self._down = (lp & 0xFFFF, (lp >> 16) & 0xFFFF); self._dragging = False
                user32.SetCapture(hwnd); return 0
            if msg == 0x0200 and self._down and wp & 1:         # WM_MOUSEMOVE with left button
                x, y = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
                if abs(x - self._down[0]) + abs(y - self._down[1]) > 4:
                    self._dragging = True; self._down = None
                    user32.ReleaseCapture()
                    user32.SendMessageW(hwnd, 0x00A1, 2, 0)      # WM_NCLBUTTONDOWN, HTCAPTION -> system drag
                    if self.on_moved:
                        r = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(r))
                        threading.Thread(target=self.on_moved, args=(r.left, r.top), daemon=True).start()
                return 0
            if msg == 0x0202:                                   # WM_LBUTTONUP
                user32.ReleaseCapture()
                if self._down and not self._dragging and self.on_click:
                    threading.Thread(target=self.on_click, daemon=True).start()
                self._down = None; return 0
            if msg == 0x0205 and self.on_right:                 # WM_RBUTTONUP
                threading.Thread(target=self.on_right, daemon=True).start(); return 0
            if msg == 0x0010:                                   # WM_CLOSE
                user32.KillTimer(hwnd, 1); user32.DestroyWindow(hwnd); return 0
            if msg == 0x0002:                                   # WM_DESTROY
                user32.PostQuitMessage(0); return 0
        except Exception as e:
            print("orb error:", e)
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    _tick = 0
    visible = True

    # ---------------- drawing
    def _prep(self):
        S = self.S
        yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
        self.cx = self.cy = S / 2
        self.dx, self.dy = xx - S / 2, yy - S / 2
        self.d = np.sqrt(self.dx ** 2 + self.dy ** 2)
        self.sprites = [_sprite(r * self.scale) for r in (1.0, 1.4, 1.85, 2.4)]

    def _splat(self, rgb, a, x, y, spr, color, alpha):
        h = spr.shape[0] // 2
        x, y = int(x), int(y)
        S = self.S
        x0, y0, x1, y1 = max(0, x - h), max(0, y - h), min(S, x + h + 1), min(S, y + h + 1)
        if x0 >= x1 or y0 >= y1:
            return
        k = spr[y0 - (y - h):y1 - (y - h), x0 - (x - h):x1 - (x - h)] * alpha
        rgb[y0:y1, x0:x1] += k[..., None] * color
        a[y0:y1, x0:x1] = np.maximum(a[y0:y1, x0:x1], k)

    def _frame(self):
        if not self.alive:
            return
        t = time.time() - self.t0
        st, sm = self.state, self.sm
        main, deep, bright, warn = (np.array(c) for c in THEMES[self.theme])
        e = lambda k, v, r: sm.__setitem__(k, sm[k] + (v - sm[k]) * r)
        e("lvl", min(1.0, self.level * 1.6) if st == "listening" else 0.0, 0.3)
        e("energy", {"listening": .5, "understanding": .7, "thinking": .9, "working": 1, "speaking": .8, "waiting": .3, "starting": .1}.get(st, .2), .08)
        e("bands", 1.0 if st in ("thinking", "working", "understanding") else 0.0, .07)
        e("gold", 1.0 if st == "waiting" else 0.0, .1)
        e("dim", 0.45 if st == "starting" else 1.0, .06)
        tgt = (bright if st == "speaking" else main) * (1 - sm["gold"]) + warn * sm["gold"]
        self.col = self.col + (tgt - self.col) * 0.12
        self.col2 = self.col2 + ((deep * (1 - sm["gold"] * .7) + warn * sm["gold"] * .7) - self.col2) * 0.12
        col, col2, dim = self.col, self.col2, sm["dim"]

        S = self.S
        R0 = S * 0.27
        speak = (.07 * abs(math.sin(t * 7.3)) + .04 * abs(math.sin(t * 11.1))) if st == "speaking" else 0
        beat = .05 * max(0, math.sin(t * 4)) ** 8 if st == "waiting" else 0
        R = R0 * (1 + .025 * math.sin(t * 1.7) + speak + beat + sm["lvl"] * .1)
        dn = self.d / R
        # glass sphere body: soft halo, faint fill, bright rim, specular highlight
        halo = np.clip(1 - dn / 1.85, 0, 1) ** 2.2 * .45 * dim
        fill = np.where(dn < 1, .22 + .16 * dn ** 3, 0) * dim
        rim = np.exp(-((dn - 1) * R / (1.3 * self.scale)) ** 2) * .75 * dim
        hx, hy = self.dx / R + .38, self.dy / R + .42
        spec = np.exp(-(hx ** 2 / .05 + hy ** 2 / .02)) * .55 * (dn < 1) * dim
        a = np.clip(halo + fill + rim + spec, 0, 1).astype(np.float32)
        rgb = (halo[..., None] * col + fill[..., None] * col2 * .35 + rim[..., None] * (col * .5 + .5) + spec[..., None] * 1.0).astype(np.float32)

        # particles (rotate, bands, ripples)
        P = self.P
        x, y, z = P[:, 0].copy(), P[:, 1].copy(), P[:, 2].copy()
        b = sm["bands"]
        if b > .01:
            ring = np.round(self.lat / .55) * .55
            sign = np.where(np.round(self.lat / .55) % 2 == 1, -.8, .8)
            ang = np.arctan2(z, x) + t * (1 + sign)
            x = x + (np.cos(ang) * np.cos(ring) - x) * b; y = y + (np.sin(ring) - y) * b; z = z + (np.sin(ang) * np.cos(ring) - z) * b
        wob = 1 + sm["lvl"] * .22 * np.sin(self.seed * 3 + t * 14 + y * 6) + .03 * np.sin(self.seed + t * 2)
        if st == "speaking":
            wob = wob + .06 * np.sin(t * 9 - y * 5)
        x, y, z = x * wob, y * wob, z * wob
        ry = t * (.25 + sm["energy"] * 1.2); rx = .35 + .15 * math.sin(t * .3)
        x1 = x * math.cos(ry) - z * math.sin(ry); z1 = x * math.sin(ry) + z * math.cos(ry)
        y2 = y * math.cos(rx) - z1 * math.sin(rx); z2 = y * math.sin(rx) + z1 * math.cos(rx)
        order = np.argsort(z2)
        for i in order:
            dd = (z2[i] + 1) / 2
            spr = self.sprites[min(3, int(dd * 4))]
            c = col2 + (col - col2) * dd
            self._splat(rgb, a, self.cx + x1[i] * R, self.cy + y2[i] * R, spr, c, (.35 + dd * .65) * dim)
        # working: progress arc
        if st == "working" or b > .5:
            a0 = t * 3.2; span = 1.2 + .6 * math.sin(t * 2)
            for k in range(28):
                ang = a0 + span * k / 27
                self._splat(rgb, a, self.cx + math.cos(ang) * R * 1.32, self.cy + math.sin(ang) * R * 1.32, self.sprites[2], col, .9)
        # tick ring
        for k in range(36):
            ang = k / 36 * 6.283 - t * .15
            r1 = R * 1.45
            self._splat(rgb, a, self.cx + math.cos(ang) * r1, self.cy + math.sin(ang) * r1, self.sprites[0], col2, .45 * dim if k % 3 else .8 * dim)

        a = np.clip(a, 0, 1)
        rgb = np.minimum(rgb, a[..., None])               # premultiplied: color <= alpha
        out = self.buf
        out[..., 0] = (rgb[..., 2] * 255).astype(np.uint8)
        out[..., 1] = (rgb[..., 1] * 255).astype(np.uint8)
        out[..., 2] = (rgb[..., 0] * 255).astype(np.uint8)
        out[..., 3] = (a * 255).astype(np.uint8)
        size = wintypes.SIZE(S, S); src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)                # AC_SRC_OVER, AC_SRC_ALPHA
        user32.UpdateLayeredWindow(self.hwnd, self.screen_dc, None, ctypes.byref(size), self.mem_dc, ctypes.byref(src), 0,
                                   ctypes.byref(blend), 2)  # ULW_ALPHA


if __name__ == "__main__":   # quick look: python orb_native.py  (cycles the states)
    import sys
    o = NativeOrb(300, 300, theme=sys.argv[1] if len(sys.argv) > 1 else "glass", on_click=lambda: print("click"), on_right=lambda: print("right"))
    for s in ["idle", "listening", "thinking", "working", "speaking", "waiting", "idle"]:
        o.set(s, 0.7); print(s); time.sleep(2.5)
    o.destroy(); time.sleep(0.3)
