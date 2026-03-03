#!/usr/bin/env python3
"""DEX Panel — Panel personalizado con sistema de temas (Default / Windows / macOS)"""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Wnck, Pango

import json, math, os, shlex, shutil, subprocess, sys, time, zipfile
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════
#  PATHS & DEFAULTS
# ═══════════════════════════════════════════════════════════════════════

PROJ_DIR = Path(__file__).resolve().parent
LOCAL_GOOGLE_ICONS_DIR = PROJ_DIR / "icons" / "Google"

CFG_DIR    = Path.home() / ".config" / "dex-panel"
FAV_FILE   = CFG_DIR / "favorites.json"
SET_FILE   = CFG_DIR / "settings.json"
THEMES_DIR = CFG_DIR / "themes"
for _d in (CFG_DIR, THEMES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULTS = {
    "theme":            "default",
    "panel_position":   "bottom",
    "panel_height":     48,
    "bg_color":         "#141418",
    "bg_opacity":       95,
    "icon_size":        32,
    "clock_format":     "%H:%M",
    "clock_show_date":  True,
    "clock_date_format":"%d/%m/%Y",
    "show_clock":       True,
    "show_taskbar":     True,
    "app_menu_cmd":     "",
    "app_menu_name":    "",
    "magnify_size":     68,
    "magnify_radius":   130,
    "dock_show_running": True,
    "dock_fav_click_activates": True,
    "dock_bg_padding": 14,
    "dock_bg_radius": 18,
    "dock_bg_y_offset": -7,
    "compositor":       True,
    "animations_enabled": True,
    "animation_ms":     140,
    "clock_show_seconds": False,
    "clock_color":      "#e0e0e0",
    "clock_date_color": "#999999",
    "clock_font_size":  0,
    "clock_timezone":   "",
    "custom_widgets":   [],
    "single_plugin_enabled": False,
    "single_plugin_cmd": "",
    "single_plugin_name": "Plugin",
    "single_plugin_icon": "",
    "autostart_enabled": False,
    "autostart_prompted": False,
    "first_setup_done": False,
}

BUILTIN_THEMES = {
    "default": {
        "name": "Default",
        "layout": "default",
        "description": "Panel clásico con iconos",
        "panel_height": 48, "bg_color": "#141418",
        "bg_opacity": 95, "icon_size": 32,
    },
    "windows": {
        "name": "Windows",
        "layout": "windows",
        "description": "Barra de tareas estilo Windows",
        "panel_height": 50, "bg_color": "#1a1a2e",
        "bg_opacity": 98, "icon_size": 30,
    },
    "macos": {
        "name": "macOS Dock",
        "layout": "macos",
        "description": "Dock estilo macOS con magnificación",
        "panel_height": 80, "bg_color": "#2d2d3a",
        "bg_opacity": 70, "icon_size": 48,
        "magnify_size": 72, "magnify_radius": 140,
    },
}

# ═══════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════

_DEBUG = os.environ.get("DEX_PANEL_DEBUG", "").strip().lower() not in ("", "0", "false", "no")


def _log(msg: str):
    if _DEBUG:
        print(f"[dex-panel] {msg}", file=sys.stderr)

def _pid_matches_dex_panel(pid: int) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "ignore").replace("\x00", " ").strip().lower()
    except Exception:
        return False
    if not cmdline:
        return False
    return ("dex-panel" in cmdline) or ("main.py" in cmdline and "python" in cmdline)


def _load_cfg():
    c = dict(DEFAULTS)
    loaded = {}
    try:
        loaded = json.loads(SET_FILE.read_text())
        if isinstance(loaded, dict):
            c.update(loaded)
    except Exception as e:
        _log(f"settings load failed: {e}")
    # Defensive migration for malformed user-edited values.
    c["bg_color"] = _safe_css_color(c.get("bg_color"), DEFAULTS["bg_color"])
    c["clock_color"] = _safe_css_color(c.get("clock_color"), DEFAULTS["clock_color"])
    c["clock_date_color"] = _safe_css_color(c.get("clock_date_color"), DEFAULTS["clock_date_color"])
    # Track which keys came from disk for migration decisions. Not persisted.
    c["_loaded_keys"] = set(loaded.keys()) if isinstance(loaded, dict) else set()
    return c

def _save_cfg(c):
    safe = {k: v for k, v in c.items() if not k.startswith("_")}
    SET_FILE.write_text(json.dumps(safe, indent=2))

def _load_fav():
    try: return json.loads(FAV_FILE.read_text())
    except Exception: return []

def _save_fav(f):
    FAV_FILE.write_text(json.dumps(f, indent=2))

def _hex(h):
    h = h.lstrip("#")
    if len(h) == 3: h = "".join(x*2 for x in h)
    return int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)

def _safe_css_color(value, fallback):
    s = str(value or "").strip()
    if not s:
        return fallback
    try:
        rgba = Gdk.RGBA()
        if rgba.parse(s):
            return s
    except Exception:
        pass
    return fallback

def _tint_pixbuf(pb, rgb=(235, 235, 235)):
    """Force non-transparent pixels to a single color (good for monochrome icons)."""
    try:
        if pb is None:
            return None
        cs = pb.get_colorspace()
        has_alpha = pb.get_has_alpha()
        bits = pb.get_bits_per_sample()
        w, h = pb.get_width(), pb.get_height()
        rs = pb.get_rowstride()
        n = pb.get_n_channels()
        if cs != GdkPixbuf.Colorspace.RGB or bits != 8 or n < 3:
            return pb

        data = bytearray(pb.get_pixels())
        rr, gg, bb = rgb
        for y in range(h):
            off = y * rs
            for x in range(w):
                i = off + x * n
                if has_alpha and data[i + 3] == 0:
                    continue
                data[i] = rr
                data[i + 1] = gg
                data[i + 2] = bb

        def destroy_fn(pixels, data=None):
            return

        return GdkPixbuf.Pixbuf.new_from_data(data, cs, has_alpha, bits, w, h, rs, destroy_fn, None)
    except Exception:
        return pb

def _icon_pb(name, size):
    if not name: return None
    th = Gtk.IconTheme.get_default()
    if name.startswith("/") and os.path.isfile(name):
        try: return GdkPixbuf.Pixbuf.new_from_file_at_scale(name, size, size, True)
        except Exception: pass
    try: return th.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
    except Exception: pass
    # Local fallback: project bundled icons (e.g. icons/Google/<name>.png|svg)
    try:
        base = name
        # Some desktop entries use "xxx-symbolic" names; try without the suffix too.
        if base.endswith("-symbolic"):
            base2 = base[:-len("-symbolic")]
        else:
            base2 = ""
        for b in [base, base2, base.lower(), base2.lower() if base2 else ""]:
            if not b:
                continue
            for ext in (".png", ".svg", ".xpm"):
                p = LOCAL_GOOGLE_ICONS_DIR / f"{b}{ext}"
                if p.is_file():
                    try:
                        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(p), size, size, True)
                        # This pack is black-on-transparent; tint so it renders on dark panel.
                        return _tint_pixbuf(pb)
                    except Exception:
                        pass
    except Exception:
        pass
    try: return th.load_icon("application-x-executable", size, Gtk.IconLookupFlags.FORCE_SIZE)
    except Exception: return None

def _menu_btn(
    label: str,
    icon_name: str | None,
    cb,
    css_class: str | None = "popup-item",
    icon_px: int = 18,
    relief: Gtk.ReliefStyle = Gtk.ReliefStyle.NONE,
):
    b = Gtk.Button()
    if css_class:
        b.get_style_context().add_class(css_class)
    b.set_relief(relief)
    hb = Gtk.Box(spacing=10)
    hb.set_halign(Gtk.Align.FILL)
    if icon_name:
        pb = _icon_pb(icon_name, icon_px)
        if pb:
            hb.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
        else:
            hb.pack_start(Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.MENU),
                          False, False, 0)
    lb = Gtk.Label(label=label)
    lb.set_halign(Gtk.Align.START)
    hb.pack_start(lb, True, True, 0)
    b.add(hb)
    b.connect("clicked", lambda *_: cb())
    return b

# ═══════════════════════════════════════════════════════════════════════
#  DESKTOP APPS
# ═══════════════════════════════════════════════════════════════════════

class DesktopApp:
    _cache = None
    def __init__(self, did, name, exe, icon, wmclass=""):
        self.desktop_id = did; self.name = name
        self.exec_cmd = exe;  self.icon_name = icon
        self.wmclass = wmclass or ""

    @classmethod
    def load_all(cls):
        if cls._cache is not None: return cls._cache
        dirs = ["/usr/share/applications","/usr/local/share/applications",
                str(Path.home()/".local/share/applications")]
        apps = {}
        for d in dirs:
            p = Path(d)
            if not p.is_dir(): continue
            for f in p.glob("*.desktop"):
                if f.name in apps: continue
                try:
                    a = cls._parse(f)
                    if a: apps[f.name] = a
                except Exception: pass
        cls._cache = apps
        return apps

    @classmethod
    def _parse(cls, path):
        name, exe, icon, wmclass, hide, inside = path.stem, "", "", "", False, False
        for ln in path.read_text(errors="ignore").splitlines():
            s = ln.strip()
            if s == "[Desktop Entry]": inside = True; continue
            if s.startswith("[") and s.endswith("]"):
                if inside: break
                continue
            if not inside: continue
            if s.startswith("Name=") and name == path.stem:
                name = s.split("=",1)[1]
            elif s.startswith("Exec="):
                exe = s.split("=",1)[1]
                for c in ["%f","%F","%u","%U","%d","%D","%n","%N","%i","%c","%k"]:
                    exe = exe.replace(c,"")
                exe = exe.strip()
            elif s.startswith("Icon="): icon = s.split("=",1)[1].strip()
            elif s.startswith("StartupWMClass="): wmclass = s.split("=",1)[1].strip()
            elif s == "NoDisplay=true": hide = True
        if hide or not exe: return None
        return cls(path.name, name, exe, icon, wmclass=wmclass)

# ═══════════════════════════════════════════════════════════════════════
#  THEME MANAGER
# ═══════════════════════════════════════════════════════════════════════

class ThemeManager:
    def __init__(self):
        for tid, data in BUILTIN_THEMES.items():
            d = THEMES_DIR / tid; d.mkdir(exist_ok=True)
            tf = d / "theme.json"
            # Do not overwrite user edits to built-in themes.
            if not tf.exists():
                tf.write_text(json.dumps(data, indent=2))

    def list_themes(self):
        themes = {}
        for d in sorted(THEMES_DIR.iterdir()):
            tf = d / "theme.json"
            if d.is_dir() and tf.exists():
                try: themes[d.name] = json.loads(tf.read_text())
                except Exception: pass
        return themes

    def get_theme(self, tid):
        tf = THEMES_DIR / tid / "theme.json"
        if tf.exists():
            try: return json.loads(tf.read_text())
            except Exception: pass
        return BUILTIN_THEMES.get("default", {})

    def import_zip(self, zip_path):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                tj = None; prefix = ""
                for n in zf.namelist():
                    if n.endswith("theme.json"):
                        tj = n; prefix = n.rsplit("theme.json",1)[0]; break
                if not tj: return None
                data = json.loads(zf.read(tj))
                tid = data.get("name","custom").lower().replace(" ","-")
                dest = THEMES_DIR / tid; dest.mkdir(parents=True, exist_ok=True)
                for n in zf.namelist():
                    if n.startswith(prefix):
                        rel = n[len(prefix):]
                        if not rel or rel.endswith("/"): continue
                        t = dest / rel; t.parent.mkdir(parents=True, exist_ok=True)
                        t.write_bytes(zf.read(n))
                return tid
        except Exception as e:
            print(f"[Theme import error] {e}"); return None

