# Copyright (c) 2025 TR-Supowe，MIT License
# 碧蓝档案主题 · 全窗壁纸版界面（文字直接绘制在壁纸上，输入框用原生控件）

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unzip_core as core

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_OK = True
except Exception:
    DND_OK = False

APP_VERSION = "1.2"
APP_TITLE = f"碧蓝解压 v{APP_VERSION} · 多层伪装包一键解压"

# 布局（和 make_assets.py 的 layout.json 保持一致）
W, H = 1040, 620
DROP = (12, 12, 366, 292)
LEFT = (12, 300, 366, 604)
RIGHT = (376, 12, 1028, 608)

# 配色
ACCENT = "#3e97dd"
ACCENT_DARK = "#2b7ab8"
PINK = "#ff7fa8"
INK = "#123451"
MUTED = "#5d7d99"
OK_C = "#12803a"
ERR_C = "#c0392b"
WARN_C = "#b26a00"
DEBUG_C = "#7b93a8"
RAIL = "#ffffff"

# 界面字体：优先 Fira Code（私有加载，不写注册表、不装系统），
# 中文由系统自动回退到雅黑等中文字体。
F = "Microsoft YaHei UI"
FONT_FAMILY_TARGET = "Fira Code"
FONT = (F, 10)
FONT_SM = (F, 9)
FONT_BOLD = (F, 10, "bold")
FONT_H1 = (F, 14, "bold")
FONT_H2 = (F, 12, "bold")
FONT_LOGO = (F, 16, "bold")




# ---------------------------------------------------------------------------
# DPI 处理：开启 DPI 感知按物理像素渲染（否则 Windows 会位图放大，字会发虚）
# ---------------------------------------------------------------------------
SCALE = 1.0


def enable_dpi_awareness():
    """必须在创建 Tk 之前调用。返回系统缩放系数（1.0 / 1.25 / 1.5 / 2.0 …）。"""
    global SCALE
    scale = 1.0
    if sys.platform == 'win32':
        try:
            import ctypes
            ok = False
            try:
                # PER_MONITOR_AWARE_V2，注意必须声明 argtypes，否则 64 位句柄会被截断
                fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
                fn.argtypes = [ctypes.c_void_p]
                fn.restype = ctypes.c_bool
                ok = bool(fn(ctypes.c_void_p(-4)))
            except Exception:
                ok = False
            if not ok:
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    try:
                        ctypes.windll.user32.SetProcessDPIAware()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            import ctypes
            try:
                dpi = ctypes.windll.user32.GetDpiForSystem()
            except Exception:
                dpi = ctypes.windll.gdi32.GetDeviceCaps(
                    ctypes.windll.user32.GetDC(0), 88)  # LOGPIXELSX
            if dpi and dpi > 0:
                scale = dpi / 96.0
        except Exception:
            scale = 1.0
    SCALE = max(1.0, min(3.0, round(scale * 4) / 4))  # 取 0.25 的整数倍
    return SCALE


class ScaledCanvas(tk.Canvas):
    """把逻辑坐标自动乘上缩放系数的 Canvas。

    界面代码仍然按 1040x620 的逻辑坐标书写，实际输出按 DPI 放大，字才清晰。
    """

    def __init__(self, master, scale=1.0, **kw):
        super().__init__(master, **kw)
        self._s = scale

    def _p(self, x, y):
        return x * self._s, y * self._s

    def create_text(self, x, y, **kw):
        return super().create_text(*self._p(x, y), **kw)

    @staticmethod
    def _flat(coords):
        if len(coords) == 1 and isinstance(coords[0], (list, tuple)):
            return tuple(coords[0])
        return coords

    def create_line(self, *coords, **kw):
        return super().create_line(*[c * self._s for c in self._flat(coords)], **kw)

    def create_rectangle(self, x1, y1, x2, y2, **kw):
        return super().create_rectangle(x1 * self._s, y1 * self._s,
                                        x2 * self._s, y2 * self._s, **kw)

    def create_polygon(self, *coords, **kw):
        return super().create_polygon(*[c * self._s for c in self._flat(coords)], **kw)

    def create_image(self, x, y, **kw):
        return super().create_image(*self._p(x, y), **kw)

    def create_window(self, x, y, **kw):
        for k in ('width', 'height'):
            if kw.get(k):
                kw[k] = int(kw[k] * self._s)
        return super().create_window(*self._p(x, y), **kw)


