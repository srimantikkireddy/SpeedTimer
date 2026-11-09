"""
SpeedTimer v2 - a taskbar-style countdown that shows REAL time left
when you watch at 1.25x / 1.5x / 2x / 3x ...

 * Click the ring = play / pause
 * Syncs with Windows media: when your video pauses, the timer pauses;
   when it plays, the timer resumes. Pausing the timer pauses the video too.
 * Hotkey: Ctrl+Alt+Space = pause / resume
 * Right-click the pill for options (sync on/off, which apps to follow)

Run: double-click this file (needs Python for Windows).
"""
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SpeedTimer")
CFG_FILE = os.path.join(APP_DIR, "config.json")

# ---- colours (Windows 11 dark look) ----
KEY = "#010101"
PILL = "#202733"
BORDER = "#3b4657"
HOVER = "#303d4e"
TEXT = "#ffffff"
SUB = "#b3bfce"
RING_BG = "#455366"
ACCENT = "#69d2ff"
DONE = "#6ccb5f"
PANEL = "#18202b"
FIELD = "#263140"
ERR = "#ff6b6b"

W, H = 300, 46  # compact two-line pill

PRESETS = [
    ("tb_left", "Taskbar \u2013 left side (default)"),
    ("tb_right", "Taskbar \u2013 right side (before tray)"),
    ("above_center", "Above taskbar \u2013 center"),
    ("bottom_left", "Bottom-left corner"),
    ("bottom_right", "Bottom-right corner"),
    ("top_left", "Top-left corner"),
    ("top_center", "Top \u2013 center"),
    ("top_right", "Top-right corner"),
]
ZOOMS = [(0.6, "Tiny  (60%)"), (0.75, "Small  (75%)"), (0.85, "Compact  (85%)"),
         (1.0, "Default  (100%)"), (1.1, "Comfortable  (110%)"),
         (1.25, "Large  (125%)"), (1.4, "Extra large  (140%)"),
         (1.6, "XL  (160%)"), (1.8, "XXL  (180%)"), (2.0, "Huge  (200%)"),
         (2.25, "Giant  (225%)"), (2.5, "Maximum  (250%)")]
LENGTHS = [(240, "Compact  (240 px)"), (270, "Short  (270 px)"),
           (300, "Standard  (300 px)"), (340, "Wide  (340 px)"),
           (380, "Extra wide  (380 px)"), (420, "Maximum  (420 px)")]
OPACITIES = [(1.0, "Opaque  (100%)"), (0.9, "90%"), (0.8, "80%"),
             (0.7, "70%"), (0.6, "60%"), (0.5, "50%"), (0.4, "40%"),
             (0.3, "30%"), (0.2, "20%"), (0.1, "10%")]

# Music apps are ignored by default (so your song pausing doesn't pause the timer).
# You can change this per app from the right-click menu.
DEFAULT_IGNORE = ("spotify", "itunes", "applemusic", "zunemusic", "groove", "amazonmusic")
BROWSER_HINTS = ("chrome", "msedge", "edge", "firefox", "brave", "opera", "zen")


# ---------------------------------------------------------------- config
def load_cfg():
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass


# ---------------------------------------------------------------- parsing/format
def parse_hms(h, m, s):
    """Three text fields (blank = 0) -> total seconds."""
    def num(t):
        t = t.strip()
        return float(t) if t else 0.0
    total = num(h) * 3600 + num(m) * 60 + num(s)
    if total <= 0 or min(num(h), num(m), num(s)) < 0:
        raise ValueError
    return total


def parse_speed(text):
    t = text.strip().lower().replace("x", "").replace("\u00d7", "").strip()
    v = float(t)
    if not 0.25 <= v <= 16:
        raise ValueError
    return v


def fmt_clock(sec):
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_speed(v):
    return f"{v:g}\u00d7"


def media_seconds(value):
    """Convert a WinRT TimeSpan/tick value to seconds."""
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    # python-winrt normally projects TimeSpan as timedelta; some versions expose ticks.
    return float(value) / 10_000_000.0


def friendly(app_id):
    return app_id.split("!")[0].split("_")[0] or app_id


def is_browser_session(app_id):
    low = app_id.lower()
    return any(name in low for name in BROWSER_HINTS)