# ═══════════════════════════════════════════════════════════════════════
#  CSS BUILDER
# ═══════════════════════════════════════════════════════════════════════

def _build_css(cfg):
    r, g, b = _hex(cfg.get("bg_color","#141418"))
    op   = cfg.get("bg_opacity",95) / 100
    h    = cfg.get("panel_height",48)
    fs   = max(10, h // 4)
    cfs  = int(cfg.get("clock_font_size", 0) or 0)
    if cfs <= 0:
        cfs = fs
    clk_col = _safe_css_color(cfg.get("clock_color", "#e0e0e0"), "#e0e0e0")
    dt_col  = _safe_css_color(cfg.get("clock_date_color", "#999999"), "#999999")
    isz  = cfg.get("icon_size",32)
    lay  = cfg.get("_layout","default")
    pos  = cfg.get("panel_position","bottom")
    bord = "top" if pos == "bottom" else "bottom"
    if lay == "windows":
        start_bg = "rgba(70,130,255,0.42)"
        start_bg_hover = "rgba(70,130,255,0.64)"
        popup_bg = "#252934"
    elif lay == "macos":
        start_bg = "rgba(255,255,255,0.12)"
        start_bg_hover = "rgba(255,255,255,0.22)"
        popup_bg = "rgba(34,36,44,0.94)"
    else:
        start_bg = "rgba(80,140,255,0.3)"
        start_bg_hover = "rgba(80,140,255,0.5)"
        popup_bg = "#2a2a2e"

    return f"""
#dex-panel {{
    background-color: {"transparent" if lay=="macos" else f"rgba({r},{g},{b},{op:.2f})"};
    border-{bord}: {"none" if lay=="macos" else "1px solid rgba(255,255,255,0.08)"};
}}
#panel-content {{ padding: 2px 6px; }}

.launcher {{
    background: transparent; border: none; border-radius: 6px;
    padding: 4px; min-width: {isz+8}px; min-height: {isz+8}px;
}}
.launcher:hover {{ background-color: rgba(255,255,255,0.12); }}
.launcher:active {{ background-color: rgba(255,255,255,0.20); }}

.panel-sep {{
    background-color: rgba(255,255,255,0.15);
    min-width: 1px; margin: 6px 4px;
}}

.task-btn {{
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 5px; padding: 4px;
    min-width: {isz+12}px; min-height: {isz+8}px;
}}
.task-btn:hover {{
    background: rgba(255,255,255,0.14);
    border-color: rgba(255,255,255,0.18);
}}
.task-active {{
    background: rgba(80,140,255,0.25);
    border: 1px solid rgba(80,140,255,0.5);
}}

.start-btn {{
    background: {start_bg};
    border: 1px solid rgba(120,170,255,0.42);
    border-radius: 4px; padding: 4px 14px;
    color: white; font-weight: bold; font-size: {fs}px;
    min-height: {h-12}px;
}}
.start-btn:hover {{ background: {start_bg_hover}; }}

.dock-item {{
    background: transparent; border: none;
    border-radius: 10px; padding: 2px;
}}
.dock-item:hover {{ background: rgba(255,255,255,0.06); }}

.active-dot {{
    background-color: rgba(255,255,255,0.85);
    border-radius: 2px; min-width: 5px; min-height: 5px;
}}
.inactive-dot {{
    background-color: rgba(255,255,255,0.35);
    min-width: 5px; min-height: 5px;
}}

.clock {{ color: {clk_col}; font-size: {cfs}px; font-weight: 600; padding: 0 10px; }}
.clock-date {{ color: {dt_col}; font-size: {max(8,cfs-2)}px; padding: 0 10px; }}
.widget-btn {{
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 6px;
    padding: 4px 8px;
    color: #e8e8e8;
}}
.widget-btn:hover {{ background: rgba(255,255,255,0.14); }}

.popup-menu {{
    background-color: {popup_bg};
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px; padding: 4px 0;
}}
.popup-item {{
    background: transparent; color: #e0e0e0; border: none;
    padding: 8px 16px; font-size: 13px; min-width: 200px;
}}
.popup-item:hover {{ background-color: rgba(80,140,255,0.3); }}
.popup-sep {{
    background-color: rgba(255,255,255,0.1);
    min-height: 1px; margin: 3px 10px;
}}
.prefs-win {{ background-color: #2a2a2e; }}
.prefs-win label {{ color: #e0e0e0; }}
.prefs-win entry {{ background: #3a3a3e; color: #e0e0e0; border: 1px solid #555; border-radius: 4px; padding: 4px 8px; }}
.prefs-win spinbutton {{ background: #3a3a3e; color: #e0e0e0; }}
"""

# ═══════════════════════════════════════════════════════════════════════
#  DEX PANEL
# ═══════════════════════════════════════════════════════════════════════

class DexPanel(Gtk.Window):

    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.cfg        = _load_cfg()
        try:
            # Persist sanitized values (e.g. broken color strings) so next run is stable.
            _save_cfg(self.cfg)
        except Exception:
            pass
        self._fav       = _load_fav()
        self._tbtns     = {}          # wnck_win -> {button, container, dot?}
        self._ditems    = []          # macOS dock items for magnification
        self._popup     = None
        self._picom_proc = None       # picom process we started (avoid killing user's picom)
        self._css       = Gtk.CssProvider()
        self._themes    = ThemeManager()
        self._clock_lbl = None
        self._date_lbl  = None
        self._fav_widgets = {}        # desktop_id -> {app, btn, dot?}
        self._relayout_source_id = 0

        self._merge_theme()
        self._ensure_default_favorites()

        # ── window setup ──
        self.set_name("dex-panel"); self.set_title("dex-panel")
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_decorated(False); self.set_keep_above(True)
        self.stick()
        self.set_skip_taskbar_hint(True); self.set_skip_pager_hint(True)

        scr = self.get_screen()
        vis = scr.get_rgba_visual()
        if vis: self.set_visual(vis); self.set_app_paintable(True)
        if scr:
            # Keep panel geometry/struts aligned after monitor changes or rotation.
            scr.connect("size-changed", self._schedule_relayout)
            scr.connect("monitors-changed", self._schedule_relayout)

        self.connect("draw", self._on_draw)
        self.connect("configure-event", self._schedule_relayout)
        self._refresh_css()
        self._build()
        self._position()
        self.show_all()

        GLib.timeout_add(500, self._set_struts)
        GLib.timeout_add(600, self._transset)
        GLib.timeout_add(800, lambda: (self._sync_compositor(), False)[-1])

        # ── wnck ──
        self._wnck = Wnck.Screen.get_default()
        self._wnck.force_update()
        self._wnck.connect("window-opened",        self._w_open)
        self._wnck.connect("window-closed",        self._w_close)
        self._wnck.connect("active-window-changed", self._w_active)
        self._scan_windows()

        # ── clock ──
        self._tick_clock()
        GLib.timeout_add_seconds(1, self._tick_clock)

        self.connect("button-press-event", self._on_rclick)
        GLib.idle_add(self._maybe_run_first_setup)

    # ───────────────────────────────────────────────────────────────────
    #  THEME MERGE
    # ───────────────────────────────────────────────────────────────────

    def _merge_theme(self, force: bool = False):
        tid   = self.cfg.get("theme","default")
        theme = self._themes.get_theme(tid)
        self.cfg["_layout"] = theme.get("layout","default")
        loaded_keys = self.cfg.get("_loaded_keys") or set()
        applied = self.cfg.get("theme_applied")
        if not force and applied == tid:
            return

        # Migration: older versions had no marker. If the user already had a theme set,
        # assume the current values are intentional and just set the marker once.
        if (
            not force
            and (applied is None or applied == "")
            and "theme" in loaded_keys
            and "theme_applied" not in loaded_keys
        ):
            self.cfg["theme_applied"] = tid
            return

        for k in ("panel_height", "bg_color", "bg_opacity", "icon_size", "magnify_size", "magnify_radius"):
            if k in theme:
                self.cfg[k] = theme[k]
        self.cfg["theme_applied"] = tid

    # ───────────────────────────────────────────────────────────────────
    #  DRAW (background)
    # ───────────────────────────────────────────────────────────────────

    def _on_draw(self, w, cr):
        lay = self.cfg.get("_layout","default")
        r, g, b = _hex(self.cfg.get("bg_color","#141418"))
        op = self.cfg.get("bg_opacity",95) / 100

        if lay == "macos":
            # clear to transparent
            cr.set_source_rgba(0,0,0,0); cr.set_operator(0); cr.paint(); cr.set_operator(2)
            # draw rounded rect behind dock
            if hasattr(self, '_dock_box'):
                a = self._dock_box.get_allocation()
                if a.width > 1 and a.height > 1:
                    pad = int(self.cfg.get("dock_bg_padding", 14))
                    rad = int(self.cfg.get("dock_bg_radius", 18))
                    yoff = int(self.cfg.get("dock_bg_y_offset", -(pad // 2)))

                    # Allocation x/y are relative to the parent; translate to window coords
                    # so the background sits behind the centered dock.
                    try:
                        wx, wy = self._dock_box.translate_coordinates(self, 0, 0)
                    except Exception:
                        wx, wy = a.x, a.y

                    x, y2 = wx - pad, wy + yoff
                    bw, bh = a.width + pad * 2, a.height + pad
                    cr.new_sub_path()
                    cr.arc(x+bw-rad, y2+rad, rad, -math.pi/2, 0)
                    cr.arc(x+bw-rad, y2+bh-rad, rad, 0, math.pi/2)
                    cr.arc(x+rad,    y2+bh-rad, rad, math.pi/2, math.pi)
                    cr.arc(x+rad,    y2+rad,    rad, math.pi, 3*math.pi/2)
                    cr.close_path()
                    cr.set_source_rgba(r/255, g/255, b/255, op)
                    cr.fill()
        else:
            cr.set_source_rgba(r/255, g/255, b/255, op)
            cr.set_operator(2); cr.paint()
        return False

    # ───────────────────────────────────────────────────────────────────
    #  CSS
    # ───────────────────────────────────────────────────────────────────

    def _refresh_css(self):
        self._css.load_from_data(_build_css(self.cfg).encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ───────────────────────────────────────────────────────────────────
    #  BUILD (dispatch by layout)
    # ───────────────────────────────────────────────────────────────────

    def _build(self):
        ch = self.get_child()
        if ch: self.remove(ch)
        self._tbtns  = {}
        self._ditems = []
        self._clock_lbl = self._date_lbl = None
        self._fav_widgets = {}

        lay = self.cfg.get("_layout","default")
        if   lay == "windows": self._build_windows()
        elif lay == "macos":   self._build_macos()
        else:                  self._build_default()

    # ── DEFAULT ───────────────────────────────────────────────────────

    def _build_default(self):
        box = Gtk.Box(spacing=0); box.set_name("panel-content")
        self._lbox = Gtk.Box(spacing=2)
        self._fill_launchers()
        box.pack_start(self._lbox, False, False, 0)
        if self._fav: box.pack_start(self._sep(), False, False, 4)
        self._tbox = Gtk.Box(spacing=3)
        if self.cfg.get("show_taskbar", True):
            box.pack_start(self._tbox, True, True, 0)
        box.pack_end(self._right_zone(), False, False, 0)
        self.add(box)

    # ── WINDOWS ───────────────────────────────────────────────────────

    def _build_windows(self):
        box = Gtk.Box(spacing=0); box.set_name("panel-content")
        # start button — show selected app name or generic
        menu_name = self.cfg.get("app_menu_name","") or "☰ Menú"
        sb = Gtk.Button()
        sb.get_style_context().add_class("start-btn")
        sb.set_relief(Gtk.ReliefStyle.NONE)
        sb_box = Gtk.Box(spacing=6)
        # Try to get app icon
        app_icon = None
        mapp = self._app_for_menu_cmd()
        if mapp:
            app_icon = mapp.icon_name
        if app_icon:
            pb = _icon_pb(app_icon, 20)
            if pb:
                im = Gtk.Image.new_from_pixbuf(pb)
                im.set_halign(Gtk.Align.CENTER)
                im.set_valign(Gtk.Align.CENTER)
                sb_box.pack_start(im, False, False, 0)
        sb_box.pack_start(Gtk.Label(label=menu_name), False, False, 0)
        sb.add(sb_box)
        sb.connect("clicked", self._start_click)
        box.pack_start(sb, False, False, 4)
        box.pack_start(self._sep(), False, False, 4)
        # launchers
        self._lbox = Gtk.Box(spacing=2)
        self._fill_launchers()
        box.pack_start(self._lbox, False, False, 0)
        if self._fav: box.pack_start(self._sep(), False, False, 4)
        # taskbar
        self._tbox = Gtk.Box(spacing=3)
        if self.cfg.get("show_taskbar", True):
            box.pack_start(self._tbox, True, True, 0)
        # clock
        box.pack_end(self._right_zone(), False, False, 0)
        self.add(box)

    # ── MACOS ─────────────────────────────────────────────────────────

    def _build_macos(self):
        outer = Gtk.Box(spacing=0)
        outer.set_name("panel-content")
        outer.set_halign(Gtk.Align.FILL)

        self._dock_box = Gtk.Box(spacing=4)
        self._dock_box.set_halign(Gtk.Align.CENTER)
        self._dock_box.set_valign(Gtk.Align.END)

        # Menu button in dock (if configured)
        menu_cmd = self.cfg.get("app_menu_cmd","")
        if menu_cmd:
            isz = self.cfg.get("icon_size", 48)
            mb = Gtk.Button()
            mb.get_style_context().add_class("dock-item")
            mb.set_relief(Gtk.ReliefStyle.NONE)
            mb.set_tooltip_text(self.cfg.get("app_menu_name","") or "Menú")
            app_icon = None
            mapp = self._app_for_menu_cmd()
            if mapp:
                app_icon = mapp.icon_name
            pb = _icon_pb(app_icon or "view-app-grid-symbolic", isz)
            img = Gtk.Image()
            if pb: img.set_from_pixbuf(pb)
            else:  img.set_from_icon_name("view-app-grid-symbolic", Gtk.IconSize.DIALOG)
            img.set_halign(Gtk.Align.CENTER)
            img.set_valign(Gtk.Align.CENTER)
            mb.set_image(img); mb.set_size_request(isz+8, isz+8)
            mb.set_always_show_image(True)
            mb.connect("clicked", self._start_click)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            vb.set_valign(Gtk.Align.CENTER)
            vb.set_halign(Gtk.Align.CENTER)
            vb.pack_start(mb, False, False, 0)
            self._dock_box.pack_start(vb, False, False, 0)
            self._ditems.append({"btn":mb,"img":img,"pb":pb,"sz":isz,"vb":vb,"task":False})
            # separator after menu btn
            s = Gtk.Box(); s.get_style_context().add_class("panel-sep")
            s.set_size_request(2, -1)
            self._dock_box.pack_start(s, False, False, 4)

        # launchers in dock
        self._fill_launchers_dock()
        # separator between favorites and open apps
        if self._fav:
            s = Gtk.Box(); s.get_style_context().add_class("panel-sep")
            s.set_size_request(2, -1)
            self._dock_box.pack_start(s, False, False, 8)
        # taskbar for open windows
        self._tbox = Gtk.Box(spacing=4)
        if self.cfg.get("show_taskbar", True):
            self._dock_box.pack_start(self._tbox, False, False, 0)

        # Wrap dock in EventBox for magnification tracking
        eb = Gtk.EventBox()
        eb.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        eb.connect("motion-notify-event", self._dock_motion)
        eb.connect("leave-notify-event",  self._dock_leave)
        eb.add(self._dock_box)

        # Center the dock inside the full-width panel
        outer.pack_start(Gtk.Box(), True, True, 0)   # left spacer
        outer.pack_start(eb, False, False, 0)
        outer.pack_start(Gtk.Box(), True, True, 0)   # right spacer

        self.add(outer)

    # ───────────────────────────────────────────────────────────────────
    #  LAUNCHERS
    # ───────────────────────────────────────────────────────────────────

    def _app_for_menu_cmd(self):
        return self._app_for_cmd(self.cfg.get("app_menu_cmd", ""))

    def _app_for_cmd(self, cmd: str):
        raw = (cmd or "").strip()
        if not raw:
            return None
        apps = DesktopApp.load_all().values()
        for app in apps:
            if (app.exec_cmd or "").strip() == raw:
                return app
        try:
            c0 = shlex.split(raw)[0]
        except Exception:
            c0 = raw.split()[0] if raw.split() else ""
        if not c0:
            return None
        for app in apps:
            try:
                a0 = shlex.split(app.exec_cmd)[0]
            except Exception:
                a0 = app.exec_cmd.split()[0] if app.exec_cmd.split() else ""
            if a0 == c0:
                return app
        return None

    def _fill_launchers(self):
        for c in self._lbox.get_children(): self._lbox.remove(c)
        apps = DesktopApp.load_all()
        isz  = self.cfg.get("icon_size",32)
        for fid in self._fav:
            app = apps.get(fid)
            if not app: continue
            btn = Gtk.Button()
            btn.get_style_context().add_class("launcher")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_tooltip_text(app.name)
            pb = _icon_pb(app.icon_name, isz)
            btn.set_image(Gtk.Image.new_from_pixbuf(pb) if pb else
                          Gtk.Image.new_from_icon_name("application-x-executable",
                                                       Gtk.IconSize.LARGE_TOOLBAR))
            btn.connect("clicked", lambda b, a=app: self._fav_click(a))
            self._lbox.pack_start(btn, False, False, 0)
        self._lbox.show_all()

    def _fill_launchers_dock(self):
        apps = DesktopApp.load_all()
        isz  = self.cfg.get("icon_size",48)
        for fid in self._fav:
            app = apps.get(fid)
            if not app: continue
            pb = _icon_pb(app.icon_name, isz)
            if not pb: continue
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            vb.set_valign(Gtk.Align.CENTER)
            btn = Gtk.Button(); btn.get_style_context().add_class("dock-item")
            btn.set_relief(Gtk.ReliefStyle.NONE); btn.set_tooltip_text(app.name)
            img = Gtk.Image.new_from_pixbuf(pb)
            btn.set_image(img); btn.set_size_request(isz+8, isz+8)
            btn.connect("clicked", lambda b, a=app: self._fav_click(a))
            dot = Gtk.Box()
            dot.set_size_request(5, 5)
            dot.set_halign(Gtk.Align.CENTER)
            dot.hide()
            vb.pack_start(dot, False, False, 0)
            vb.pack_start(btn, False, False, 0)
            self._dock_box.pack_start(vb, False, False, 0)
            self._fav_widgets[app.desktop_id] = {"app": app, "btn": btn, "dot": dot}
            self._ditems.append({"btn":btn,"img":img,"pb":pb,"sz":isz,"vb":vb,"task":False, "dot": dot})
        self._update_fav_indicators()

    # ───────────────────────────────────────────────────────────────────
    #  MACOS MAGNIFICATION
    # ───────────────────────────────────────────────────────────────────

    def _dock_motion(self, eb, ev):
        if self.cfg.get("_layout") != "macos": return
        base = self.cfg.get("icon_size",48)
        mag  = self.cfg.get("magnify_size",72)
        rad  = self.cfg.get("magnify_radius",140)
        if mag <= base or rad <= 0:
            return

        # Translate event coords to dock_box-relative
        dock_alloc = self._dock_box.get_allocation()
        cx = ev.x - dock_alloc.x if dock_alloc.x > 0 else ev.x

        changed = False
        for it in self._ditems:
            a  = it["vb"].get_allocation()
            ic = a.x + a.width / 2
            d  = abs(cx - ic)
            sz = int(base + (mag - base) * (math.cos(math.pi * d / rad) + 1) / 2) if d < rad else base
            if sz != it["sz"]:
                it["sz"] = sz
                it["img"].set_from_pixbuf(it["pb"].scale_simple(sz, sz, GdkPixbuf.InterpType.BILINEAR))
                it["btn"].set_size_request(sz+8, sz+8)
                changed = True
        if changed: self.queue_draw()

    def _dock_leave(self, eb, ev):
        if self.cfg.get("_layout") != "macos": return
        base = self.cfg.get("icon_size",48)
        for it in self._ditems:
            if it["sz"] != base:
                it["sz"] = base
                it["img"].set_from_pixbuf(it["pb"].scale_simple(base, base, GdkPixbuf.InterpType.BILINEAR))
                it["btn"].set_size_request(base+8, base+8)
        self.queue_draw()

    # ───────────────────────────────────────────────────────────────────
    #  SHARED WIDGETS
    # ───────────────────────────────────────────────────────────────────

    def _sep(self):
        s = Gtk.Box(); s.get_style_context().add_class("panel-sep")
        s.set_size_request(1,-1); return s

    def _clock_widget(self):
        bx = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bx.set_valign(Gtk.Align.CENTER)
        self._clock_lbl = Gtk.Label(); self._clock_lbl.get_style_context().add_class("clock")
        bx.pack_start(self._clock_lbl, False, False, 0)
        self._date_lbl = Gtk.Label(); self._date_lbl.get_style_context().add_class("clock-date")
        bx.pack_start(self._date_lbl, False, False, 0)
        return bx

    def _custom_widgets_box(self):
        box = Gtk.Box(spacing=6)
        box.set_valign(Gtk.Align.CENTER)
        widgets = self.cfg.get("custom_widgets", [])
        if not isinstance(widgets, list):
            return box
        for w in widgets:
            if not isinstance(w, dict):
                continue
            name = str(w.get("name", "")).strip() or "Widget"
            cmd = str(w.get("command", "")).strip()
            if not cmd:
                continue
            icon = str(w.get("icon", "")).strip()
            b = Gtk.Button()
            b.get_style_context().add_class("widget-btn")
            b.set_relief(Gtk.ReliefStyle.NONE)
            hb = Gtk.Box(spacing=6)
            if icon:
                pb = _icon_pb(icon, 16)
                if pb:
                    hb.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
            hb.pack_start(Gtk.Label(label=name), False, False, 0)
            b.add(hb)
            b.set_tooltip_text(cmd)
            b.connect("clicked", lambda *_a, c=cmd: self._launch(c))
            box.pack_start(b, False, False, 0)
        return box

    def _single_plugin_button(self):
        if not self.cfg.get("single_plugin_enabled", False):
            return None
        cmd = (self.cfg.get("single_plugin_cmd", "") or "").strip()
        if not cmd:
            return None

        app = self._app_for_cmd(cmd)
        name = (self.cfg.get("single_plugin_name", "") or "").strip()
        icon = (self.cfg.get("single_plugin_icon", "") or "").strip()
        if not name and app:
            name = app.name
        if not icon and app:
            icon = app.icon_name
        if not name:
            name = "Plugin"

        b = Gtk.Button()
        b.get_style_context().add_class("widget-btn")
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_tooltip_text(f"{name}: {cmd}")

        pb = _icon_pb(icon or "application-x-executable", 16)
        if pb:
            b.add(Gtk.Image.new_from_pixbuf(pb))
        else:
            b.add(Gtk.Label(label=name[0:1].upper() or "P"))

        b.connect("clicked", lambda *_a: self._launch(cmd))
        return b

    def _right_zone(self):
        rz = Gtk.Box(spacing=8)
        rz.set_valign(Gtk.Align.CENTER)
        spb = self._single_plugin_button()
        if spb:
            rz.pack_start(spb, False, False, 0)
        rz.pack_start(self._custom_widgets_box(), False, False, 0)
        if self.cfg.get("show_clock", True):
            rz.pack_start(self._clock_widget(), False, False, 0)
        return rz

    def _animate_window_in(self, win):
        if not self.cfg.get("animations_enabled", True):
            return
        ms = max(0, int(self.cfg.get("animation_ms", 140)))
        if ms <= 0:
            return
        try:
            win.set_opacity(0.0)
        except Exception:
            return
        steps = 8
        interval = max(10, ms // steps)
        state = {"i": 0}

        def _tick():
            state["i"] += 1
            t = state["i"] / float(steps)
            try:
                win.set_opacity(min(1.0, t))
            except Exception:
                return False
            return state["i"] < steps

        GLib.timeout_add(interval, _tick)

    def _close_dialog_if_inactive(self, dlg):
        try:
            if not dlg.get_visible():
                return False
            if dlg.is_active():
                return False
            dlg.destroy()
        except Exception:
            return False
        return False

    def _deferred_dialog_focus_out(self, dlg, *_a):
        # Defer close to avoid killing the dialog while GTK opens combo popups.
        GLib.timeout_add(180, lambda: self._close_dialog_if_inactive(dlg))
        return False

    def _autostart_file(self):
        return Path.home() / ".config" / "autostart" / "dex-panel.desktop"

    def _set_autostart(self, enabled: bool):
        af = self._autostart_file()
        af.parent.mkdir(parents=True, exist_ok=True)
        if not enabled:
            try:
                if af.exists():
                    af.unlink()
            except Exception:
                pass
            self.cfg["autostart_enabled"] = False
            _save_cfg(self.cfg)
            return

        exe = shutil.which("dex-panel")
        if exe:
            exec_line = exe
        else:
            exec_line = f"python3 {shlex.quote(str(PROJ_DIR / 'main.py'))}"
        data = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Dex Panel\n"
            f"Exec={exec_line}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "StartupNotify=false\n"
        )
        try:
            af.write_text(data)
            self.cfg["autostart_enabled"] = True
            _save_cfg(self.cfg)
        except Exception as e:
            _log(f"autostart write failed: {e}")

    def _default_favorites(self):
        apps = DesktopApp.load_all()
        values = list(apps.values())

        def pick(predicates):
            for a in values:
                txt = f"{a.desktop_id} {a.name} {a.exec_cmd}".lower()
                if any(p in txt for p in predicates):
                    return a.desktop_id
            return None

        picks = [
            pick(["thunar", "nautilus", "nemo", "pcmanfm", "dolphin", "file manager", "explorador"]),
            pick(["xfce4-terminal", "gnome-terminal", "konsole", "xterm", "kitty", "alacritty", "terminal"]),
            pick(["xfce4-appfinder", "appfinder", "rofi", "ulauncher", "search", "buscar"]),
        ]
        out = []
        for did in picks:
            if did and did not in out:
                out.append(did)
        return out

    def _ensure_default_favorites(self):
        if self._fav:
            return
        defaults = self._default_favorites()
        if defaults:
            self._fav = defaults
            _save_fav(self._fav)

    def _menu_app_candidates(self):
        items = [
            {"name": "Sistema (App Finder)", "cmd": "xfce4-appfinder --collapsed", "icon": "system-search"},
            {"name": "Dex Menu (Personalizado)", "cmd": "dex-menu", "icon": "view-app-grid-symbolic"},
        ]
        apps = sorted(DesktopApp.load_all().values(), key=lambda a: a.name.lower())
        for a in apps:
            if not a.exec_cmd:
                continue
            items.append({"name": a.name, "cmd": a.exec_cmd, "icon": a.icon_name or "application-x-executable"})
        return items

    def _maybe_run_first_setup(self):
        if self.cfg.get("first_setup_done", False):
            return False

        dlg = Gtk.Window(title="Configuración inicial de Dex Panel")
        dlg.set_default_size(560, 360)
        dlg.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        dlg.set_transient_for(self)
        dlg.set_modal(True)
        dlg.get_style_context().add_class("prefs-win")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_start(14); outer.set_margin_end(14)
        outer.set_margin_top(14); outer.set_margin_bottom(14)
        title = Gtk.Label()
        title.set_markup("<b><big>Configuración inicial</big></b>")
        title.set_halign(Gtk.Align.START)
        outer.pack_start(title, False, False, 0)

        info = Gtk.Label(
            label="Elige si deseas inicio automático y qué app abrirá el botón de menú.",
            xalign=0.0
        )
        info.set_line_wrap(True)
        outer.pack_start(info, False, False, 0)

        g = Gtk.Grid(column_spacing=12, row_spacing=10)
        outer.pack_start(g, True, True, 0)

        g.attach(Gtk.Label(label="Iniciar con la sesión:", halign=Gtk.Align.START), 0, 0, 1, 1)
        w_auto = Gtk.Switch(); w_auto.set_halign(Gtk.Align.START)
        w_auto.set_active(self.cfg.get("autostart_enabled", False))
        g.attach(w_auto, 1, 0, 1, 1)

        g.attach(Gtk.Label(label="Aplicación del menú:", halign=Gtk.Align.START), 0, 1, 1, 1)
        model = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str)
        combo = Gtk.ComboBox.new_with_model(model)
        combo.set_hexpand(True)
        rp = Gtk.CellRendererPixbuf()
        rt = Gtk.CellRendererText()
        combo.pack_start(rp, False); combo.add_attribute(rp, "pixbuf", 0)
        combo.pack_start(rt, True);  combo.add_attribute(rt, "text", 1)

        cur_cmd = (self.cfg.get("app_menu_cmd", "") or "").strip()
        active_idx = 0
        for i, item in enumerate(self._menu_app_candidates()):
            pb = _icon_pb(item.get("icon"), 18)
            model.append([pb, item.get("name", "App"), item.get("cmd", "")])
            if cur_cmd and item.get("cmd", "").strip() == cur_cmd:
                active_idx = i
        combo.set_active(active_idx)
        g.attach(combo, 1, 1, 1, 1)

        g.attach(Gtk.Label(label="Comando personalizado (opcional):", halign=Gtk.Align.START), 0, 2, 1, 1)
        w_custom = Gtk.Entry()
        w_custom.set_placeholder_text("Ej: dex-menu  |  rofi -show drun")
        g.attach(w_custom, 1, 2, 1, 1)

        bb = Gtk.Box(spacing=8); bb.set_halign(Gtk.Align.END)
        bc = Gtk.Button(label="Cancelar")
        ba = Gtk.Button(label="Guardar e iniciar")
        bb.pack_start(bc, False, False, 0)
        bb.pack_start(ba, False, False, 0)
        outer.pack_end(bb, False, False, 0)

        bc.connect("clicked", lambda *_: dlg.destroy())

        def _apply_setup(*_a):
            self._set_autostart(w_auto.get_active())
            custom_cmd = w_custom.get_text().strip()
            if custom_cmd:
                self.cfg["app_menu_cmd"] = custom_cmd
                self.cfg["app_menu_name"] = "Personalizado"
            else:
                itr = combo.get_active_iter()
                if itr:
                    m = combo.get_model()
                    self.cfg["app_menu_name"] = m.get_value(itr, 1) or ""
                    self.cfg["app_menu_cmd"] = m.get_value(itr, 2) or ""
            self._ensure_default_favorites()
            self.cfg["first_setup_done"] = True
            self.cfg["autostart_prompted"] = True
            _save_cfg(self.cfg)
            self._full_refresh()
            dlg.destroy()

        ba.connect("clicked", _apply_setup)
        dlg.add(outer)
        dlg.show_all()
        self._animate_window_in(dlg)
        return False

    # ───────────────────────────────────────────────────────────────────
    #  FAVORITES: RUNNING INDICATORS + ACTIVATE
    # ───────────────────────────────────────────────────────────────────

    def _set_dot(self, dot, on: bool):
        if not dot:
            return
        if not self.cfg.get("dock_show_running", True):
            dot.hide()
            return
        if not on:
            dot.hide()
            return
        dot.show()
        sc = dot.get_style_context()
        sc.remove_class("active-dot"); sc.remove_class("inactive-dot")
        # Favorite indicator: dim dot when running (active window dot is handled elsewhere).
        sc.add_class("inactive-dot")

    def _wnck_window_class_names(self, w):
        out = []
        for m in ("get_class_group_name", "get_class_instance_name"):
            fn = getattr(w, m, None)
            if not fn:
                continue
            try:
                v = fn()
                if v:
                    out.append(str(v))
            except Exception:
                pass
        try:
            cg = w.get_class_group()
            if cg:
                n = cg.get_name()
                if n:
                    out.append(str(n))
        except Exception:
            pass
        return [x.strip().lower() for x in out if x and str(x).strip()]

    def _window_matches_app(self, w, app: DesktopApp) -> bool:
        if not app:
            return False
        wc = (app.wmclass or "").strip().lower()
        if wc:
            return wc in self._wnck_window_class_names(w)
        # Fallback: many apps match the desktop file id stem (e.g. "firefox").
        try:
            stem = (Path(app.desktop_id).stem or "").strip().lower()
        except Exception:
            stem = ""
        if stem and stem in self._wnck_window_class_names(w):
            return True
        # Fallback: best-effort match using app name.
        an = (app.name or "").strip().lower()
        if not an:
            return False
        try:
            wn = (w.get_name() or "").strip().lower()
            if an and an in wn:
                return True
        except Exception:
            pass
        return False

    def _activate_app(self, app: DesktopApp) -> bool:
        if not hasattr(self, "_wnck") or self._wnck is None:
            return False
        ts = int(GLib.get_monotonic_time() / 1_000_000)
        for w in self._wnck.get_windows():
            if not self._taskable(w):
                continue
            if self._window_matches_app(w, app):
                try:
                    if w.is_minimized():
                        w.unminimize(ts)
                except Exception:
                    pass
                try:
                    w.activate(ts)
                except Exception:
                    pass
                return True
        return False

    def _fav_click(self, app: DesktopApp):
        if self.cfg.get("dock_fav_click_activates", True):
            if self._activate_app(app):
                return
        self._launch(app.exec_cmd)

    def _update_fav_indicators(self):
        # Update dots for favorites in macOS dock (and keep it safe for other layouts).
        if not hasattr(self, "_wnck") or self._wnck is None:
            return
        for did, d in list(self._fav_widgets.items()):
            app = d.get("app")
            dot = d.get("dot")
            on = False
            try:
                for w in self._wnck.get_windows():
                    if self._taskable(w) and self._window_matches_app(w, app):
                        on = True
                        break
            except Exception:
                on = False
            self._set_dot(dot, on)

    # ───────────────────────────────────────────────────────────────────
    #  POSITION & STRUTS
    # ───────────────────────────────────────────────────────────────────

    def _geo(self):
        d = Gdk.Display.get_default()
        return (d.get_primary_monitor() or d.get_monitor(0)).get_geometry()

    def _position(self):
        g = self._geo(); h = self.cfg.get("panel_height",48)
        pos = self.cfg.get("panel_position","bottom")
        y = g.y + g.height - h if pos == "bottom" else g.y
        self.set_size_request(g.width, h)
        self.resize(g.width, h); self.move(g.x, y)

    def _schedule_relayout(self, *_args):
        if self._relayout_source_id:
            return False
        def _apply():
            self._relayout_source_id = 0
            try:
                self._position()
                self._set_struts()
                self._transset()
            except Exception as e:
                _log(f"relayout failed: {e}")
            return False
        self._relayout_source_id = GLib.timeout_add(90, _apply)
        return False

    def _set_struts(self):
        try:
            xid = str(self.get_window().get_xid())
            g = self._geo(); h = self.cfg.get("panel_height",48)
            pos = self.cfg.get("panel_position","bottom")
            st = (f"0, 0, 0, {h}, 0, 0, 0, 0, 0, 0, 0, {g.width}" if pos == "bottom"
                  else f"0, 0, {h}, 0, 0, 0, 0, 0, 0, {g.width}, 0, 0")
            subprocess.run(["xprop","-id",xid,"-f","_NET_WM_STRUT_PARTIAL","32c",
                            "-set","_NET_WM_STRUT_PARTIAL",st],
                           capture_output=True, timeout=3)
        except Exception: pass
        return False

    def _transset(self):
        if not shutil.which("transset"): return False
        lay = self.cfg.get("_layout","default")
        if lay == "macos": return False
        try:
            gw = self.get_window()
            if gw:
                op = self.cfg.get("bg_opacity",95) / 100
                subprocess.Popen(["transset","--id",str(gw.get_xid()),str(op)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception: pass
        return False

    # ───────────────────────────────────────────────────────────────────
    #  COMPOSITOR (picom)
    # ───────────────────────────────────────────────────────────────────

    def _compositor_running(self):
        try:
            r = subprocess.run(["pgrep","-x","picom"], capture_output=True, timeout=2)
            return r.returncode == 0
        except Exception:
            return False

    def _start_compositor(self):
        if not shutil.which("picom"): return
        if self._compositor_running(): return
        try:
            self._picom_proc = subprocess.Popen([
                "picom",
                "--backend", "xrender",
                "--no-fading-openclose",
                "--no-vsync",
                "--no-dock-shadow",
                "--shadow-exclude", "name = 'dex-panel'",
                "--shadow-exclude", "window_type = 'popup_menu'",
                "--shadow-exclude", "window_type = 'dropdown_menu'",
                "--shadow-exclude", "window_type = 'tooltip'",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self._picom_proc = None
            _log(f"failed to start picom: {e}")

    def _stop_compositor(self):
        # Only stop picom if we started it. Never kill the user's compositor.
        p = getattr(self, "_picom_proc", None)
        if not p:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except Exception:
                    p.kill()
            self._picom_proc = None
        except Exception as e:
            _log(f"failed to stop picom: {e}")

    def _sync_compositor(self):
        if self.cfg.get("compositor", True):
            self._start_compositor()
        else:
            self._stop_compositor()

    # ───────────────────────────────────────────────────────────────────
    #  WNCK – WINDOW TRACKING
    # ───────────────────────────────────────────────────────────────────

    def _taskable(self, w):
        return w.get_window_type() == Wnck.WindowType.NORMAL and not w.is_skip_tasklist()

    def _scan_windows(self):
        for w in self._wnck.get_windows():
            if self._taskable(w): self._add_task(w)
        self._mark_active()
        self._update_fav_indicators()

    def _w_open(self, s, w):
        if self._taskable(w): self._add_task(w)
        self._update_fav_indicators()

    def _w_close(self, s, w):
        d = self._tbtns.pop(w, None)
        if d:
            c = d.get("ct", d["btn"])
            p = c.get_parent()
            if p: p.remove(c)
        self._ditems = [i for i in self._ditems if i.get("win") is not w]
        self.queue_draw()
        self._update_fav_indicators()

    def _w_active(self, s, prev): self._mark_active()

    def _add_task(self, w):
        if w in self._tbtns: return
        lay = self.cfg.get("_layout","default")
        isz = self.cfg.get("icon_size",32)
        if lay == "macos": self._add_task_dock(w, isz)
        else:              self._add_task_icon(w, isz)

    def _add_task_icon(self, w, isz):
        btn = Gtk.Button()
        btn.get_style_context().add_class("task-btn")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        mini = w.get_icon()
        if mini:
            btn.set_image(Gtk.Image.new_from_pixbuf(
                mini.scale_simple(isz, isz, GdkPixbuf.InterpType.BILINEAR)))
        else:
            btn.set_image(Gtk.Image.new_from_icon_name(
                "application-x-executable", Gtk.IconSize.LARGE_TOOLBAR))
        btn.set_tooltip_text(w.get_name())
        btn.connect("clicked", self._task_click, w)
        w.connect("name-changed", lambda ww, b=btn: b.set_tooltip_text(ww.get_name()))
        self._tbtns[w] = {"btn":btn, "ct":btn}
        self._tbox.pack_start(btn, False, False, 0)
        btn.show_all()

    def _add_task_dock(self, w, isz):
        pb = w.get_icon()
        if pb: pb = pb.scale_simple(isz, isz, GdkPixbuf.InterpType.BILINEAR)
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vb.set_valign(Gtk.Align.CENTER)
        btn = Gtk.Button()
        btn.get_style_context().add_class("dock-item")
        btn.set_relief(Gtk.ReliefStyle.NONE); btn.set_tooltip_text(w.get_name())
        img = Gtk.Image()
        if pb: img.set_from_pixbuf(pb)
        else:  img.set_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        btn.set_image(img); btn.set_size_request(isz+8, isz+8)
        btn.connect("clicked", self._task_click, w)
        dot = Gtk.Box(); dot.set_size_request(5,5); dot.set_halign(Gtk.Align.CENTER)
        if not self.cfg.get("dock_show_running", True):
            dot.hide()
        else:
            dot.get_style_context().add_class("active-dot" if w.is_active() else "inactive-dot")
        vb.pack_start(dot, False, False, 0)
        vb.pack_start(btn, False, False, 0)
        self._tbox.pack_start(vb, False, False, 0)
        vb.show_all()
        self._tbtns[w] = {"btn":btn, "ct":vb, "dot":dot}
        if pb:
            self._ditems.append({"btn":btn,"img":img,"pb":pb,"sz":isz,"vb":vb,"task":True,"win":w})
        w.connect("name-changed", lambda ww, b=btn: b.set_tooltip_text(ww.get_name()))
        self.queue_draw()

    def _mark_active(self):
        act = self._wnck.get_active_window()
        lay = self.cfg.get("_layout","default")
        for w, d in self._tbtns.items():
            ia = (w == act)
            if lay == "macos":
                dot = d.get("dot")
                if dot:
                    if not self.cfg.get("dock_show_running", True):
                        dot.hide()
                    else:
                        dot.show()
                        sc = dot.get_style_context()
                        sc.remove_class("active-dot"); sc.remove_class("inactive-dot")
                        sc.add_class("active-dot" if ia else "inactive-dot")
            else:
                sc = d["btn"].get_style_context()
                if ia: sc.add_class("task-active")
                else:  sc.remove_class("task-active")
        self._update_fav_indicators()

    def _task_click(self, btn, w):
        if w.is_active(): w.minimize()
        else: w.activate(int(GLib.get_monotonic_time()/1000000))

    def _start_click(self, btn):
        cmd = self.cfg.get("app_menu_cmd","").strip()
        if cmd: self._launch(cmd)

    # ───────────────────────────────────────────────────────────────────
    #  CLOCK
    # ───────────────────────────────────────────────────────────────────

    def _tick_clock(self):
        if self._clock_lbl:
            tz_name = (self.cfg.get("clock_timezone", "") or "").strip()
            old_tz = os.environ.get("TZ")
            if tz_name:
                os.environ["TZ"] = tz_name
                try:
                    time.tzset()
                except Exception:
                    pass
            now = time.localtime()
            fmt = self.cfg.get("clock_format","%H:%M")
            if self.cfg.get("clock_show_seconds", False) and "%S" not in fmt:
                fmt = f"{fmt}:%S"
            self._clock_lbl.set_text(time.strftime(fmt, now))
            if self.cfg.get("clock_show_date", True):
                self._date_lbl.set_text(time.strftime(self.cfg.get("clock_date_format","%d/%m/%Y"), now))
                self._date_lbl.show()
            else:
                self._date_lbl.hide()
            if tz_name:
                if old_tz is None:
                    os.environ.pop("TZ", None)
                else:
                    os.environ["TZ"] = old_tz
                try:
                    time.tzset()
                except Exception:
                    pass
        return True

    def _launch(self, cmd):
        cmd = (cmd or "").strip()
        if not cmd:
            return
        # Prefer shell=False for safety; fall back to shell=True if parsing fails.
        try:
            argv = shlex.split(cmd)
            if not argv:
                return
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            _log(f"launch failed (shell=False): {e}; falling back to shell=True")
            try:
                subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e2:
                _log(f"launch failed (shell=True): {e2}")

    # ───────────────────────────────────────────────────────────────────
    #  CONTEXT MENU
    # ───────────────────────────────────────────────────────────────────

    def _on_rclick(self, w, ev):
        if ev.button == 3:
            self._popup_menu(ev)
            return True
        return False

    def _popup_menu(self, ev):
        if self._popup: self._popup.destroy()
        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        win.set_keep_above(True); win.set_decorated(False); win.stick()
        win.set_resizable(False)
        win.set_accept_focus(True)
        win.add_events(Gdk.EventMask.KEY_PRESS_MASK)
        win.connect("destroy", lambda *_: setattr(self, "_popup", None))
        scr = win.get_screen(); vis = scr.get_rgba_visual()
        if vis: win.set_visual(vis); win.set_app_paintable(True)
        win.connect("draw", lambda w,cr:(cr.set_source_rgba(0,0,0,0),cr.set_operator(0),
                                         cr.paint(),cr.set_operator(2),False)[-1])
        # Auto-close on focus loss / escape.
        win.connect("focus-out-event", lambda *a: (win.destroy(), False)[-1])
        win.connect(
            "key-press-event",
            lambda w, e: (win.destroy(), True)[-1] if e.keyval == Gdk.KEY_Escape else False,
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("popup-menu")

        def it(label, icon, cb):
            b = _menu_btn(label, icon, lambda: (win.destroy(), cb()))
            box.pack_start(b, False, False, 0)
        def sp():
            s = Gtk.Box(); s.get_style_context().add_class("popup-sep")
            s.set_size_request(-1,1); box.pack_start(s, False, False, 0)

        it("Preferencias", "dex-settings", self._dlg_prefs)
        it("Temas", "dex-themes", self._dlg_themes)
        it("Editar favoritos", "dex-favorites", self._dlg_fav)
        sp()
        it("Cerrar panel", "dex-close", lambda: (self.destroy(), Gtk.main_quit()))

        win.add(box); win.show_all()
        win.grab_focus()

        # Position safely on-screen (top panel must open downward, bottom upward).
        wr, hr = win.get_size()
        try:
            disp = Gdk.Display.get_default()
            mon = None
            if disp:
                try:
                    mon = disp.get_monitor_at_point(int(ev.x_root), int(ev.y_root))
                except Exception:
                    mon = disp.get_primary_monitor() or disp.get_monitor(0)
            g = mon.get_geometry() if mon else self._geo()
        except Exception:
            g = self._geo()

        pos = self.cfg.get("panel_position", "bottom")
        x = int(ev.x_root)
        if pos == "top":
            y = int(ev.y_root) + 2
        else:
            y = int(ev.y_root) - hr - 2

        # Clamp into monitor bounds.
        x = max(g.x, min(x, g.x + g.width - wr))
        y = max(g.y, min(y, g.y + g.height - hr))
        win.move(x, y)
        self._popup = win
        self._animate_window_in(win)

    # ───────────────────────────────────────────────────────────────────
    #  PREFERENCES DIALOG
    # ───────────────────────────────────────────────────────────────────

    def _dlg_prefs(self):
        dlg = Gtk.Window(title="Preferencias del panel")
        dlg.set_default_size(520, 520)
        dlg.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        dlg.set_transient_for(self)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.set_modal(False)
        dlg.get_style_context().add_class("prefs-win")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_start(14); outer.set_margin_end(14)
        outer.set_margin_top(14); outer.set_margin_bottom(14)

        tl = Gtk.Label()
        tl.set_markup("<b><big>Preferencias</big></b>")
        tl.set_halign(Gtk.Align.START)
        outer.pack_start(tl, False, False, 0)
        self._animate_window_in(dlg)

        nb = Gtk.Notebook()
        nb.set_scrollable(True)

        # ── TAB: Apariencia ──
        g1 = Gtk.Grid(column_spacing=16, row_spacing=10)
        g1.set_margin_start(14); g1.set_margin_end(14)
        g1.set_margin_top(14); g1.set_margin_bottom(14)
        r = 0

        def lbl(g, t, row):
            g.attach(Gtk.Label(label=t, halign=Gtk.Align.START), 0, row, 1, 1)

        lbl(g1, "Mostrar reloj:", r)
        w_sc = Gtk.Switch(); w_sc.set_halign(Gtk.Align.START)
        w_sc.set_active(self.cfg.get("show_clock", True))
        g1.attach(w_sc, 1, r, 1, 1); r += 1

        lbl(g1, "Mostrar tareas:", r)
        w_st = Gtk.Switch(); w_st.set_halign(Gtk.Align.START)
        w_st.set_active(self.cfg.get("show_taskbar", True))
        g1.attach(w_st, 1, r, 1, 1); r += 1

        lbl(g1,"Posición:", r)
        w_pos = Gtk.ComboBoxText()
        w_pos.append("bottom","Abajo"); w_pos.append("top","Arriba")
        w_pos.set_active_id(self.cfg.get("panel_position","bottom"))
        w_pos.set_hexpand(True)
        g1.attach(w_pos, 1, r, 1, 1); r += 1

        lbl(g1,"Altura:", r)
        w_h = Gtk.SpinButton.new_with_range(28,100,2)
        w_h.set_value(self.cfg.get("panel_height",48))
        g1.attach(w_h, 1, r, 1, 1); r += 1

        lbl(g1,"Tamaño iconos:", r)
        w_isz = Gtk.SpinButton.new_with_range(16,72,4)
        w_isz.set_value(self.cfg.get("icon_size",32))
        g1.attach(w_isz, 1, r, 1, 1); r += 1

        lbl(g1,"Color de fondo:", r)
        w_col = Gtk.ColorButton()
        rgba = Gdk.RGBA(); rgba.parse(self.cfg.get("bg_color","#141418"))
        w_col.set_rgba(rgba)
        g1.attach(w_col, 1, r, 1, 1); r += 1

        lbl(g1,"Opacidad (%):", r)
        w_op = Gtk.SpinButton.new_with_range(10,100,5)
        w_op.set_value(self.cfg.get("bg_opacity",95))
        g1.attach(w_op, 1, r, 1, 1); r += 1

        lbl(g1,"Compositor (transparencia):", r)
        w_comp = Gtk.Switch(); w_comp.set_halign(Gtk.Align.START)
        w_comp.set_active(self.cfg.get("compositor", True))
        w_comp.set_tooltip_text("Activa picom para transparencia real")
        g1.attach(w_comp, 1, r, 1, 1); r += 1

        lbl(g1,"Animaciones:", r)
        w_anim = Gtk.Switch(); w_anim.set_halign(Gtk.Align.START)
        w_anim.set_active(self.cfg.get("animations_enabled", True))
        g1.attach(w_anim, 1, r, 1, 1); r += 1

        lbl(g1, "Duración animación (ms):", r)
        w_anim_ms = Gtk.SpinButton.new_with_range(0, 500, 10)
        w_anim_ms.set_value(self.cfg.get("animation_ms", 140))
        g1.attach(w_anim_ms, 1, r, 1, 1); r += 1

        nb.append_page(g1, Gtk.Label(label="Apariencia"))

        # ── TAB: Reloj ──
        g2 = Gtk.Grid(column_spacing=16, row_spacing=10)
        g2.set_margin_start(14); g2.set_margin_end(14)
        g2.set_margin_top(14); g2.set_margin_bottom(14)
        r = 0

        lbl(g2,"Formato reloj:", r)
        w_cf = Gtk.Entry(); w_cf.set_text(self.cfg.get("clock_format","%H:%M"))
        w_cf.set_tooltip_text("%H:%M = 24h · %I:%M %p = 12h")
        g2.attach(w_cf, 1, r, 1, 1); r += 1

        lbl(g2,"Mostrar fecha:", r)
        w_sd = Gtk.Switch(); w_sd.set_halign(Gtk.Align.START)
        w_sd.set_active(self.cfg.get("clock_show_date",True))
        g2.attach(w_sd, 1, r, 1, 1); r += 1

        lbl(g2,"Formato fecha:", r)
        w_df = Gtk.Entry(); w_df.set_text(self.cfg.get("clock_date_format","%d/%m/%Y"))
        g2.attach(w_df, 1, r, 1, 1); r += 1

        lbl(g2, "Mostrar segundos:", r)
        w_sec = Gtk.Switch(); w_sec.set_halign(Gtk.Align.START)
        w_sec.set_active(self.cfg.get("clock_show_seconds", False))
        g2.attach(w_sec, 1, r, 1, 1); r += 1

        lbl(g2, "Zona horaria (TZ):", r)
        w_tz = Gtk.Entry(); w_tz.set_text(self.cfg.get("clock_timezone", ""))
        w_tz.set_placeholder_text("Ej: America/Santo_Domingo")
        g2.attach(w_tz, 1, r, 1, 1); r += 1

        lbl(g2, "Tamaño fuente reloj (0=auto):", r)
        w_cfs = Gtk.SpinButton.new_with_range(0, 36, 1)
        w_cfs.set_value(self.cfg.get("clock_font_size", 0))
        g2.attach(w_cfs, 1, r, 1, 1); r += 1

        lbl(g2, "Color reloj:", r)
        w_clk_col = Gtk.ColorButton()
        rgba_clk = Gdk.RGBA(); rgba_clk.parse(self.cfg.get("clock_color", "#e0e0e0"))
        w_clk_col.set_rgba(rgba_clk)
        g2.attach(w_clk_col, 1, r, 1, 1); r += 1

        lbl(g2, "Color fecha:", r)
        w_dt_col = Gtk.ColorButton()
        rgba_dt = Gdk.RGBA(); rgba_dt.parse(self.cfg.get("clock_date_color", "#999999"))
        w_dt_col.set_rgba(rgba_dt)
        g2.attach(w_dt_col, 1, r, 1, 1); r += 1

        nb.append_page(g2, Gtk.Label(label="Reloj"))

        # ── TAB: Avanzado ──
        g3 = Gtk.Grid(column_spacing=16, row_spacing=10)
        g3.set_margin_start(14); g3.set_margin_end(14)
        g3.set_margin_top(14); g3.set_margin_bottom(14)
        r = 0

        lbl(g3,"App del botón Menú:", r)
        w_mc_model = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str)  # pixbuf, label, cmd
        w_mc = Gtk.ComboBox.new_with_model(w_mc_model)
        w_mc.set_hexpand(True)
        rp = Gtk.CellRendererPixbuf()
        rt = Gtk.CellRendererText()
        w_mc.pack_start(rp, False)
        w_mc.add_attribute(rp, "pixbuf", 0)
        w_mc.pack_start(rt, True)
        w_mc.add_attribute(rt, "text", 1)

        w_mc_model.append([None, "— Ninguna —", ""])
        all_apps = DesktopApp.load_all()
        sorted_apps = sorted(all_apps.values(), key=lambda a: a.name.lower())
        current_cmd = self.cfg.get("app_menu_cmd","")
        active_idx = 0
        for i, app in enumerate(sorted_apps, start=1):
            pb = _icon_pb(app.icon_name, 18)
            w_mc_model.append([pb, app.name, app.exec_cmd])
            if app.exec_cmd == current_cmd and current_cmd:
                active_idx = i
        w_mc.set_active(active_idx)
        g3.attach(w_mc, 1, r, 1, 1); r += 1

        lbl(g3,"Magnificación (macOS):", r)
        w_mg = Gtk.SpinButton.new_with_range(0,96,4)
        w_mg.set_value(self.cfg.get("magnify_size",68))
        w_mg.set_tooltip_text("Tamaño max icono al pasar cursor")
        g3.attach(w_mg, 1, r, 1, 1); r += 1

        lbl(g3,"Radio magnif. (px):", r)
        w_mr = Gtk.SpinButton.new_with_range(50,300,10)
        w_mr.set_value(self.cfg.get("magnify_radius",130))
        g3.attach(w_mr, 1, r, 1, 1); r += 1

        lbl(g3, "Puntos apps abiertas (macOS):", r)
        w_dot = Gtk.Switch(); w_dot.set_halign(Gtk.Align.START)
        w_dot.set_active(self.cfg.get("dock_show_running", True))
        g3.attach(w_dot, 1, r, 1, 1); r += 1

        lbl(g3, "Click favorito activa app:", r)
        w_act = Gtk.Switch(); w_act.set_halign(Gtk.Align.START)
        w_act.set_active(self.cfg.get("dock_fav_click_activates", True))
        w_act.set_tooltip_text("Si hay una ventana abierta, enfoca la app en vez de abrir otra")
        g3.attach(w_act, 1, r, 1, 1); r += 1

        lbl(g3, "Dock padding (px):", r)
        w_dpad = Gtk.SpinButton.new_with_range(0, 50, 1)
        w_dpad.set_value(self.cfg.get("dock_bg_padding", 14))
        w_dpad.set_tooltip_text("Padding del recuadro del dock (solo macOS)")
        g3.attach(w_dpad, 1, r, 1, 1); r += 1

        lbl(g3, "Dock radio (px):", r)
        w_drad = Gtk.SpinButton.new_with_range(0, 50, 1)
        w_drad.set_value(self.cfg.get("dock_bg_radius", 18))
        w_drad.set_tooltip_text("Radio de esquinas del recuadro (solo macOS)")
        g3.attach(w_drad, 1, r, 1, 1); r += 1

        lbl(g3, "Dock offset Y (px):", r)
        w_dy = Gtk.SpinButton.new_with_range(-50, 50, 1)
        w_dy.set_value(self.cfg.get("dock_bg_y_offset", -7))
        w_dy.set_tooltip_text("Sube/baja el recuadro del dock (solo macOS)")
        g3.attach(w_dy, 1, r, 1, 1); r += 1

        lbl(g3, "Iniciar con sesión:", r)
        w_auto = Gtk.Switch(); w_auto.set_halign(Gtk.Align.START)
        w_auto.set_active(self.cfg.get("autostart_enabled", False))
        g3.attach(w_auto, 1, r, 1, 1); r += 1

        nb.append_page(g3, Gtk.Label(label="Avanzado"))

        # ── TAB: Widgets ──
        g4 = Gtk.Grid(column_spacing=12, row_spacing=8)
        g4.set_margin_start(14); g4.set_margin_end(14)
        g4.set_margin_top(14); g4.set_margin_bottom(14)
        rr = 0
        lbl(g4, "Nombre widget:", rr)
        w_wname = Gtk.Entry(); w_wname.set_placeholder_text("Ej: Notas")
        g4.attach(w_wname, 1, rr, 1, 1); rr += 1

        lbl(g4, "Comando:", rr)
        w_wcmd = Gtk.Entry(); w_wcmd.set_placeholder_text("Ej: mousepad")
        g4.attach(w_wcmd, 1, rr, 1, 1); rr += 1

        lbl(g4, "Icono (opcional):", rr)
        w_wicon = Gtk.Entry(); w_wicon.set_placeholder_text("Ej: accessories-text-editor")
        g4.attach(w_wicon, 1, rr, 1, 1); rr += 1

        wlist = Gtk.ListStore(str, str, str)
        for it in (self.cfg.get("custom_widgets", []) or []):
            if isinstance(it, dict):
                wlist.append([str(it.get("name", "")), str(it.get("command", "")), str(it.get("icon", ""))])
        tv = Gtk.TreeView(model=wlist)
        for i, title in enumerate(("Nombre", "Comando", "Icono")):
            rd = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, rd, text=i)
            tv.append_column(col)
        scr_w = Gtk.ScrolledWindow()
        scr_w.set_vexpand(True)
        scr_w.add(tv)
        g4.attach(scr_w, 0, rr, 2, 1); rr += 1

        hb_w = Gtk.Box(spacing=8)
        b_add = Gtk.Button(label="Agregar widget")
        b_rm = Gtk.Button(label="Quitar seleccionado")
        hb_w.pack_start(b_add, False, False, 0)
        hb_w.pack_start(b_rm, False, False, 0)
        g4.attach(hb_w, 0, rr, 2, 1); rr += 1

        def _w_add(*_a):
            n = w_wname.get_text().strip() or "Widget"
            c = w_wcmd.get_text().strip()
            i = w_wicon.get_text().strip()
            if not c:
                return
            wlist.append([n, c, i])
            w_wname.set_text("")
            w_wcmd.set_text("")
            w_wicon.set_text("")

        def _w_rm(*_a):
            sel = tv.get_selection()
            model, itr = sel.get_selected()
            if itr:
                model.remove(itr)

        b_add.connect("clicked", _w_add)
        b_rm.connect("clicked", _w_rm)
        nb.append_page(g4, Gtk.Label(label="Widgets"))

        # ── TAB: Plugin (app única) ──
        g5 = Gtk.Grid(column_spacing=12, row_spacing=8)
        g5.set_margin_start(14); g5.set_margin_end(14)
        g5.set_margin_top(14); g5.set_margin_bottom(14)
        rp = 0

        lbl(g5, "Activar plugin:", rp)
        w_pl_en = Gtk.Switch(); w_pl_en.set_halign(Gtk.Align.START)
        w_pl_en.set_active(self.cfg.get("single_plugin_enabled", False))
        g5.attach(w_pl_en, 1, rp, 1, 1); rp += 1

        lbl(g5, "App del plugin:", rp)
        w_pl_model = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str)  # pixbuf, label, cmd
        w_pl = Gtk.ComboBox.new_with_model(w_pl_model)
        w_pl.set_hexpand(True)
        rpp = Gtk.CellRendererPixbuf()
        rpt = Gtk.CellRendererText()
        w_pl.pack_start(rpp, False)
        w_pl.add_attribute(rpp, "pixbuf", 0)
        w_pl.pack_start(rpt, True)
        w_pl.add_attribute(rpt, "text", 1)

        w_pl_model.append([None, "— Ninguna —", ""])
        current_pl_cmd = (self.cfg.get("single_plugin_cmd", "") or "").strip()
        active_pl_idx = 0
        for i, app in enumerate(sorted_apps, start=1):
            if not app.exec_cmd:
                continue
            pb = _icon_pb(app.icon_name, 18)
            w_pl_model.append([pb, app.name, app.exec_cmd])
            if current_pl_cmd and app.exec_cmd == current_pl_cmd:
                active_pl_idx = i
        w_pl.set_active(active_pl_idx)
        g5.attach(w_pl, 1, rp, 1, 1); rp += 1

        lbl(g5, "Comando custom:", rp)
        w_pl_custom = Gtk.Entry()
        w_pl_custom.set_placeholder_text("Ej: code")
        g5.attach(w_pl_custom, 1, rp, 1, 1); rp += 1

        lbl(g5, "Nombre botón:", rp)
        w_pl_name = Gtk.Entry()
        w_pl_name.set_placeholder_text("Opcional (si vacío usa nombre de app)")
        w_pl_name.set_text(self.cfg.get("single_plugin_name", ""))
        g5.attach(w_pl_name, 1, rp, 1, 1); rp += 1

        lbl(g5, "Icono botón:", rp)
        w_pl_icon = Gtk.Entry()
        w_pl_icon.set_placeholder_text("Ej: accessories-text-editor")
        w_pl_icon.set_text(self.cfg.get("single_plugin_icon", ""))
        g5.attach(w_pl_icon, 1, rp, 1, 1); rp += 1

        info_pl = Gtk.Label(
            label="Este plugin agrega un botón pequeño que abre una sola app.",
            xalign=0.0
        )
        info_pl.set_line_wrap(True)
        g5.attach(info_pl, 0, rp, 2, 1); rp += 1
        nb.append_page(g5, Gtk.Label(label="Plugin"))

        # ── Buttons ──
        bb = Gtk.Box(spacing=8); bb.set_halign(Gtk.Align.END)
        bb.set_margin_top(12); bb.set_margin_bottom(12); bb.set_margin_end(16)

        br = Gtk.Button(label="Restablecer")
        br.set_tooltip_text("Vuelve a los valores por defecto de esta ventana (no guarda hasta Aplicar)")
        bb.pack_start(br, False, False, 0)

        bc = Gtk.Button(label="Cancelar")
        bc.connect("clicked", lambda b: dlg.destroy())
        bb.pack_start(bc, False, False, 0)

        def reset_widgets(b):
            # Only reset what this dialog controls.
            w_sc.set_active(DEFAULTS.get("show_clock", True))
            w_st.set_active(DEFAULTS.get("show_taskbar", True))
            w_pos.set_active_id(DEFAULTS.get("panel_position", "bottom"))
            w_h.set_value(DEFAULTS.get("panel_height", 48))
            w_isz.set_value(DEFAULTS.get("icon_size", 32))
            rgba = Gdk.RGBA(); rgba.parse(DEFAULTS.get("bg_color", "#141418"))
            w_col.set_rgba(rgba)
            w_op.set_value(DEFAULTS.get("bg_opacity", 95))
            w_comp.set_active(DEFAULTS.get("compositor", True))
            w_anim.set_active(DEFAULTS.get("animations_enabled", True))
            w_anim_ms.set_value(DEFAULTS.get("animation_ms", 140))
            w_cf.set_text(DEFAULTS.get("clock_format", "%H:%M"))
            w_sd.set_active(DEFAULTS.get("clock_show_date", True))
            w_df.set_text(DEFAULTS.get("clock_date_format", "%d/%m/%Y"))
            w_sec.set_active(DEFAULTS.get("clock_show_seconds", False))
            w_tz.set_text(DEFAULTS.get("clock_timezone", ""))
            w_cfs.set_value(DEFAULTS.get("clock_font_size", 0))
            rgba_clk = Gdk.RGBA(); rgba_clk.parse(DEFAULTS.get("clock_color", "#e0e0e0"))
            w_clk_col.set_rgba(rgba_clk)
            rgba_dt = Gdk.RGBA(); rgba_dt.parse(DEFAULTS.get("clock_date_color", "#999999"))
            w_dt_col.set_rgba(rgba_dt)
            w_mg.set_value(DEFAULTS.get("magnify_size", 68))
            w_mr.set_value(DEFAULTS.get("magnify_radius", 130))
            w_dot.set_active(DEFAULTS.get("dock_show_running", True))
            w_act.set_active(DEFAULTS.get("dock_fav_click_activates", True))
            w_dpad.set_value(DEFAULTS.get("dock_bg_padding", 14))
            w_drad.set_value(DEFAULTS.get("dock_bg_radius", 18))
            w_dy.set_value(DEFAULTS.get("dock_bg_y_offset", -7))
            w_auto.set_active(DEFAULTS.get("autostart_enabled", False))
            w_pl_en.set_active(DEFAULTS.get("single_plugin_enabled", False))
            w_pl_custom.set_text("")
            w_pl_name.set_text(DEFAULTS.get("single_plugin_name", "Plugin"))
            w_pl_icon.set_text(DEFAULTS.get("single_plugin_icon", ""))
            w_pl.set_active(0)

        br.connect("clicked", reset_widgets)

        def apply_prefs(b):
            rv = w_col.get_rgba()
            rv_clk = w_clk_col.get_rgba()
            rv_dt = w_dt_col.get_rgba()
            self.cfg["show_clock"]       = w_sc.get_active()
            self.cfg["show_taskbar"]     = w_st.get_active()
            self.cfg["panel_position"]   = w_pos.get_active_id()
            self.cfg["panel_height"]     = int(w_h.get_value())
            self.cfg["icon_size"]        = int(w_isz.get_value())
            self.cfg["bg_color"]         = "#{:02x}{:02x}{:02x}".format(
                int(rv.red*255), int(rv.green*255), int(rv.blue*255))
            self.cfg["bg_opacity"]       = int(w_op.get_value())
            self.cfg["compositor"]       = w_comp.get_active()
            self.cfg["animations_enabled"] = w_anim.get_active()
            self.cfg["animation_ms"]       = int(w_anim_ms.get_value())
            self.cfg["clock_format"]     = w_cf.get_text()
            self.cfg["clock_show_date"]  = w_sd.get_active()
            self.cfg["clock_date_format"]= w_df.get_text()
            self.cfg["clock_show_seconds"] = w_sec.get_active()
            self.cfg["clock_timezone"]     = w_tz.get_text().strip()
            self.cfg["clock_font_size"]    = int(w_cfs.get_value())
            self.cfg["clock_color"]        = "#{:02x}{:02x}{:02x}".format(
                int(rv_clk.red*255), int(rv_clk.green*255), int(rv_clk.blue*255))
            self.cfg["clock_date_color"]   = "#{:02x}{:02x}{:02x}".format(
                int(rv_dt.red*255), int(rv_dt.green*255), int(rv_dt.blue*255))
            itr = w_mc.get_active_iter()
            sel_cmd = ""
            sel_txt = ""
            if itr:
                model = w_mc.get_model()
                sel_txt = model.get_value(itr, 1) or ""
                sel_cmd = model.get_value(itr, 2) or ""
            self.cfg["app_menu_cmd"]     = sel_cmd
            self.cfg["app_menu_name"]    = sel_txt if sel_cmd else ""
            self.cfg["magnify_size"]     = int(w_mg.get_value())
            self.cfg["magnify_radius"]   = int(w_mr.get_value())
            self.cfg["dock_show_running"] = w_dot.get_active()
            self.cfg["dock_fav_click_activates"] = w_act.get_active()
            self.cfg["dock_bg_padding"]  = int(w_dpad.get_value())
            self.cfg["dock_bg_radius"]   = int(w_drad.get_value())
            self.cfg["dock_bg_y_offset"] = int(w_dy.get_value())
            self.cfg["custom_widgets"] = [
                {"name": r[0], "command": r[1], "icon": r[2]}
                for r in wlist
                if str(r[1]).strip()
            ]

            itr_pl = w_pl.get_active_iter()
            sel_pl_cmd = ""
            sel_pl_name = ""
            if itr_pl:
                model_pl = w_pl.get_model()
                sel_pl_name = model_pl.get_value(itr_pl, 1) or ""
                sel_pl_cmd = model_pl.get_value(itr_pl, 2) or ""
            custom_pl_cmd = w_pl_custom.get_text().strip()
            final_pl_cmd = custom_pl_cmd if custom_pl_cmd else sel_pl_cmd
            final_pl_name = (w_pl_name.get_text() or "").strip()
            if not final_pl_name and sel_pl_name and final_pl_cmd:
                final_pl_name = sel_pl_name

            self.cfg["single_plugin_enabled"] = bool(w_pl_en.get_active() and final_pl_cmd)
            self.cfg["single_plugin_cmd"] = final_pl_cmd
            self.cfg["single_plugin_name"] = final_pl_name
            self.cfg["single_plugin_icon"] = (w_pl_icon.get_text() or "").strip()

            self._set_autostart(w_auto.get_active())
            _save_cfg(self.cfg)
            self._sync_compositor()
            self._full_refresh()
            dlg.destroy()

        ba = Gtk.Button(label="Aplicar")
        ba.connect("clicked", apply_prefs)
        bb.pack_start(ba, False, False, 0)

        outer.pack_start(nb, True, True, 0)
        outer.pack_start(bb, False, False, 0)
        dlg.add(outer); dlg.show_all(); self._animate_window_in(dlg)

    # ───────────────────────────────────────────────────────────────────
    #  THEME SELECTOR DIALOG
    # ───────────────────────────────────────────────────────────────────

    def _dlg_themes(self):
        dlg = Gtk.Window(title="Temas")
        dlg.set_default_size(420, 420)
        dlg.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        dlg.set_transient_for(self)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.set_modal(True)
        dlg.get_style_context().add_class("prefs-win")
        dlg.add_events(Gdk.EventMask.FOCUS_CHANGE_MASK)
        dlg.connect("focus-out-event", lambda *_a: (dlg.destroy(), False)[-1])

        themes  = self._themes.list_themes()
        current = self.cfg.get("theme","default")

        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vb.set_margin_start(16); vb.set_margin_end(16)
        vb.set_margin_top(16); vb.set_margin_bottom(16)

        tl = Gtk.Label(); tl.set_markup("<b><big>Seleccionar tema</big></b>")
        vb.pack_start(tl, False, False, 4)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        lb = Gtk.ListBox(); lb.set_selection_mode(Gtk.SelectionMode.SINGLE)
        sc.add(lb)

        for tid, data in themes.items():
            row = Gtk.ListBoxRow()
            hb = Gtk.Box(spacing=12)
            hb.set_margin_start(12); hb.set_margin_end(12)
            hb.set_margin_top(10); hb.set_margin_bottom(10)
            tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            nl = Gtk.Label(halign=Gtk.Align.START)
            mk = " ✓" if tid == current else ""
            nl.set_markup(f"<b>{data.get('name',tid)}{mk}</b>")
            tb.pack_start(nl, False, False, 0)
            dl = Gtk.Label(label=data.get("description",""), halign=Gtk.Align.START)
            dl.get_style_context().add_class("dim-label")
            tb.pack_start(dl, False, False, 0)
            hb.pack_start(tb, True, True, 0)
            row.add(hb); row._tid = tid
            lb.add(row)
            if tid == current: lb.select_row(row)

        vb.pack_start(sc, True, True, 0)

        bb = Gtk.Box(spacing=8); bb.set_halign(Gtk.Align.END)

        bi = _menu_btn(
            "Importar ZIP",
            "dex-import",
            lambda: self._import_zip(dlg),
            css_class=None,
            relief=Gtk.ReliefStyle.NORMAL,
        )
        bb.pack_start(bi, False, False, 0)

        def apply_theme(b):
            sel = lb.get_selected_row()
            if sel:
                self.cfg["theme"] = sel._tid
                self.cfg.pop("theme_applied", None)  # force theme defaults to apply once
                _save_cfg(self.cfg)
                self._merge_theme(force=True)
                _save_cfg(self.cfg)
                self._full_refresh()
            dlg.destroy()

        ba = _menu_btn("Aplicar", "dex-apply", lambda: apply_theme(None), css_class=None, relief=Gtk.ReliefStyle.NORMAL)
        bb.pack_start(ba, False, False, 0)

        bx = _menu_btn("Cerrar", "dex-close", lambda: dlg.destroy(), css_class=None, relief=Gtk.ReliefStyle.NORMAL)
        bb.pack_start(bx, False, False, 0)

        vb.pack_start(bb, False, False, 0)
        dlg.add(vb); dlg.show_all(); self._animate_window_in(dlg)

    def _import_zip(self, parent):
        fc = Gtk.FileChooserDialog(title="Importar tema ZIP", parent=parent,
                                   action=Gtk.FileChooserAction.OPEN)
        fc.add_buttons("Cancelar", Gtk.ResponseType.CANCEL,
                       "Importar", Gtk.ResponseType.OK)
        ff = Gtk.FileFilter(); ff.set_name("ZIP"); ff.add_pattern("*.zip")
        fc.add_filter(ff)
        if fc.run() == Gtk.ResponseType.OK:
            tid = self._themes.import_zip(fc.get_filename())
            if tid:
                parent.destroy()
                self._dlg_themes()
        fc.destroy()

    # ───────────────────────────────────────────────────────────────────
    #  FAVORITES EDITOR
    # ───────────────────────────────────────────────────────────────────

    def _dlg_fav(self):
        dlg = Gtk.Window(title="Editar favoritos")
        dlg.set_default_size(400, 500)
        dlg.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        dlg.set_transient_for(self)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.get_style_context().add_class("prefs-win")
        dlg.add_events(Gdk.EventMask.FOCUS_CHANGE_MASK)
        dlg.connect("focus-out-event", lambda *_a: (dlg.destroy(), False)[-1])

        apps = DesktopApp.load_all()
        srt  = sorted(apps.values(), key=lambda a: a.name.lower())

        se = Gtk.SearchEntry(); se.set_placeholder_text("Buscar aplicación…")
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        lb = Gtk.ListBox(); lb.set_selection_mode(Gtk.SelectionMode.NONE)
        sw.add(lb)

        rows = []
        for app in srt:
            row = Gtk.ListBoxRow()
            hb = Gtk.Box(spacing=10)
            hb.set_margin_start(8); hb.set_margin_end(8)
            hb.set_margin_top(4); hb.set_margin_bottom(4)
            pb = _icon_pb(app.icon_name, 24)
            hb.pack_start(Gtk.Image.new_from_pixbuf(pb) if pb else
                          Gtk.Image.new_from_icon_name("application-x-executable",
                                                       Gtk.IconSize.LARGE_TOOLBAR),
                          False, False, 0)
            hb.pack_start(Gtk.Label(label=app.name, halign=Gtk.Align.START), True, True, 0)
            s = Gtk.Switch(); s.set_active(app.desktop_id in self._fav)
            s.connect("notify::active", self._fav_toggle, app.desktop_id)
            hb.pack_end(s, False, False, 0)
            row.add(hb); row._sn = app.name.lower()
            lb.add(row); rows.append(row)

        se.connect("changed", lambda e: [
            rr.set_visible(not e.get_text() or e.get_text().lower() in rr._sn)
            for rr in rows])

        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vb.set_margin_start(8); vb.set_margin_end(8)
        vb.set_margin_top(8); vb.set_margin_bottom(8)
        vb.pack_start(se, False, False, 0)
        vb.pack_start(sw, True, True, 0)
        bc = Gtk.Button(label="Cerrar"); bc.set_halign(Gtk.Align.END)
        bc.connect("clicked", lambda b: dlg.destroy())
        vb.pack_start(bc, False, False, 0)
        dlg.add(vb); dlg.show_all(); self._animate_window_in(dlg)

    def _fav_toggle(self, sw, ps, did):
        if sw.get_active():
            if did not in self._fav: self._fav.append(did)
        else:
            if did in self._fav: self._fav.remove(did)
        _save_fav(self._fav)
        self._full_refresh()

    # ───────────────────────────────────────────────────────────────────
    #  FULL REFRESH
    # ───────────────────────────────────────────────────────────────────

    def _full_refresh(self):
        self._merge_theme()
        self._refresh_css()
        self._build()
        self._position()
        self.show_all()
        self._scan_windows()
        self._tick_clock()
        GLib.timeout_add(300, self._set_struts)
        GLib.timeout_add(400, self._transset)
        GLib.timeout_add(500, lambda: (self._sync_compositor(), False)[-1])


# ═══════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    pf = Path(f"/tmp/dex-panel.{os.getuid()}.pid")
    try:
        if pf.exists():
            try:
                old = int(pf.read_text().strip())
            except Exception:
                try:
                    pf.unlink()
                except Exception:
                    pass
            else:
                try:
                    os.kill(old, 0)
                    if _pid_matches_dex_panel(old):
                        print(f"dex-panel ya corriendo (PID {old})")
                        return
                except ProcessLookupError:
                    pass
                except PermissionError:
                    if _pid_matches_dex_panel(old):
                        print(f"dex-panel ya corriendo (PID {old})")
                        return
        pf.write_text(str(os.getpid()))
    except Exception as e:
        _log(f"pidfile handling failed: {e}")

    try:
        DexPanel()
        Gtk.main()
    finally:
        try: pf.unlink()
        except Exception: pass


if __name__ == "__main__":
    main()