def load_private_fonts():
    """把 assets/fonts 下的字体注册给当前进程（FR_PRIVATE，仅本程序可见）。"""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        here = os.path.dirname(os.path.abspath(__file__))
        dirs = []
        if getattr(sys, 'frozen', False):
            dirs.append(os.path.join(os.path.dirname(sys.executable), 'assets', 'fonts'))
            if hasattr(sys, '_MEIPASS'):
                dirs.append(os.path.join(sys._MEIPASS, 'assets', 'fonts'))
        dirs.append(os.path.join(here, 'assets', 'fonts'))
        FR_PRIVATE = 0x10
        for d in dirs:
            if not os.path.isdir(d):
                continue
            loaded = 0
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(('.ttf', '.otf')):
                    try:
                        loaded += ctypes.windll.gdi32.AddFontResourceExW(
                            os.path.join(d, f), FR_PRIVATE, 0)
                    except Exception:
                        pass
            if loaded:
                return True
    except Exception:
        pass
    return False


def apply_font_family(root):
    """如果 Fira Code 可用就用它，并把各字号常量按新字体重建。"""
    global F, FONT, FONT_SM, FONT_BOLD, FONT_H1, FONT_H2, FONT_LOGO
    try:
        import tkinter.font as tkfont
        families = {f.lower(): f for f in tkfont.families(root)}
    except Exception:
        return
    pick = families.get(FONT_FAMILY_TARGET.lower())
    if not pick:
        if 'Fira Code' in families:
            pick = families['Fira Code']
        else:
            return
    F = pick
    FONT = (F, 10)
    FONT_SM = (F, 9)
    FONT_BOLD = (F, 10, "bold")
    FONT_H1 = (F, 14, "bold")
    FONT_H2 = (F, 12, "bold")
    FONT_LOGO = (F, 16, "bold")


def resource_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def asset_path(name):
    cands = []
    if getattr(sys, 'frozen', False):
        cands.append(os.path.join(os.path.dirname(sys.executable), 'assets', name))
        if hasattr(sys, '_MEIPASS'):
            cands.append(os.path.join(sys._MEIPASS, 'assets', name))
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, 'assets', name))
    cands.append(os.path.join(here, name))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def find_7z(app_dir):
    import shutil
    for base in (app_dir, os.path.dirname(app_dir)):
        p = os.path.join(base, '7z', '7z.exe')
        if os.path.exists(p):
            return p
    return shutil.which("7z")


class QueueReporter(core.Reporter):
    def __init__(self, q):
        self.q = q

    def info(self, m):
        self.q.put(("log", m, "info"))

    def success(self, m):
        self.q.put(("log", m, "success"))

    def error(self, m):
        self.q.put(("log", m, "error"))

    def warn(self, m):
        self.q.put(("log", m, "warn"))

    def debug(self, m):
        self.q.put(("log", m, "debug"))

    def stage(self, t):
        self.q.put(("stage", t))

    def task(self, i, n, name):
        self.q.put(("task", i, n, name))

    def rename(self, a, b, note=''):
        self.q.put(("rename", a, b, note))

    def progress(self, p):
        self.q.put(("progress", p))