# ---------------------------------------------------------------- Windows media sessions
class Media:
    """Reads play/pause state of every app that reports to Windows media controls,
    and can send pause/play to them. Runs in a background thread."""

    PIP = ["winrt-runtime", "winrt-Windows.Media.Control",
           "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections"]

    def __init__(self):
        self.ok = False
        self.error = None
        self.state = {}       # app_id -> is_playing
        self.timelines = {}   # app_id -> media timeline sample
        self.media_titles = {}  # app_id -> current Windows media title
        self.playing_since = {}
        self.loop = None
        self.mgr = None
        threading.Thread(target=self._run, daemon=True).start()

    def _import(self):
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Mgr,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PS,
        )
        return Mgr, PS

    def _run(self):
        try:
            try:
                Mgr, PS = self._import()
            except Exception:
                # first run: install the small helper packages automatically
                subprocess.run([sys.executable, "-m", "pip", "install", "--quiet"] + self.PIP,
                               creationflags=0x08000000, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240)
                import importlib
                importlib.invalidate_caches()
                Mgr, PS = self._import()
            try:
                from winrt.runtime import ApartmentType, init_apartment
                init_apartment(ApartmentType.MULTI_THREADED)
            except Exception:
                pass
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._poll(Mgr, PS))
        except Exception as exc:
            self.error = repr(exc)
            self.ok = False

    async def _poll(self, Mgr, PS):
        self.mgr = await Mgr.request_async()
        self.ok = True
        title_cache = {}
        title_checked = {}

        async def refresh_title(session, app_id):
            try:
                props = await session.try_get_media_properties_async()
                title_cache[app_id] = (props.title or props.subtitle or "").strip()
            except Exception:
                title_cache.setdefault(app_id, "")

        while True:
            try:
                data = {}
                timelines = {}
                titles = {}
                now = time.monotonic()
                for ss in self.mgr.get_sessions():
                    info = ss.get_playback_info()
                    app_id = ss.source_app_user_model_id
                    playing = info.playback_status == PS.PLAYING
                    data[app_id] = playing
                    if playing and not self.state.get(app_id, False):
                        self.playing_since[app_id] = now
                    elif app_id not in self.playing_since:
                        self.playing_since[app_id] = now
                    if now - title_checked.get(app_id, 0) >= 2.0:
                        title_checked[app_id] = now
                        asyncio.create_task(refresh_title(ss, app_id))
                    if title_cache.get(app_id):
                        titles[app_id] = title_cache[app_id]
                    try:
                        timeline = ss.get_timeline_properties()
                        start = media_seconds(timeline.start_time)
                        end = media_seconds(timeline.end_time)
                        position = media_seconds(timeline.position)
                        if end > start and position >= start:
                            try:
                                reported_rate = info.playback_rate
                                reported_rate = float(reported_rate) if reported_rate is not None else None
                            except Exception:
                                reported_rate = None
                            timelines[app_id] = {
                                "start": start, "end": end, "position": position,
                                "sampled_at": now, "playing": playing,
                                "rate": reported_rate,
                            }
                    except Exception:
                        pass
                self.state = data
                self.timelines = timelines
                self.media_titles = titles
            except Exception:
                pass
            await asyncio.sleep(0.5)

    def command(self, action, ids):
        if not self.ok or not self.loop or not ids:
            return
        asyncio.run_coroutine_threadsafe(self._cmd(action, set(ids)), self.loop)

    async def _cmd(self, action, ids):
        try:
            for ss in self.mgr.get_sessions():
                if ss.source_app_user_model_id in ids:
                    if action == "pause":
                        await ss.try_pause_async()
                    else:
                        await ss.try_play_async()
        except Exception:
            pass


# ---------------------------------------------------------------- the app
class SpeedTimer:
    def __init__(self):
        self.cfg = load_cfg()
        self.root = tk.Tk()
        self.root.title("SpeedTimer")
        self.s0 = self.root.winfo_fpixels("1i") / 96.0      # Windows display scaling
        self.zoom = min(max(self.cfg.get("zoom", 1.0), 0.6), 2.5)   # widget size
        try:
            self.design_w = min(max(int(self.cfg.get("widget_length", W)), 240), 420)
        except (TypeError, ValueError):
            self.design_w = W
        try:
            self.opacity = min(max(float(self.cfg.get("opacity", 0.9)), 0.1), 1.0)
        except (TypeError, ValueError):
            self.opacity = 0.9
        self.s = self.s0 * self.zoom
        self.w, self.h = int(self.design_w * self.s), int(H * self.s)

        self.length_sec = self.cfg.get("length_sec") or self.cfg.get("length", 0) * 60
        self.speed = self.cfg.get("speed", 1.5)
        self.total = self.length_sec / self.speed if self.length_sec else 0
        self.remaining = self.total
        self.running = False
        self.done = False
        self.end_mono = 0.0
        self.hover = None
        self.editor = None
        self._hk_down = False
        self._press = None
        self._timeline_active = False
        self._timeline_key = None
        self._timeline_sample_seen = None
        self._timeline_last_position = None
        self._timeline_last_playing = None
        self._timeline_moving_until = 0.0
        self._manual_override = None
        self._manual_override_app = None
        self._manual_override_status = None
        self.display_speed = self.speed
        self.speed_from_media = False
        self.current_media_title = ""
        self.current_media_app = ""

        # media sync state
        self.media = Media()
        self.last_ids = []
        self.grace_until = 0.0
        self._m_stable = None
        self._m_cand = None
        self._m_t = 0.0

        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", bool(self.cfg.get("always_on_top", False)))
        r.attributes("-alpha", 1.0)
        r.configure(bg=KEY)
        try:
            r.attributes("-transparentcolor", KEY)
        except Exception:
            pass

        # Keep the translucent pill on its own layered window so labels and
        # controls remain fully opaque and readable.
        self.bg_root = tk.Toplevel(r)
        self.bg_root.overrideredirect(True)
        self.bg_root.attributes("-topmost", bool(self.cfg.get("always_on_top", False)))
        self.bg_root.configure(bg=KEY)
        try:
            self.bg_root.attributes("-transparentcolor", KEY)
        except Exception:
            pass
        self.bg_canvas = tk.Canvas(self.bg_root, width=self.w, height=self.h,
                                   bg=KEY, highlightthickness=0, bd=0)
        self.bg_canvas.pack()
        self.apply_opacity(self.opacity, save=False)

        r.geometry(f"{self.w}x{self.h}+0+0")
        self.bg_root.geometry(f"{self.w}x{self.h}+0+0")
        startup = self.cfg.get("startup_position", {})
        if startup.get("mode") == "preset" and startup.get("preset") in dict(PRESETS):
            self.set_preset(startup["preset"], save=False)
        elif startup.get("mode") == "custom" and "x" in startup and "y" in startup:
            self.set_custom_position(startup["x"], startup["y"])
        elif self.cfg.get("preset"):
            self.set_preset(self.cfg["preset"], save=False)
        elif "x" in self.cfg and "y" in self.cfg:
            x = min(max(self.cfg["x"], 0), r.winfo_screenwidth() - self.w)
            y = min(max(self.cfg["y"], 0), r.winfo_screenheight() - self.h)
            r.geometry(f"+{x}+{y}")
        else:
            self.set_preset("tb_left", save=False)
        self.align_background()

        self.c = tk.Canvas(r, width=self.w, height=self.h, bg=KEY, highlightthickness=0, bd=0)
        self.c.pack()

        # the ring itself is the play/pause button
        self.btns = {
            "play": (27, 23, 17),
            "reset": (self.design_w - 54, 23, 14),
            "edit": (self.design_w - 24, 23, 14),
        }
        self._build_static()

        self.c.bind("<Motion>", self.on_motion)
        self.c.bind("<Leave>", lambda e: self.set_hover(None))
        self.c.bind("<ButtonPress-1>", self.on_press)
        self.c.bind("<B1-Motion>", self.on_drag)
        self.c.bind("<ButtonRelease-1>", self.on_release)
        self.c.bind("<Button-3>", self.on_menu)
        self.c.bind("<MouseWheel>", self.on_wheel)
        self.c.bind("<Double-Button-1>",
                    lambda e: self.open_editor() if self.hit(e.x, e.y) is None else None)
        # Transparent pixels in the foreground text layer pass pointer input
        # through to this translucent background layer.
        self.bg_canvas.bind("<Motion>", self.on_motion)
        self.bg_canvas.bind("<Leave>", lambda e: self.set_hover(None))
        self.bg_canvas.bind("<ButtonPress-1>", self.on_press)
        self.bg_canvas.bind("<B1-Motion>", self.on_drag)
        self.bg_canvas.bind("<ButtonRelease-1>", self.on_release)
        self.bg_canvas.bind("<Button-3>", self.on_menu)
        self.bg_canvas.bind("<MouseWheel>", self.on_wheel)
        self.bg_canvas.bind("<Double-Button-1>",
                            lambda e: self.open_editor() if self.hit(e.x, e.y) is None else None)
        r.bind("<Configure>", self.on_root_configure, add="+")
        r.update_idletasks()
        self.align_background()

        self.tick()
        self.keep_on_top()

    # ---------- drawing ----------
    def sc(self, v):
        return int(round(v * self.s))

    def rrect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.bg_canvas.create_polygon(pts, smooth=True, **kw)

    def _build_static(self):
        sc, c = self.sc, self.bg_canvas
        c.delete("all")
        self.rrect(1, 1, self.w - 2, self.h - 2, sc(22), fill=PILL, outline=BORDER)
        self.hover_items = {}
        for name, (cx, cy, r) in self.btns.items():
            self.hover_items[name] = c.create_oval(
                sc(cx - r), sc(cy - r), sc(cx + r), sc(cy + r), fill=PILL, outline="")

    def sync_active(self):
        return bool(self.cfg.get("sync", True)) and self.media.ok

    def draw(self):
        sc, c = self.sc, self.c
        c.delete("dyn")

        has = self.total > 0
        frac = 0.0
        if has:
            frac = 1 - (self.remaining / self.total)
        frac = min(max(frac, 0), 0.9999)

        # progress ring (clickable play/pause)
        rcx, rcy, rr = 27, 23, 12
        box = (sc(rcx - rr), sc(rcy - rr), sc(rcx + rr), sc(rcy + rr))
        col = DONE if self.done else ACCENT
        c.create_oval(*box, outline=RING_BG, width=sc(3), tags="dyn")
        if has and frac > 0:
            c.create_arc(*box, start=90, extent=-frac * 360, style="arc",
                         outline=col, width=sc(3), tags="dyn")
        if self.done:
            c.create_text(sc(rcx), sc(rcy), text="\u2713", fill=DONE,
                          font=("Segoe UI Symbol", -sc(13), "bold"), tags="dyn")
        else:
            gcol = TEXT if has else "#777777"
            self._glyph(rcx, rcy, 0.75, gcol, pause=self.running)

        # Keep the remaining time and media/app name together on the first row.
        if not has:
            main = "--:--"
            sub = "Click \u270e to set length & speed"
        elif self.done:
            main, sub = "Done", "Finished \u00b7 click ring to restart"
        else:
            main = fmt_clock(self.remaining)
            shown_speed = getattr(self, "display_speed", self.speed)
            speed_tag = "  \u00b7  media" if self.speed_from_media else ""
            if self._timeline_active and not self.speed_from_media:
                speed_tag = "  ·  entered"
            auto = speed_tag if self._timeline_active else (
                "  \u00b7  auto" if self.sync_active() else "")
            if self.running:
                finish = datetime.now() + timedelta(seconds=self.remaining)
                endtxt = finish.strftime("%I:%M %p").lstrip("0")
                sub = f"Playing  \u00b7  {fmt_speed(shown_speed)}{auto}  \u00b7  Est {endtxt}"
            elif self.sync_active() and self.remaining >= self.total - 0.5:
                sub = f"Waiting for video  \u00b7  {fmt_speed(shown_speed)}"
            else:
                sub = f"Paused  \u00b7  {fmt_speed(shown_speed)}{auto}  \u00b7  Est \u2014"

        media_label = self.current_media_title
        if not media_label or len(media_label) > 20:
            media_label = self.current_media_app or media_label
        main_x = 52
        main_width = len(main) * 9.8
        label_x = main_x + main_width + 13
        right_limit = self.design_w - 72
        title_room = max(0, right_limit - label_x)
        max_title_chars = int(title_room / 6.2)
        if len(media_label) > max_title_chars:
            media_label = (media_label[:max(0, max_title_chars - 1)].rstrip() + "\u2026"
                           if max_title_chars > 1 else "")

        status_room = max(8, int((self.design_w - 66) / 5.4))
        if len(sub) > status_room:
            sub = sub[:max(1, status_room - 1)].rstrip() + "\u2026"

        c.create_text(sc(main_x), sc(17), text=main, anchor="w",
                      fill=DONE if self.done else TEXT,
                      font=("Segoe UI Semibold", -sc(19)), tags="dyn")
        if media_label:
            c.create_text(sc(label_x), sc(17), text=media_label, anchor="w", fill=TEXT,
                          font=("Segoe UI Semibold", -sc(11)), tags="dyn")
        c.create_text(sc(main_x), sc(35), text=sub, anchor="w", fill=SUB,
                      font=("Segoe UI", -sc(9)), tags="dyn")

        rx, ry, _ = self.btns["reset"]
        c.create_text(sc(rx), sc(ry) - 1, text="\u21ba", fill=TEXT,
                      font=("Segoe UI Symbol", -sc(17)), tags="dyn")
        ex, ey, _ = self.btns["edit"]
        c.create_text(sc(ex), sc(ey), text="\u270e", fill=TEXT,
                      font=("Segoe UI Symbol", -sc(15)), tags="dyn")

    def _glyph(self, cx, cy, k, color, pause):
        sc, c = self.sc, self.c
        u = 6 * k
        if pause:
            w = 2.6 * k
            for dx in (-0.7, 0.7):
                c.create_rectangle(sc(cx + u * dx - w / 2), sc(cy - u),
                                   sc(cx + u * dx + w / 2), sc(cy + u),
                                   fill=color, outline="", tags="dyn")
        else:
            c.create_polygon(sc(cx - u * 0.6), sc(cy - u), sc(cx - u * 0.6), sc(cy + u),
                             sc(cx + u * 1.0), sc(cy), fill=color, outline="", tags="dyn")

    # ---------- interaction ----------
    def hit(self, x, y):
        for name, (cx, cy, r) in self.btns.items():
            if (x - self.sc(cx)) ** 2 + (y - self.sc(cy)) ** 2 <= self.sc(r) ** 2:
                return name
        return None

    def set_hover(self, name):
        if name == self.hover:
            return
        self.hover = name
        for n, item in self.hover_items.items():
            self.bg_canvas.itemconfigure(item, fill=HOVER if n == name else PILL)

    def on_motion(self, e):
        self.set_hover(self.hit(e.x, e.y))

    def on_press(self, e):
        self._press = {"btn": self.hit(e.x, e.y), "mx": e.x_root, "my": e.y_root,
                       "wx": self.root.winfo_x(), "wy": self.root.winfo_y(), "moved": False}

    def on_drag(self, e):
        p = self._press
        if not p or p["btn"]:
            return
        dx, dy = e.x_root - p["mx"], e.y_root - p["my"]
        if abs(dx) + abs(dy) > 3:
            p["moved"] = True
        self.root.geometry(f"+{p['wx'] + dx}+{p['wy'] + dy}")
        self.align_background()

    def on_release(self, e):
        p, self._press = self._press, None
        if not p:
            return
        if p["btn"]:
            if self.hit(e.x, e.y) == p["btn"]:
                {"play": self.toggle, "reset": self.reset, "edit": self.open_editor}[p["btn"]]()
        elif p["moved"]:
            self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
            self.cfg.pop("preset", None)      # custom spot now
            save_cfg(self.cfg)

    def set_custom_position(self, x, y):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = min(max(int(x), 0), max(0, sw - self.w))
        y = min(max(int(y), 0), max(0, sh - self.h))
        self.root.geometry(f"+{x}+{y}")
        self.align_background()

    def align_background(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.bg_root.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def on_root_configure(self, event):
        if event.widget == self.root:
            self.bg_root.geometry(f"{self.w}x{self.h}+{event.x}+{event.y}")

    def on_wheel(self, e):
        if e.state & 0x0004:                  # Ctrl held
            self.apply_zoom(self.zoom + (0.1 if e.delta > 0 else -0.1))

    # ---------- right-click menu ----------
    def is_followed(self, app_id):
        ov = self.cfg.get("follow", {})
        if app_id in ov:
            return ov[app_id]
        low = app_id.lower()
        return not any(k in low for k in DEFAULT_IGNORE)

    def on_menu(self, e):
        m = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT,
                    activebackground=HOVER, activeforeground=TEXT, bd=0)
        m.add_command(label="Set timer…", command=self.open_editor)
        m.add_command(label="Pause / Resume   ·   Ctrl+Alt+Space", command=self.toggle)
        m.add_separator()

        settings = tk.Menu(m, tearoff=0, bg=PANEL, fg=TEXT,
                           activebackground=HOVER, activeforeground=TEXT, bd=0)

        sync_var = tk.BooleanVar(value=bool(self.cfg.get("sync", True)))

        def set_sync():
            self.cfg["sync"] = sync_var.get()
            save_cfg(self.cfg)
            self._m_stable = None

        settings.add_checkbutton(label="Sync with media (auto pause/resume)",
                                 variable=sync_var, command=set_sync)

        topmost_var = tk.BooleanVar(value=bool(self.cfg.get("always_on_top", False)))

        def set_topmost():
            self.cfg["always_on_top"] = topmost_var.get()
            save_cfg(self.cfg)
            self.root.attributes("-topmost", topmost_var.get())
            self.bg_root.attributes("-topmost", topmost_var.get())
            if topmost_var.get():
                self.bg_root.lift()
                self.root.lift()

        settings.add_checkbutton(label="Keep above fullscreen windows",
                                 variable=topmost_var, command=set_topmost)

        timeline_var = tk.BooleanVar(value=bool(self.cfg.get("media_remaining", True)))

        def set_timeline():
            self.cfg["media_remaining"] = timeline_var.get()
            save_cfg(self.cfg)
            self._timeline_key = None
            if not timeline_var.get():
                self.display_speed = self.speed
                self.speed_from_media = False

        settings.add_checkbutton(label="Use media position for remaining time",
                                 variable=timeline_var, command=set_timeline)

        sub = tk.Menu(m, tearoff=0, bg=PANEL, fg=TEXT,
                      activebackground=HOVER, activeforeground=TEXT, bd=0)
        if not self.media.ok:
            sub.add_command(label="Media detection unavailable", state="disabled")
        elif not self.media.state:
            sub.add_command(label="No media apps detected right now", state="disabled")
        else:
            for app_id, playing in sorted(self.media.state.items()):
                var = tk.BooleanVar(value=self.is_followed(app_id))

                def toggle_src(a=app_id, v=var):
                    self.cfg.setdefault("follow", {})[a] = v.get()
                    save_cfg(self.cfg)
                    self._m_stable = None

                label = friendly(app_id) + ("  (playing)" if playing else "")
                sub.add_checkbutton(label=label, variable=var, command=toggle_src)
        settings.add_cascade(label="Media sources to follow", menu=sub)

        def submenu():
            return tk.Menu(m, tearoff=0, bg=PANEL, fg=TEXT,
                           activebackground=HOVER, activeforeground=TEXT, bd=0)

        pos = submenu()
        cur = self.cfg.get("preset")
        for key, label in PRESETS:
            mark = "\u25cf  " if key == cur else "    "
            pos.add_command(label=mark + label, command=lambda k=key: self.set_preset(k))
        pos.add_separator()
        pos.add_command(label="    (or just drag the widget anywhere)", state="disabled")
        settings.add_cascade(label="Move widget to", menu=pos)

        startup_menu = submenu()
        startup_cfg = self.cfg.get("startup_position", {})
        startup_label = ("Saved custom location" if startup_cfg.get("mode") == "custom"
                         else dict(PRESETS).get(startup_cfg.get("preset"), "Current position"))
        startup_menu.add_command(label=f"On launch: {startup_label}", state="disabled")
        startup_menu.add_separator()
        for key, label in PRESETS:
            mark = "●  " if startup_cfg.get("mode") == "preset" and startup_cfg.get("preset") == key else "    "
            startup_menu.add_command(label=mark + label,
                                     command=lambda k=key: self.set_startup_preset(k))
        startup_menu.add_separator()
        startup_menu.add_command(label="Save current location for startup",
                                 command=self.save_startup_custom)
        settings.add_cascade(label="Startup position", menu=startup_menu)

        siz = submenu()
        for z, label in ZOOMS:
            mark = "\u25cf  " if abs(z - self.zoom) < 0.05 else "    "
            siz.add_command(label=mark + label, command=lambda z=z: self.apply_zoom(z))
        siz.add_separator()
        siz.add_command(label="    Tip: Ctrl + scroll on the widget", state="disabled")
        settings.add_cascade(label=f"Widget size  ({int(round(self.zoom * 100))}%)", menu=siz)

        length_menu = submenu()
        for width, label in LENGTHS:
            mark = "●  " if width == self.design_w else "    "
            length_menu.add_command(label=mark + label,
                                    command=lambda width=width: self.apply_length(width))
        settings.add_cascade(label=f"Widget length  ({self.design_w} px)", menu=length_menu)

        transparency = submenu()
        for value, label in OPACITIES:
            mark = "●  " if abs(value - self.opacity) < 0.06 else "    "
            transparency.add_command(
                label=mark + label,
                command=lambda value=value: self.apply_opacity(value))
        settings.add_cascade(label=f"Transparency  ({int(round(self.opacity * 100))}%)",
                             menu=transparency)

        m.add_cascade(label="Settings", menu=settings)

        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        try:
            # Keep the menu clear of the pill, which otherwise can cover its
            # first row while the widget is topmost.
            m.update_idletasks()
            mw, mh = m.winfo_reqwidth(), m.winfo_reqheight()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
            below = wy + self.h + self.sc(4)
            above = wy - mh - self.sc(4)
            if below + mh <= sh:
                my = below
            elif above >= 0:
                my = above
            else:
                my = min(max(e.y_root, 0), max(0, sh - mh))
            mx = min(max(e.x_root, 0), max(0, sw - mw))
            m.tk_popup(mx, my)
        finally:
            m.grab_release()

    def set_startup_preset(self, key):
        self.cfg["startup_position"] = {"mode": "preset", "preset": key}
        save_cfg(self.cfg)

    def save_startup_custom(self):
        self.cfg["startup_position"] = {
            "mode": "custom", "x": self.root.winfo_x(), "y": self.root.winfo_y()
        }
        save_cfg(self.cfg)

    def apply_opacity(self, value, save=True):
        self.opacity = min(max(float(value), 0.1), 1.0)
        try:
            self.bg_root.attributes("-alpha", self.opacity)
        except tk.TclError:
            pass
        if save:
            self.cfg["opacity"] = self.opacity
            save_cfg(self.cfg)

    def taskbar_rect(self):
        """(left, top, right, bottom) of the real Windows taskbar."""
        try:
            class RECT(ctypes.Structure):
                _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                            ("r", ctypes.c_long), ("b", ctypes.c_long)]
            hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            rc = RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
            if rc.r - rc.l > 0 and rc.b - rc.t > 0:
                return rc.l, rc.t, rc.r, rc.b
        except Exception:
            pass
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return 0, sh - int(48 * self.s0), sw, sh

    def set_preset(self, key, save=True):
        r, s0 = self.root, self.s0
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        tl, tt, tr, tbm = self.taskbar_rect()
        w, h = self.w, self.h
        m = int(12 * s0)
        mid_y = (tt + tbm - h) // 2                  # vertically centred on the taskbar
        spots = {
            "tb_left": (tl + int(190 * s0), mid_y),
            "tb_right": (tr - w - int(330 * s0), mid_y),
            "above_center": ((sw - w) // 2, tt - h - m),
            "bottom_left": (m, tt - h - m),
            "bottom_right": (sw - w - m, tt - h - m),
            "top_left": (m, m),
            "top_center": ((sw - w) // 2, m),
            "top_right": (sw - w - m, m),
        }
        x, y = spots.get(key, spots["tb_left"])
        x = min(max(x, 0), sw - w)
        y = min(max(y, 0), sh - h)
        r.geometry(f"+{x}+{y}")
        self.align_background()
        if save:
            self.cfg["preset"] = key
            self.cfg["x"], self.cfg["y"] = x, y
            save_cfg(self.cfg)

    def apply_zoom(self, z):
        z = round(min(max(z, 0.6), 2.5), 2)
        if abs(z - self.zoom) < 0.001:
            return
        old_h = self.h
        self.zoom = z
        self.s = self.s0 * z
        self.w, self.h = int(self.design_w * self.s), int(H * self.s)
        self.c.configure(width=self.w, height=self.h)
        self.bg_canvas.configure(width=self.w, height=self.h)
        self.c.delete("all")
        self.hover = None
        self._build_static()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        y += (old_h - self.h) // 2                    # keep the vertical centre
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self.align_background()
        self.cfg["zoom"] = z
        if self.cfg.get("preset"):
            self.set_preset(self.cfg["preset"])      # re-anchor (e.g. right-side presets)
        else:
            self.cfg["x"], self.cfg["y"] = x, y
        save_cfg(self.cfg)
        self.draw()

    def apply_length(self, width):
        width = min(max(int(width), 240), 420)
        if width == self.design_w:
            return
        old_w = self.w
        self.design_w = width
        self.w = int(self.design_w * self.s)
        self.btns["reset"] = (self.design_w - 54, 23, 14)
        self.btns["edit"] = (self.design_w - 24, 23, 14)
        self.c.configure(width=self.w, height=self.h)
        self.bg_canvas.configure(width=self.w, height=self.h)
        self.c.delete("all")
        self.hover = None
        self._build_static()
        x = self.root.winfo_x() + (old_w - self.w) // 2
        y = self.root.winfo_y()
        x = min(max(x, 0), max(0, self.root.winfo_screenwidth() - self.w))
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self.align_background()
        self.cfg["widget_length"] = self.design_w
        if self.cfg.get("preset"):
            self.set_preset(self.cfg["preset"])
        else:
            self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
            save_cfg(self.cfg)
        self.draw()

    def quit(self):
        self.root.destroy()

    # ---------- timer logic ----------
    def followed_ids_playing(self):
        return [a for a, p in self.media.state.items() if p and self.is_followed(a)]

    def active_timeline(self):
        """Choose the most recently started followed session that is still playing."""
        candidates = self.followed_ids_playing()
        if not candidates:
            # Keep the most recently active followed session while it is paused so
            # its current position remains visible and the timer pauses with it.
            candidates = [a for a, p in self.media.state.items() if self.is_followed(a)]
        if not candidates:
            return None, None
        app_id = max(candidates, key=lambda a: self.media.playing_since.get(a, 0))
        return app_id, self.media.timelines.get(app_id)

    def sync_timeline(self):
        """Use Windows' timeline when available; otherwise retain the manual timer."""
        app_id, data = self.active_timeline()
        if app_id:
            self.current_media_title = (self.media.media_titles.get(app_id) or friendly(app_id))
            self.current_media_app = friendly(app_id)
        else:
            self.current_media_title = ""
            self.current_media_app = ""
        if not self.cfg.get("media_remaining", True):
            self._timeline_active = False
            self._timeline_key = None
            self._timeline_sample_seen = None
            self._timeline_last_position = None
            self._timeline_last_playing = None
            self._timeline_moving_until = 0.0
            return
        if not data:
            if self._timeline_active:
                self.running = False
                self._timeline_active = False
                self._timeline_key = None
                self._timeline_sample_seen = None
                self._timeline_last_position = None
                self._timeline_last_playing = None
                self._timeline_moving_until = 0.0
                self.speed_from_media = False
                self.display_speed = self.speed
            return
        self._timeline_active = True
        start, end = data["start"], data["end"]
        if (self._manual_override is not None and
                (app_id != self._manual_override_app or
                 data["playing"] != self._manual_override_status)):
            self._manual_override = None
            self._manual_override_app = None
            self._manual_override_status = None
        position = data["position"]
        rate = data.get("rate")
        self.speed_from_media = bool(rate is not None and 0.25 <= rate <= 16)
        # Browser extensions often change HTML video speed without updating
        # the Windows media session, which then continues to report 1.0x.
        # Treat that browser default as unavailable and use the speed entered
        # in SpeedTimer instead.
        if is_browser_session(app_id) and self.speed_from_media and abs(rate - 1.0) < 0.02:
            self.speed_from_media = False
        effective_speed = rate if self.speed_from_media else self.speed
        self.display_speed = effective_speed
        key = (app_id, round(start, 2), round(end, 2), round(effective_speed, 3))
        new_item = key != self._timeline_key
        if new_item:
            self._timeline_key = key
            self.total = max(0.0, (end - start) / effective_speed)
            self.done = False
        sample_key = (app_id, data["sampled_at"])
        now = time.monotonic()
        new_sample = sample_key != self._timeline_sample_seen
        anchored = False
        if new_item or new_sample:
            self._timeline_sample_seen = sample_key
            position = data["position"]
            moved = (self._timeline_last_position is None or
                     abs(position - self._timeline_last_position) >= 0.25)
            if (self._timeline_last_position is not None and
                    position - self._timeline_last_position >= 0.25):
                self._timeline_moving_until = now + 1.5
            state_changed = data["playing"] != self._timeline_last_playing
            if new_item or moved or state_changed:
                projected_position = position
                if data["playing"]:
                    projected_position += max(0.0, now - data["sampled_at"]) * effective_speed
                projected_position = min(max(projected_position, start), end)
                self.remaining = max(0.0, (end - projected_position) / effective_speed)
                self.end_mono = now + self.remaining
                anchored = True
            self._timeline_last_position = position
            self._timeline_last_playing = data["playing"]
        if self.running and not anchored:
            self.remaining = max(0.0, self.end_mono - now)
        media_playing = data["playing"] or now < self._timeline_moving_until
        if self.sync_active() and now >= self.grace_until:
            desired_running = (self._manual_override if self._manual_override is not None
                               else media_playing)
            self.running = bool(desired_running and self.remaining > 0)
        if self.remaining <= 0:
            self.remaining = 0
            self.running = False
            self.done = True

    def toggle(self):
        """Manual pause/resume (ring, hotkey). Also controls the video."""
        if self.total <= 0:
            self.open_editor()
            return
        app_id, media_data = self.active_timeline()
        prior_media_state = media_data["playing"] if media_data else None
        if self.done:
            self.reset()
        now = time.monotonic()
        self.grace_until = now + 2.0
        if self.running:
            self.remaining = max(0, self.end_mono - now)
            self.running = False
            if self.sync_active():
                ids = self.followed_ids_playing()
                if ids:
                    self.last_ids = ids
                self.media.command("pause", ids)
        else:
            self.end_mono = now + self.remaining
            self.running = True
            if self.sync_active() and not self.followed_ids_playing():
                self.media.command("play", self.last_ids)
        if app_id is not None and prior_media_state is not None:
            self._manual_override = self.running
            self._manual_override_app = app_id
            self._manual_override_status = prior_media_state
        self.draw()

    def reset(self):
        self.running = False
        self.done = False
        self.remaining = self.total
        self.grace_until = time.monotonic() + 1.0
        self.draw()

    def start_new(self, length_sec, speed):
        self.length_sec, self.speed = length_sec, speed
        self.total = length_sec / speed
        self.remaining = self.total
        self.done = False
        now = time.monotonic()
        self.grace_until = now + 1.5
        if self.sync_active() and not self.followed_ids_playing():
            self.running = False          # wait until the video actually plays
        else:
            self.running = True
            self.end_mono = now + self.remaining
        self.cfg["length_sec"], self.cfg["speed"] = length_sec, speed
        self.cfg.pop("length", None)
        save_cfg(self.cfg)
        self.draw()

    def sync_media(self):
        if not self.sync_active():
            self._m_stable = None
            return
        now = time.monotonic()
        playing = bool(self.followed_ids_playing())
        if self._m_stable is None:
            self._m_stable = self._m_cand = playing
            return
        if playing == self._m_stable:
            self._m_cand = playing
            return
        if playing != self._m_cand:       # new candidate change: start debounce
            self._m_cand = playing
            self._m_t = now
            return
        if now - self._m_t >= 1.0:        # change held for 1s -> commit
            self._m_stable = playing
            self.on_media_change(playing, self._m_t)

    def on_media_change(self, playing, t):
        if time.monotonic() < self.grace_until:
            return                         # we caused this ourselves
        if self.total <= 0 or self.done:
            return
        if playing and not self.running:
            self.end_mono = t + self.remaining     # resume as of the moment it started
            self.running = True
        elif not playing and self.running:
            self.remaining = max(0, self.end_mono - t)   # pause as of the moment it stopped
            self.running = False
            self.last_ids = self.last_ids  # keep last known ids for manual resume

    def remember_ids(self):
        ids = self.followed_ids_playing()
        if ids:
            self.last_ids = ids

    def tick(self):
        self.remember_ids()
        self.sync_media()
        self.sync_timeline()
        if self.running and not self._timeline_active:
            self.remaining = self.end_mono - time.monotonic()
            if self.remaining <= 0:
                self.remaining = 0
                self.running = False
                self.done = True
                try:
                    import winsound
                    winsound.MessageBeep()
                except Exception:
                    pass
        self.draw()
        self.poll_hotkey()
        self.root.after(250, self.tick)

    def poll_hotkey(self):
        try:
            g = ctypes.windll.user32.GetAsyncKeyState
            down = bool(g(0x11) & 0x8000 and g(0x12) & 0x8000 and g(0x20) & 0x8000)
        except Exception:
            return
        if down and not self._hk_down:
            self.toggle()
        self._hk_down = down

    def keep_on_top(self):
        try:
            self.align_background()
            # Stay over ordinary windows, but yield the topmost band to a
            # fullscreen app unless the user explicitly asks to stay above it.
            enabled = bool(self.cfg.get("always_on_top", False))
            fullscreen = self.foreground_is_fullscreen()
            show_above = enabled or not fullscreen
            self.root.attributes("-topmost", show_above)
            self.bg_root.attributes("-topmost", show_above)
            if show_above:
                self.bg_root.lift()
                self.root.lift()
        except Exception:
            pass
        self.root.after(1500, self.keep_on_top)

    def foreground_is_fullscreen(self):
        """Return whether the foreground window covers its monitor."""
        try:
            user32 = ctypes.windll.user32

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

            user32.GetForegroundWindow.restype = ctypes.c_void_p
            user32.MonitorFromWindow.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            user32.MonitorFromWindow.restype = ctypes.c_void_p
            user32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.POINTER(MONITORINFO))
            user32.GetMonitorInfoW.restype = ctypes.c_int
            user32.GetWindowRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(RECT))
            user32.GetWindowRect.restype = ctypes.c_int
            hwnd = user32.GetForegroundWindow()
            if not hwnd or hwnd == self.root.winfo_id():
                return False
            monitor = user32.MonitorFromWindow(hwnd, 2)  # nearest monitor
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return False
            rect = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False
            bounds = info.rcMonitor
            return (rect.left <= bounds.left and rect.top <= bounds.top and
                    rect.right >= bounds.right and rect.bottom >= bounds.bottom)
        except Exception:
            return False

    # ---------- editor popup ----------
    def open_editor(self):
        if self.editor is not None and self.editor.winfo_exists():
            self.editor.destroy()
            self.editor = None
            return
        sc = lambda v: int(round(v * self.s0))      # popup ignores the widget zoom
        d = tk.Toplevel(self.root)
        self.editor = d
        d.overrideredirect(True)
        d.attributes("-topmost", bool(self.cfg.get("always_on_top", False)))
        d.configure(bg=BORDER)
        box = tk.Frame(d, bg=PANEL)
        box.pack(padx=1, pady=1)
        pad = sc(16)

        def lab(text, top=0):
            tk.Label(box, text=text, bg=PANEL, fg=SUB, anchor="w",
                     font=("Segoe UI", -sc(11))).pack(fill="x", padx=pad, pady=(top, sc(4)))

        tk.Label(box, text="Set timer", bg=PANEL, fg=TEXT, anchor="w",
                 font=("Segoe UI Semibold", -sc(16))).pack(fill="x", padx=pad, pady=(pad, sc(10)))

        lab("Episode / movie length")
        ls = int(round(self.length_sec or 0))
        hms_vars = [tk.StringVar(value=str(ls // 3600) if ls else ""),
                    tk.StringVar(value=str((ls % 3600) // 60) if ls else ""),
                    tk.StringVar(value=str(ls % 60) if ls else "")]
        hms_row = tk.Frame(box, bg=PANEL)
        hms_row.pack(fill="x", padx=pad)
        hms_entries = []
        for i, (var, unit) in enumerate(zip(hms_vars, ("hr", "min", "sec"))):
            e = tk.Entry(hms_row, textvariable=var, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=("Segoe UI", -sc(15)), width=4, justify="center",
                         highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
            e.pack(side="left", ipady=sc(5))
            tk.Label(hms_row, text=unit, bg=PANEL, fg=SUB,
                     font=("Segoe UI", -sc(11))).pack(side="left", padx=(sc(4), sc(10)))
            hms_entries.append(e)
        len_e = hms_entries[1]            # focus the minutes box first
        len_var = hms_vars[1]

        lab("Playback speed", top=sc(12))
        spd_var = tk.StringVar(value=f"{self.speed:g}")
        spd_e = tk.Entry(box, textvariable=spd_var, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=("Segoe UI", -sc(15)), width=18,
                         highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        spd_e.pack(fill="x", padx=pad, ipady=sc(5))

        chips = tk.Frame(box, bg=PANEL)
        chips.pack(fill="x", padx=pad, pady=(sc(8), 0))

        for v in (1, 1.25, 1.5, 2, 2.5, 3):
            ch = tk.Label(chips, text=fmt_speed(v), bg=FIELD, fg=TEXT, cursor="hand2",
                          font=("Segoe UI", -sc(11)), padx=sc(7), pady=sc(3))
            ch.pack(side="left", padx=(0, sc(5)))
            ch.bind("<Button-1>", lambda e, v=v: spd_var.set(f"{v:g}"))
            ch.bind("<Enter>", lambda e, w=ch: w.configure(bg=HOVER))
            ch.bind("<Leave>", lambda e, w=ch: w.configure(bg=FIELD))

        info = tk.Label(box, text="", bg=PANEL, fg=SUB, anchor="w", font=("Segoe UI", -sc(11)))
        info.pack(fill="x", padx=pad, pady=(sc(10), 0))

        def refresh_info(*_):
            try:
                real = parse_hms(*[v.get() for v in hms_vars]) / parse_speed(spd_var.get())
                info.configure(text=f"Real time: {fmt_clock(real)}", fg=ACCENT)
            except Exception:
                info.configure(text="", fg=SUB)

        for v in hms_vars:
            v.trace_add("write", refresh_info)
        spd_var.trace_add("write", refresh_info)
        refresh_info()

        def go(_=None):
            ok = True
            try:
                L = parse_hms(*[v.get() for v in hms_vars])
            except Exception:
                for e in hms_entries:
                    e.configure(highlightbackground=ERR, highlightcolor=ERR)
                ok = False
            try:
                S = parse_speed(spd_var.get())
            except Exception:
                spd_e.configure(highlightbackground=ERR, highlightcolor=ERR)
                ok = False
            if not ok:
                return
            self.close_editor()
            self.start_new(L, S)

        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x", padx=pad, pady=(sc(12), pad))

        def button(text, bg, fg, cmd):
            b = tk.Label(row, text=text, bg=bg, fg=fg, cursor="hand2",
                         font=("Segoe UI Semibold", -sc(12)), padx=sc(16), pady=sc(7))
            b.pack(side="left", padx=(0, sc(8)))
            b.bind("<Button-1>", lambda e: cmd())

        button("Start", ACCENT, "#003040", go)
        button("Cancel", FIELD, TEXT, self.close_editor)

        d.bind("<Return>", go)
        d.bind("<Escape>", lambda e: self.close_editor())

        def close_if_focus_left(_event=None):
            def check():
                if self.editor is not d or not d.winfo_exists():
                    return
                focused = d.focus_get()
                if focused is None or not str(focused).startswith(str(d)):
                    self.close_editor()
            d.after(80, check)

        d.bind("<FocusOut>", close_if_focus_left, add="+")

        d.update_idletasks()
        ew, eh = d.winfo_reqwidth(), d.winfo_reqheight()
        x = min(max(self.root.winfo_x(), 0), self.root.winfo_screenwidth() - ew)
        y = self.root.winfo_y() - eh - sc(10)
        d.geometry(f"{ew}x{eh}+{x}+{max(y, 0)}")
        d.focus_force()
        len_e.focus_set()
        len_e.select_range(0, "end")

    def close_editor(self):
        if self.editor is not None and self.editor.winfo_exists():
            self.editor.destroy()
        self.editor = None

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    SpeedTimer().run()