class App:
    def __init__(self, root):
        self.root = root
        self.app_dir = resource_dir()
        self.seven = find_7z(self.app_dir)
        self.queue = queue.Queue()
        self.engine = None
        self.worker = None
        self.cancel_event = threading.Event()
        self.running = False
        self.pending = []
        self._hover = False
        self._closing = False
        self._seq = 0
        self._buttons = {}
        self._pct = 0.0
        self.log_lines = []
        self.rename_rows = []
        self._build_ui()
        if DND_OK:
            self._log("拖拽已启用：把文件或文件夹拖到左侧壁纸上即可开始。", "info")
        else:
            self._log("拖拽组件不可用：请点击「选择文件 / 选择文件夹」。", "warn")
        self.root.after(80, self._drain_queue)

    # ---------------------------------------------------------------- 工具
    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)


    def _load_wallpaper(self):
        """把壁纸按当前 DPI 缩放到物理像素尺寸，保证清晰。"""
        wp = asset_path('wallpaper.png')
        if not wp:
            return None
        target = (int(W * SCALE), int(H * SCALE))
        try:
            from PIL import Image, ImageTk
            im = Image.open(wp).convert('RGB')
            if im.size != target:
                im = im.resize(target, Image.LANCZOS)
            return ImageTk.PhotoImage(im)
        except Exception:
            try:
                return tk.PhotoImage(file=wp)
            except Exception:
                return None

    def _center_on_workarea(self):
        """按主屏工作区（去掉任务栏）居中，避免多屏虚拟桌面上跑出屏幕。"""
        wa_l, wa_t, wa_w, wa_h = 0, 0, 0, 0
        try:
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            # SPI_GETWORKAREA = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
                wa_l, wa_t = r.left, r.top
                wa_w, wa_h = r.right - r.left, r.bottom - r.top
        except Exception:
            pass
        if wa_w <= 0 or wa_h <= 0:
            wa_w = self.root.winfo_screenwidth()
            wa_h = self.root.winfo_screenheight()
        x = max(8, wa_l + (wa_w - int(W * SCALE)) // 2)
        y = max(8, wa_t + (wa_h - int(H * SCALE)) // 2 - 16)
        return x, y

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.root.title(APP_TITLE)
        x, y = self._center_on_workarea()
        self.root.geometry(f"{int(W * SCALE)}x{int(H * SCALE)}+{x}+{y}")
        self.root.resizable(False, False)
        self.root.configure(bg="#cfe8fb")

        ico = asset_path('icon.ico')
        if ico:
            try:
                self.root.iconbitmap(default=ico)
            except Exception:
                pass

        self.canvas = ScaledCanvas(self.root, scale=SCALE, width=int(W * SCALE),
                                   height=int(H * SCALE), highlightthickness=0,
                                   bg="#cfe8fb")
        self.canvas.pack(fill="both", expand=True)

        self._bg = self._load_wallpaper()
        if self._bg is not None:
            self.canvas.create_image(0, 0, image=self._bg, anchor="nw")
        if self._bg is None:
            self.canvas.configure(bg="#cfe8fb")

        self._draw_drop_area()
        self._draw_left_panel()
        self._draw_right_panel()
        self._bind_dnd()

        if not self.seven:
            messagebox.showerror("缺少 7-Zip", "没有找到 7z.exe，请确认程序目录下有 7z 文件夹。")

    # ------------------------------------------------------------- 投放区
    def _draw_drop_area(self):
        x0, y0, x1, y1 = DROP
        cx = x0 + 268
        self.canvas.create_text(cx, y0 + 113, text="拖到这里", font=(F, 16, "bold"),
                                fill="#0b3050", tags="drop")
        self.canvas.create_text(cx, y0 + 147, text="文件 / 文件夹", font=(F, 9),
                                fill="#1b4260", tags="drop")
        self.canvas.create_text(cx, y0 + 167, text="任意后缀 · 分卷 · 加密包",
                                font=(F, 9), fill="#2c5570", tags="drop")

    def _draw_drop_hint(self, hover):
        self.canvas.delete("drophint")
        if not hover:
            return
        x0, y0, x1, y1 = DROP
        self._round_rect(x0 + 2, y0 + 2, x1 - 2, y1 - 2, 18,
                         fill="", outline=ACCENT, width=3, tags="drophint")

    def _queue_hint(self):
        self.canvas.delete("queuehint")
        if self.pending:
            self.canvas.create_text(DROP[0] + 60, DROP[3] - 34,
                                    text=f"已排队 {len(self.pending)} 项",
                                    font=(F, 10, "bold"), fill=PINK, tags="queuehint")

    # ------------------------------------------------------------ 左面板
    def _draw_left_panel(self):
        lx, ly, lr, lb = LEFT
        self.password_var = tk.StringVar()
        self.outdir_var = tk.StringVar()
        self.use_lib = tk.BooleanVar(value=False)
        self.nested = tk.BooleanVar(value=True)
        self.sep_folder = tk.BooleanVar(value=True)
        self.del_src = tk.BooleanVar(value=False)

        # 三个次级按钮
        bw = 106
        self._button(lx + 12, ly + 12, bw, 32, "选择文件", self._pick_files)
        self._button(lx + 12 + bw + 8, ly + 12, bw, 32, "选择文件夹", self._pick_folder)
        self._button(lx + 12 + (bw + 8) * 2, ly + 12, bw, 32, "清空", self._clear)

        # 密码
        self.canvas.create_text(lx + 12, ly + 68, text="解压密码", anchor="w",
                                font=FONT_SM, fill=INK)
        self.pw_entry = self._entry(lx + 78, ly + 56, lr - lx - 90, self.password_var)
        self._checkbox(lx + 12, ly + 92, "使用密码库（passwords/*.txt）", self.use_lib)

        # 解压位置
        self.canvas.create_text(lx + 12, ly + 136, text="解压到", anchor="w",
                                font=FONT_SM, fill=INK)
        self.out_entry = self._entry(lx + 66, ly + 124, 150, self.outdir_var)
        self._button(lx + 226, ly + 124, 116, 30, "浏览…", self._pick_outdir, small=True)
        self.canvas.create_text(lx + 12, ly + 158, text="留空 = 解压到压缩包所在目录",
                                anchor="w", font=(F, 8), fill=MUTED)

        # 开关
        self._checkbox(lx + 12, ly + 186, "解压到单独文件夹", self.sep_folder)
        self._checkbox(lx + 190, ly + 186, "完成后删除压缩包", self.del_src)
        self._checkbox(lx + 12, ly + 214, "自动识别并解压二级压缩（嵌套包）", self.nested)

        # 主操作
        self._button(lx + 12, lb - 56, 200, 42, "开始解压", self.start, primary=True,
                     name="start")
        self._button(lx + 222, lb - 56, 120, 42, "停止", self.stop, name="stop")
        self._set_button_state("stop", False)

    def _entry(self, x, y, w, var):
        e = tk.Entry(self.canvas, textvariable=var, font=FONT, relief="flat",
                     bg="#ffffff", fg=INK, insertbackground=ACCENT,
                     highlightthickness=1, highlightbackground="#a9cfeb",
                     highlightcolor=ACCENT, bd=0)
        self.canvas.create_window(x, y, window=e, anchor="nw", width=w, height=28)
        return e

    def _button(self, x, y, w, h, text, command, primary=False, small=False, name=None):
        """按钮只创建一次图元；此后悬停/禁用只改颜色，绝不删除重建。

        历史坑：早期在悬停回调里 delete + 重建按钮，重建出的控件正好在鼠标
        指针下，会立刻再次触发 <Enter>，形成无限重绘 —— 表现为窗口一点就假死。
        """
        tag = f"btn{self._seq}"
        self._seq += 1
        poly = self._round_rect(x, y, x + w, y + h, 10, fill="#ffffff",
                                outline="#a9cfeb", width=1, tags=(tag, "ui"))
        txt = self.canvas.create_text(x + w // 2, y + h // 2, text=text,
                                      font=(F, 9) if small else FONT_BOLD,
                                      fill=INK, tags=(tag, "ui"))
        self._buttons[tag] = dict(x=x, y=y, w=w, h=h, cmd=command, enabled=True,
                                  primary=primary, text=text, small=small,
                                  poly=poly, txt=txt, hover=False)
        self.canvas.tag_bind(tag, "<Button-1>", lambda e, t=tag: self._on_button(t))
        self.canvas.tag_bind(tag, "<Enter>", lambda e, t=tag: self._button_hover(t, True))
        self.canvas.tag_bind(tag, "<Leave>", lambda e, t=tag: self._button_hover(t, False))
        self._paint_button(tag)
        if name:
            self.__dict__[f"btn_{name}"] = tag
        return tag

    def _paint_button(self, tag):
        """只改颜色，不增删图元。"""
        b = self._buttons[tag]
        if not b['enabled']:
            fill, line, fg = "#dfe9f2", "#c3d6e6", "#9fb3c4"
        elif b['hover']:
            fill = ACCENT_DARK if b['primary'] else "#eaf5ff"
            line = ACCENT_DARK if b['primary'] else ACCENT
            fg = "#ffffff" if b['primary'] else ACCENT_DARK
        else:
            fill = ACCENT if b['primary'] else "#ffffff"
            line = ACCENT if b['primary'] else "#a9cfeb"
            fg = "#ffffff" if b['primary'] else INK
        self.canvas.itemconfigure(b['poly'], fill=fill, outline=line)
        self.canvas.itemconfigure(b['txt'], fill=fg)

    def _button_hover(self, tag, on):
        b = self._buttons[tag]
        if not b['enabled'] or b['hover'] == on:
            return          # 状态没变就不重绘，杜绝事件回环
        b['hover'] = on
        self._paint_button(tag)

    def _on_button(self, tag):
        b = self._buttons[tag]
        if b['enabled']:
            b['cmd']()

    def _set_button_state(self, name, enabled):
        tag = getattr(self, f"btn_{name}", None)
        if not tag:
            return
        self._buttons[tag]['enabled'] = enabled
        if not enabled:
            self._buttons[tag]['hover'] = False
        self._paint_button(tag)

    def _checkbox(self, x, y, label, var):
        tag = f"chk{self._seq}"
        self._seq += 1
        state = {"x": x, "y": y, "var": var, "tag": tag, "label": label}
        setattr(self, f"_{tag}", state)
        self._paint_check(tag)
        self.canvas.tag_bind(tag, "<Button-1>", lambda e, t=tag: self._toggle_check(t))
        return tag

    def _paint_check(self, tag):
        st = getattr(self, f"_{tag}")
        self.canvas.delete(tag)
        x, y = st['x'], st['y']
        self._round_rect(x, y, x + 17, y + 17, 5, fill="#ffffff", outline="#8fc4ea",
                         width=1, tags=(tag,))
        if st['var'].get():
            self.canvas.create_line(x + 4, y + 9, x + 7, y + 13, x + 13, y + 4,
                                    fill=ACCENT, width=2.4, capstyle="round",
                                    joinstyle="round", tags=(tag,))
        self.canvas.create_text(x + 24, y + 8, text=st['label'], anchor="w",
                                font=FONT_SM, fill=INK, tags=(tag,))

    def _toggle_check(self, tag):
        st = getattr(self, f"_{tag}")
        st['var'].set(not st['var'].get())
        self._paint_check(tag)

    # ------------------------------------------------------------ 右面板
    def _draw_right_panel(self):
        rx, ry, rr, rb = RIGHT
        self.task_text = self.canvas.create_text(rx + 20, ry + 26, anchor="w",
                                                 text="等待文件…", font=FONT_H2, fill=INK)
        self.pct_text = self.canvas.create_text(rr - 20, ry + 26, anchor="e",
                                                text="0%", font=FONT_H2, fill=ACCENT)
        self.stage_text = self.canvas.create_text(rx + 20, ry + 56, anchor="w",
                                                  text="阶段：空闲", font=FONT_SM, fill=MUTED)

        # 进度条
        self.bar = (rx + 20, ry + 78, rr - 20, ry + 96)
        self._round_rect(*self.bar, 9, fill="#e6f1fb", outline="", tags="bar")
        self.bar_fill = self._round_rect(self.bar[0], self.bar[1], self.bar[0] + 1,
                                         self.bar[3], 9, fill=ACCENT, outline="", tags="bar")
        self.canvas.tag_lower(self.bar_fill)
        self.canvas.tag_raise(self.bar)

        # 改名区
        self.canvas.create_text(rx + 20, ry + 120, anchor="w", text="分卷识别 / 改名",
                                font=FONT_BOLD, fill=INK)
        self.rename_count = self.canvas.create_text(rr - 20, ry + 120, anchor="e",
                                                    text="暂无改名", font=FONT_SM, fill=PINK)
        self.canvas.create_line(rx + 20, ry + 138, rr - 20, ry + 138, fill="#bcd9ee")
        for tx, txt in ((rx + 24, "原始文件"), (rx + 300, "说明"), (rx + 430, "规范化为")):
            self.canvas.create_text(tx, ry + 158, anchor="w", text=txt,
                                    font=FONT_SM, fill=MUTED)

        # 日志区
        self.log_top = ry + 326
        self.canvas.create_text(rx + 20, ry + 300, anchor="w", text="日志",
                                font=FONT_BOLD, fill=INK)
        self.canvas.create_line(rx + 20, ry + 318, rr - 20, ry + 318, fill="#bcd9ee")
        self.log_x = rx + 24
        self.log_y0 = self.log_top + 6
        self.log_bottom = rb - 12
        self.log_line_h = 18
        self._log_visible = int((self.log_bottom - self.log_y0) / self.log_line_h)

    def _set_progress(self, pct):
        self._pct = max(0.0, min(100.0, pct))
        x0, y0, x1, y1 = self.bar
        w = (x1 - x0) * self._pct / 100.0
        self.canvas.delete("barfill")
        if w > 6:
            self._round_rect(x0, y0, x0 + max(w, 12), y1, 9, fill=ACCENT,
                             outline="", tags="barfill")
            self.canvas.tag_lower("barfill")
            self.canvas.tag_raise("bar")
        self.canvas.itemconfigure(self.pct_text, text=f"{self._pct:.0f}%")

    def _render_log(self):
        self.canvas.delete("logline")
        colors = {"info": INK, "success": OK_C, "error": ERR_C, "warn": WARN_C,
                  "debug": DEBUG_C, "stage": ACCENT_DARK}
        # 先按字符宽度折行，避免长行被右侧裁掉
        wrapped = []
        for text, level in self.log_lines:
            if len(text) <= 94:
                wrapped.append((text, level))
                continue
            indent = "    "
            chunk = ""
            first = True
            for ch in text:
                width = 94 if first else 90
                if len(chunk) >= width:
                    wrapped.append((chunk, level))
                    chunk = indent if not first or True else ""
                    first = False
                chunk += ch
            if chunk:
                wrapped.append((chunk, level))
        lines = wrapped[-self._log_visible:]
        y = self.log_y0
        for text, level in lines:
            self.canvas.create_text(self.log_x, y, anchor="nw", text=text,
                                    font=FONT_SM,
                                    fill=colors.get(level, INK),
                                    tags="logline")
            y += self.log_line_h

    def _render_renames(self):
        self.canvas.delete("renrow")
        rows = self.rename_rows[-4:]
        y = RIGHT[1] + 174
        for a, note, b in rows:
            self.canvas.create_text(RIGHT[0] + 24, y, anchor="nw", text=a,
                                    font=FONT_SM, fill=INK, tags="renrow", width=260)
            self.canvas.create_text(RIGHT[0] + 300, y, anchor="nw", text=note,
                                    font=FONT_SM, fill=MUTED, tags="renrow")
            self.canvas.create_text(RIGHT[0] + 430, y, anchor="nw", text=b,
                                    font=FONT_SM, fill=ACCENT, tags="renrow")
            y += 26

    # ------------------------------------------------------------- 拖拽
    def _bind_dnd(self):
        if not DND_OK:
            return
        try:
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind('<<Drop>>', self._on_drop)
            self.canvas.dnd_bind('<<DropEnter>>', lambda e: self._set_hover(True))
            self.canvas.dnd_bind('<<DropLeave>>', lambda e: self._set_hover(False))
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self._on_drop)
        except Exception:
            pass

    def _set_hover(self, v):
        self._hover = v
        self._draw_drop_hint(v)

    def _on_drop(self, event):
        self._set_hover(False)
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            paths = [event.data]
        self._enqueue([p for p in paths if os.path.exists(p)])

    # ------------------------------------------------------------- 交互
    def _pick_files(self):
        paths = filedialog.askopenfilenames(title="选择要解压的文件")
        if paths:
            self._enqueue(list(paths))

    def _pick_folder(self):
        p = filedialog.askdirectory(title="选择包含待解压文件的文件夹")
        if p:
            self._enqueue([p])

    def _pick_outdir(self):
        p = filedialog.askdirectory(title="选择解压到的文件夹",
                                    initialdir=self.outdir_var.get().strip() or None)
        if p:
            self.outdir_var.set(os.path.normpath(p))

    def _enqueue(self, paths):
        if self.running:
            self._log("正在处理中，请等当前任务完成后再拖入。", "warn")
            return
        self.pending.extend(paths)
        self._queue_hint()
        self._log(f"已加入 {len(paths)} 项，共 {len(self.pending)} 项待处理。", "info")
        if not self.running:
            self.start()

    def _clear(self):
        if self.running:
            return
        self.pending.clear()
        self.rename_rows.clear()
        self.log_lines.clear()
        self._render_log()
        self._render_renames()
        self._queue_hint()
        self.canvas.itemconfigure(self.rename_count, text="暂无改名")
        self._set_progress(0)
        self.canvas.itemconfigure(self.task_text, text="等待文件…")
        self.canvas.itemconfigure(self.stage_text, text="阶段：空闲")

    def _log(self, text, level="info"):
        self.log_lines.append((text, level))
        if len(self.log_lines) > 2000:
            del self.log_lines[:1000]
        self._render_log()

    def _collect_passwords(self):
        pwd = self.password_var.get()
        if self.use_lib.get():
            found = []
            lib = os.path.join(self.app_dir, 'passwords')
            if os.path.isdir(lib):
                for name in sorted(os.listdir(lib)):
                    if name.lower().endswith('.txt'):
                        try:
                            with open(os.path.join(lib, name), 'r', encoding='utf-8') as f:
                                found.extend(x.strip() for x in f if x.strip())
                        except OSError:
                            pass
            if pwd:
                found.insert(0, pwd)
            return found or ['']
        return [pwd] if pwd else ['']

    def start(self):
        if self.running or not self.pending:
            return
        if not self.seven:
            messagebox.showerror("缺少 7-Zip", "没有找到 7z.exe，请确认 7z 文件夹存在。")
            return
        self.running = True
        self.cancel_event = threading.Event()
        self._set_button_state("start", False)
        self._set_button_state("stop", True)
        self.rename_rows.clear()
        self._render_renames()
        self.canvas.itemconfigure(self.rename_count, text="暂无改名")
        self._set_progress(0)

        paths, self.pending = self.pending[:], []
        self._queue_hint()

        reporter = QueueReporter(self.queue)
        out_dir = self.outdir_var.get().strip() or None
        separate = bool(self.sep_folder.get())
        delete_src = bool(self.del_src.get())
        self._log(f"输出位置：{out_dir or '压缩包所在目录'}"
                  f"（{'单独文件夹' if separate else '直接放入'}）", "debug")
        if delete_src:
            self._log("注意：解压成功后将删除原始压缩包。", "warn")
        self.engine = core.Engine(self._collect_passwords(), self.seven, self.app_dir,
                                  reporter=reporter, cancel_event=self.cancel_event,
                                  output_dir=out_dir, separate_folder=separate,
                                  delete_source=delete_src,
                                  recursive=bool(self.nested.get()))

        def work():
            try:
                self.queue.put(("done", self.engine.run(paths)))
            except Exception as e:
                self.queue.put(("fatal", str(e)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop(self):
        if not self.running:
            return
        self.cancel_event.set()
        self._log("已请求停止，将在当前文件结束后中止。", "warn")

    # ------------------------------------------------------------- 队列
    def _drain_queue(self):
        if self._closing:
            return
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1], item[2])
                elif kind == "stage":
                    self.canvas.itemconfigure(self.stage_text, text=f"阶段：{item[1]}")
                    self._log(f"—— {item[1]} ——", "stage")
                elif kind == "task":
                    _, i, n, name = item
                    self.canvas.itemconfigure(self.task_text, text=f"[{i}/{n}] {name}")
                elif kind == "rename":
                    _, a, b, note = item
                    self.rename_rows.append((a, note or "改名", b))
                    self._render_renames()
                    self.canvas.itemconfigure(self.rename_count,
                                              text=f"已识别 {len(self.rename_rows)} 项")
                elif kind == "progress":
                    self._set_progress(item[1])
                elif kind == "done":
                    self._finish(item[1])
                elif kind == "fatal":
                    self._log(f"严重错误: {item[1]}", "error")
                    self._finish({'ok': 0, 'fail': 1, 'outputs': [], 'nested': 0,
                                  'deleted': 0})
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(80, self._drain_queue)

    def _finish(self, stats):
        self.running = False
        self._set_button_state("start", True)
        self._set_button_state("stop", False)
        self._set_progress(100)
        if self.cancel_event.is_set():
            self.canvas.itemconfigure(self.stage_text, text="阶段：已停止")
            self._log("任务已停止。", "warn")
        else:
            self.canvas.itemconfigure(self.stage_text, text="阶段：完成")
            extra = ""
            if stats.get('deleted'):
                extra += f"，已删除源包 {stats['deleted']} 个"
            if stats.get('nested'):
                extra += f"，二级压缩 {stats['nested']} 个"
            self._log(f"全部完成：成功 {stats['ok']} 个，失败 {stats['fail']} 个{extra}。",
                      "success" if not stats.get('failures') else "warn")
            fails = stats.get('failures') or []
            if fails:
                self._log(f"失败原因汇总（共 {len(fails)} 项）：", "error")
                for it in fails[:6]:
                    self._log(f"  · {it['file']} —— {it['reason']}", "error")
                if len(fails) > 6:
                    self._log(f"  …… 其余 {len(fails) - 6} 项见失败报告", "error")
        if stats.get('outputs'):
            self._log("输出目录：" + "；".join(stats['outputs'][-3:]), "debug")


def main():
    enable_dpi_awareness()
    load_private_fonts()
    root = TkinterDnD.Tk() if DND_OK else tk.Tk()
    try:
        # 让字号随 DPI 放大（1pt = SCALE * 96/72 像素）
        root.tk.call('tk', 'scaling', SCALE * 4.0 / 3.0)
    except Exception:
        pass
    apply_font_family(root)
    app = App(root)

    def on_close():
        """关闭流程：取消任务 -> 杀掉 7z -> 最多等 2.5s 让线程收尾 -> 销毁窗口。

        注意：这里不能弹模态确认框。之前用 messagebox 时，用户再点一次关闭
        （或按 ESC）会把模态框关掉、回调返回 False，窗口不销毁 -> 看起来就是
        "点关闭没反应"；即使点了确定，后台线程也可能拖着进程不退出。
        """
        if app._closing:
            return
        app._closing = True
        try:
            if app.engine:
                app.cancel_event.set()
                proc = getattr(app.engine, '_proc', None)
                if proc is not None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            w = app.worker
            if w is not None and w.is_alive():
                w.join(timeout=2.5)          # 让临时目录有机会清理
        except Exception:
            pass
        try:
            root.quit()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        # 兜底：万一还有守护线程/子进程拖着，4 秒后强制结束进程
        def _force_exit():
            time.sleep(4.0)
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True).start()

    root.protocol("WM_DELETE_WINDOW", on_close)

    auto = os.environ.get('UNZIP_GUI_AUTOSTART')
    if auto:
        paths = [p for p in auto.split('|') if p and os.path.exists(p)]
        if paths:
            root.after(400, lambda: app._enqueue(paths))

    root.mainloop()


if __name__ == '__main__':
    main()
