import math
import os
import re
import io
import zlib
import struct
import sys
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import subprocess
import json
import time
import tempfile
import threading
import ctypes

# ==========================================
# 唤醒 Windows 高 DPI 感知 (解决内嵌画面被裁剪和界面模糊问题)
# ==========================================
try:
    # 告诉 Windows 当前应用自己处理缩放，不要强行拉伸
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ==========================================
# 拦截导入错误
# ==========================================
try:
    from PIL import Image, ImageCms
    import numpy as np
    import cv2  
    import imagecodecs
except Exception as e:
    root = tk.Tk()
    root.withdraw()
    error_msg = traceback.format_exc()
    messagebox.showerror(
        "初始化错误 (闪退拦截)", 
        f"导入第三方依赖库失败！请确保已安装所需依赖包。\n\n"
        f"报错详情: {e}\n\n"
        f"请在终端尝试运行安装命令:\npip install Pillow numpy opencv-python imagecodecs"
    )
    sys.exit(1)

# ==========================================
# MPV 后台渲染引擎控制类
# ==========================================
class MPVPreviewer:
    def __init__(self, mpv_path="mpv.exe", pipe_name="mpv_hdr_pipe"):
        self.mpv_path = os.path.abspath(mpv_path)
        self.pipe_path = rf"\\.\pipe\{pipe_name}"
        self.process = None

    def start(self, wid=None):
        if not os.path.exists(self.mpv_path):
            print(f"❌ 找不到 mpv.exe: {self.mpv_path}")
            return False

        cmd = [
            self.mpv_path,
            "--idle=yes",
            "--keep-open=yes",
            "--image-display-duration=inf",
            "--vo=gpu-next",
            "--target-colorspace-hint=yes",
            f"--input-ipc-server={self.pipe_path}",
            "--hwdec=auto" # 开启硬解，降低 CPU 占用
        ]

        # 【修复核心】：去除 autofit，禁止拖拽，让 MPV 完全贴合容器
        if wid is not None:
            cmd.append(f"--wid={wid}")
            cmd.append("--no-border")
            cmd.append("--force-window=immediate")
            cmd.append("--no-window-dragging") # 防止在内嵌画面上拖拽导致坐标错乱
        else:
            cmd.append("--force-window=yes")

        self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        start_time = time.time()
        while time.time() - start_time < 5.0:
            try:
                with open(self.pipe_path, 'w', encoding='utf-8') as f:
                    pass 
                return True
            except FileNotFoundError:
                time.sleep(0.1)
            except Exception:
                time.sleep(0.1)
        return False

    def show_image(self, file_path):
        if not self.process or self.process.poll() is not None:
            return False

        safe_path = os.path.abspath(file_path).replace('\\', '/')
        command = {"command": ["loadfile", safe_path, "replace"]}
        cmd_str = json.dumps(command) + "\n"
        try:
            with open(self.pipe_path, 'w', encoding='utf-8') as f:
                f.write(cmd_str)
                f.flush()
            return True
        except Exception:
            return False

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                command = {"command": ["quit"]}
                cmd_str = json.dumps(command) + "\n"
                with open(self.pipe_path, 'w', encoding='utf-8') as f:
                    f.write(cmd_str)
                    f.flush()
            except:
                self.process.terminate()

# ==========================================
# 核心光电转换函数 & 元数据注入
# ==========================================

MATRIX_SRGB_TO_2020 = [
    [0.6274040, 0.3292820, 0.0433136],
    [0.0690970, 0.9195400, 0.0113612],
    [0.0163916, 0.0880132, 0.8955950]
]

MATRIX_P3_TO_2020 = [
    [0.7538328, 0.1985976, 0.0475696],
    [0.0457436, 0.9417772, 0.0124792],
    [-0.0012103, -0.0176041, 1.0188144]
]

def inject_hdr_metadata_to_png(png_bytes):
    cicp_data = struct.pack('>BBBB', 9, 16, 0, 1)
    cicp_type = b'cICP'
    crc = zlib.crc32(cicp_type + cicp_data) & 0xffffffff
    cicp_chunk = struct.pack('>I', len(cicp_data)) + cicp_type + cicp_data + struct.pack('>I', crc)
    return png_bytes[:33] + cicp_chunk + png_bytes[33:]

def safe_get(tk_var, default=0.0):
    try:
        val = tk_var.get()
        return val if val != "" else default
    except:
        return default

# ==========================================
# GUI 应用程序类
# ==========================================

class HDRCalculatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gain Map HDR转PQ HDR (Pro Edition)")
        self.root.geometry("1400x900")
        
        self.apply_dark_theme()
        
        # 变量初始化
        self.sdr_gamut = tk.StringVar(value="sRGB")
        self.sdr_eotf = tk.StringVar(value="sRGB")
        self.gm_gamma = [tk.DoubleVar(value=1.0) for _ in range(3)]
        self.gm_min = [tk.DoubleVar(value=0.0) for _ in range(3)]
        self.gm_max = [tk.DoubleVar(value=4.0) for _ in range(3)]
        self.base_offset = [tk.DoubleVar(value=0.015625) for _ in range(3)]
        self.alt_offset = [tk.DoubleVar(value=0.015625) for _ in range(3)]
        self.gm_cap_min = tk.DoubleVar(value=0.0) 
        self.gm_cap_max = tk.DoubleVar(value=4.0) 
        
        self.sdr_white = tk.DoubleVar(value=203.0)
        self.hw_hdr_ratio = tk.DoubleVar(value=4.92)
        self.save_layers_var = tk.BooleanVar(value=False)
        
        self.base_img = None
        self.gain_img = None
        self.current_filepath = ""
        
        self.preview_timer = None
        self.preview_counter = 0
        self.previewer = MPVPreviewer()

        self.build_ui()
        self.bind_events()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.root.after(200, self.init_mpv_engine)

    def init_mpv_engine(self):
        self.log("🚀 正在将 MPV 渲染引擎挂载到右侧视窗...")
        wid = self.preview_frame.winfo_id()
        if self.previewer.start(wid=wid):
            self.log("✅ MPV 渲染引擎挂载成功！")
        else:
            self.log("❌ MPV 启动失败，请检查 mpv.exe 是否在同目录。")

    def apply_dark_theme(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        
        bg_main = '#202020'       
        bg_panel = '#2d2d2d'      
        fg_text = '#e0e0e0'       
        fg_accent = '#4dabf7'     
        bg_input = '#1e1e1e'      
        bg_btn_act = '#383838'    
        border_col = '#404040'    

        self.root.configure(bg=bg_main)
        
        style.configure('.', background=bg_main, foreground=fg_text, font=("Microsoft YaHei UI", 10))
        style.configure('TFrame', background=bg_main)
        style.configure('TLabelframe', background=bg_main, foreground=fg_accent, bordercolor=border_col, borderwidth=1)
        style.configure('TLabelframe.Label', background=bg_main, foreground=fg_accent, font=("Microsoft YaHei UI", 10, "bold"))
        style.configure('TEntry', fieldbackground=bg_input, foreground=fg_text, bordercolor=border_col, lightcolor=bg_main, darkcolor=bg_main)
        style.configure('TCombobox', fieldbackground=bg_input, background=bg_panel, foreground=fg_text, bordercolor=border_col)
        style.configure('TButton', background=bg_panel, foreground=fg_text, bordercolor=border_col, focuscolor=fg_accent, padding=(6, 4))
        style.map('TButton', background=[('active', bg_btn_act)], foreground=[('active', 'white')])
        style.configure('Accent.TButton', background='#005fb8', foreground='white', borderwidth=0, padding=(6, 6))
        style.map('Accent.TButton', background=[('active', '#0078d4')])
        style.configure('TScale', background=bg_main, troughcolor=bg_input)
        style.configure('TCheckbutton', background=bg_main, foreground=fg_text)
        style.map('TCheckbutton', background=[('active', bg_main)])

        self.root.option_add('*TCombobox*Listbox.background', bg_panel)
        self.root.option_add('*TCombobox*Listbox.foreground', fg_text)
        self.root.option_add('*TCombobox*Listbox.selectBackground', '#005fb8')

    def build_ui(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        
        # 高 DPI 状态下左侧留出足够空间
        left_frame = ttk.Frame(main_paned, width=480)
        left_frame.pack_propagate(False) 
        
        self.preview_frame = tk.Frame(main_paned, bg="black", bd=0, highlightthickness=0, width=800, height=800)
        
        main_paned.add(left_frame, weight=0)
        main_paned.add(self.preview_frame, weight=1)

        # ====== 左侧区域内容 ======
        top_btn_frame = ttk.LabelFrame(left_frame, text=" 图像加载 ")
        top_btn_frame.pack(fill="x", pady=(0, 10))
        ttk.Button(top_btn_frame, text="⚡ 自动提取 Ultra HDR (推荐)", style='Accent.TButton', command=self.import_image_metadata).pack(fill="x", padx=10, pady=8)
        
        sub_btn_frame = ttk.Frame(top_btn_frame)
        sub_btn_frame.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(sub_btn_frame, text="导入 SDR 基础层", command=self.import_base_image).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(sub_btn_frame, text="导入 Gain Map 层", command=self.import_gain_image).pack(side="right", fill="x", expand=True, padx=(4, 0))
        
        lf1 = ttk.LabelFrame(left_frame, text=" SDR 色彩空间 ")
        lf1.pack(fill="x", pady=5)
        lf1.columnconfigure(1, weight=1)
        lf1.columnconfigure(3, weight=1)
        
        ttk.Label(lf1, text="色域:").grid(row=0, column=0, padx=(10, 5), pady=8, sticky="e")
        ttk.Combobox(lf1, textvariable=self.sdr_gamut, values=["sRGB", "Display P3"], width=10).grid(row=0, column=1, padx=(0, 10), sticky="ew")
        ttk.Label(lf1, text="EOTF:").grid(row=0, column=2, padx=5, pady=8, sticky="e")
        ttk.Combobox(lf1, textvariable=self.sdr_eotf, values=["sRGB", "Gamma 2.2"], width=10).grid(row=0, column=3, padx=(0, 10), sticky="ew")
        
        lf2 = ttk.LabelFrame(left_frame, text=" Gain Map 元数据 (实时渲染) ")
        lf2.pack(fill="x", pady=5)
        lf2.columnconfigure(1, weight=1)
        lf2.columnconfigure(2, weight=1)
        lf2.columnconfigure(3, weight=1)
        
        def create_3_entries(parent, row, label_text, var_list):
            ttk.Label(parent, text=label_text).grid(row=row, column=0, padx=(5, 5), pady=6, sticky="e")
            ttk.Entry(parent, textvariable=var_list[0], justify='center').grid(row=row, column=1, padx=3, sticky="ew")
            ttk.Entry(parent, textvariable=var_list[1], justify='center').grid(row=row, column=2, padx=3, sticky="ew")
            ttk.Entry(parent, textvariable=var_list[2], justify='center').grid(row=row, column=3, padx=(3, 10), sticky="ew")

        create_3_entries(lf2, 0, "Gamma (RGB):", self.gm_gamma)
        create_3_entries(lf2, 1, "GainMapMin:", self.gm_min)
        create_3_entries(lf2, 2, "GainMapMax:", self.gm_max)
        create_3_entries(lf2, 3, "BaseOffset:", self.base_offset)
        create_3_entries(lf2, 4, "AltOffset:", self.alt_offset)
        
        hr_frame = ttk.Frame(lf2)
        hr_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 5))
        hr_frame.columnconfigure(1, weight=1)
        hr_frame.columnconfigure(3, weight=1)
        
        ttk.Label(hr_frame, text="Base Headroom:").grid(row=0, column=0, padx=5, sticky="e")
        ttk.Entry(hr_frame, textvariable=self.gm_cap_min, width=10, justify='center').grid(row=0, column=1, sticky="w")
        ttk.Label(hr_frame, text="Alt Headroom:").grid(row=0, column=2, padx=5, sticky="e")
        ttk.Entry(hr_frame, textvariable=self.gm_cap_max, width=10, justify='center').grid(row=0, column=3, sticky="w")
        
        lf3 = ttk.LabelFrame(left_frame, text=" 环境光与硬件目标 (实时渲染) ")
        lf3.pack(fill="x", pady=5)
        lf3.columnconfigure(1, weight=1)
        
        ttk.Label(lf3, text="SDR 白点 (nits):").grid(row=0, column=0, padx=(10, 5), pady=8, sticky="e")
        sdr_box = ttk.Frame(lf3)
        sdr_box.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Scale(sdr_box, from_=80, to=500, variable=self.sdr_white, orient="horizontal").pack(side="left", fill="x", expand=True)
        ttk.Entry(sdr_box, textvariable=self.sdr_white, width=6, justify='center').pack(side="right", padx=(10, 0))

        ttk.Label(lf3, text="硬件 HDR 比率:").grid(row=1, column=0, padx=(10, 5), pady=8, sticky="e")
        hw_box = ttk.Frame(lf3)
        hw_box.grid(row=1, column=1, sticky="ew", padx=(0, 10))
        ttk.Scale(hw_box, from_=1.0, to=15.0, variable=self.hw_hdr_ratio, orient="horizontal").pack(side="left", fill="x", expand=True)
        ttk.Entry(hw_box, textvariable=self.hw_hdr_ratio, width=6, justify='center').pack(side="right", padx=(10, 0))

        ttk.Checkbutton(left_frame, text="附带保存 SDR 基础层与 Gain Map 图层", variable=self.save_layers_var).pack(anchor="w", padx=5, pady=(15, 5))

        action_frame = ttk.Frame(left_frame)
        action_frame.pack(pady=5, fill="x")
        ttk.Button(action_frame, text="💾 导出无损 PNG (16-bit)", command=lambda: self.calculate('png')).pack(side="left", expand=True, padx=(0, 4), fill="x")
        ttk.Button(action_frame, text="💾 导出 AVIF (10-bit)", command=lambda: self.calculate('avif')).pack(side="right", expand=True, padx=(4, 0), fill="x")

        self.res_frame = ttk.LabelFrame(left_frame, text=" 终端日志 ")
        self.res_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(self.res_frame, state="disabled", bg="#1e1e1e", fg="#a9b7c6", insertbackground="white", bd=0, highlightthickness=0, font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    def bind_events(self):
        vars_to_bind = [
            *self.gm_gamma, *self.gm_min, *self.gm_max, 
            *self.base_offset, *self.alt_offset, 
            self.gm_cap_min, self.gm_cap_max, 
            self.sdr_white, self.hw_hdr_ratio,
            self.sdr_gamut, self.sdr_eotf
        ]
        def format_scale(*args):
            try:
                val = self.sdr_white.get()
                if val != round(val, 1): self.sdr_white.set(round(val, 1))
                val2 = self.hw_hdr_ratio.get()
                if val2 != round(val2, 2): self.hw_hdr_ratio.set(round(val2, 2))
            except: pass
            self.schedule_preview()
            
        for v in vars_to_bind:
            v.trace_add("write", format_scale)

    def log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    # ==========================================
    # 实时预览核心逻辑
    # ==========================================
    def schedule_preview(self, *args):
        if self.preview_timer:
            self.root.after_cancel(self.preview_timer)
        self.preview_timer = self.root.after(300, self.start_preview_thread)

    def start_preview_thread(self):
        if not self.base_img or not self.gain_img: return
        threading.Thread(target=self._generate_and_push_preview, daemon=True).start()

    def _generate_and_push_preview(self):
        try:
            base_arr_raw = np.array(self.base_img, dtype=np.float32) / 255.0
            h, w = base_arr_raw.shape[:2]
            
            max_dim = 1080
            if max(h, w) > max_dim:
                scale = max_dim / max(h, w)
                new_w, new_h = int(w * scale), int(h * scale)
                base_arr = cv2.resize(base_arr_raw, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            else:
                base_arr = base_arr_raw
                new_w, new_h = w, h

            gain_arr_raw = np.array(self.gain_img, dtype=np.float32) / 255.0
            gain_arr = cv2.resize(gain_arr_raw, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

            sdr_eotf = self.sdr_eotf.get().lower()
            if sdr_eotf == 'srgb':
                linear_sdr_arr = np.where(base_arr <= 0.04045, base_arr / 12.92, np.power((base_arr + 0.055) / 1.055, 2.4))
            else:
                linear_sdr_arr = np.power(base_arr, 2.2)

            gamma_arr = np.array([safe_get(v, 1.0) for v in self.gm_gamma], dtype=np.float32)
            min_arr = np.array([safe_get(v, 0.0) for v in self.gm_min], dtype=np.float32)
            max_arr = np.array([safe_get(v, 4.0) for v in self.gm_max], dtype=np.float32)
            b_offset_arr = np.array([safe_get(v, 0.015625) for v in self.base_offset], dtype=np.float32)
            a_offset_arr = np.array([safe_get(v, 0.015625) for v in self.alt_offset], dtype=np.float32)

            hw_ratio = max(1.0, safe_get(self.hw_hdr_ratio, 4.92))
            cap_min = safe_get(self.gm_cap_min, 0.0)
            cap_max = safe_get(self.gm_cap_max, 4.0)
            
            disp_log2 = math.log2(hw_ratio)
            weight_factor = max(0.0, min(1.0, (disp_log2 - cap_min) / (cap_max - cap_min))) if cap_max > cap_min else 1.0

            log_recovery_arr = np.power(gain_arr, 1.0 / np.where(gamma_arr == 0, 1.0, gamma_arr))
            log_boost_arr = min_arr * (1.0 - log_recovery_arr) + max_arr * log_recovery_arr
            
            linear_hdr_rel_arr = np.maximum(0.0, (linear_sdr_arr + b_offset_arr) * np.exp2(log_boost_arr * weight_factor) - a_offset_arr)
            absolute_nits_arr = linear_hdr_rel_arr * safe_get(self.sdr_white, 203.0)

            sdr_gamut = self.sdr_gamut.get().lower().replace(" ", "")
            trans_matrix = np.array(MATRIX_SRGB_TO_2020 if sdr_gamut == 'srgb' else MATRIX_P3_TO_2020, dtype=np.float32)
            absolute_nits_arr = np.dot(absolute_nits_arr, trans_matrix.T)

            L_arr = np.clip(absolute_nits_arr / 10000.0, 0.0, 1.0)
            m1, m2 = 2610.0 / 16384.0, (2523.0 / 4096.0) * 128.0  
            c1, c2, c3 = 3424.0 / 4096.0, (2413.0 / 4096.0) * 32.0, (2392.0 / 4096.0) * 32.0
            pq_norm_arr = np.power((c1 + c2 * np.power(L_arr, m1)) / (1.0 + c3 * np.power(L_arr, m1)), m2)

            pq_16bit_arr = np.round(pq_norm_arr * 65535.0).astype(np.uint16)
            pq_16bit_bgr = cv2.cvtColor(pq_16bit_arr, cv2.COLOR_RGB2BGR)
            is_success, buffer = cv2.imencode(".png", pq_16bit_bgr)
            
            if is_success:
                hdr_png_bytes = inject_hdr_metadata_to_png(buffer.tobytes())
                self.preview_counter += 1
                temp_dir = tempfile.gettempdir()
                out_path = os.path.join(temp_dir, f"mpv_hdr_preview_{self.preview_counter % 3}.png")
                
                with open(out_path, "wb") as f:
                    f.write(hdr_png_bytes)
                
                time.sleep(0.05) 
                self.previewer.show_image(out_path)

        except Exception as e:
            pass

    # ==========================================
    # 导入方法：自动一体化提取
    # ==========================================
    def import_image_metadata(self):
        filepath = filedialog.askopenfilename(
            title="选择 Ultra HDR 图片",
            filetypes=[("JPEG 图片", "*.jpg *.jpeg"), ("所有文件", "*.*")]
        )
        if not filepath: return
        self.current_filepath = filepath
        self.log(f"📁 正在处理: {os.path.basename(filepath)}")
        try:
            with Image.open(filepath) as img:
                icc_bytes = img.info.get('icc_profile')
                if icc_bytes:
                    prf = ImageCms.ImageCmsProfile(io.BytesIO(icc_bytes))
                    icc_lower = ImageCms.getProfileName(prf).strip().lower()
                    if 'p3' in icc_lower or 'display' in icc_lower: self.sdr_gamut.set("Display P3")
                    elif 'srgb' in icc_lower or 'iec' in icc_lower: self.sdr_gamut.set("sRGB")
            with open(filepath, 'rb') as f:
                data = f.read()
            decoded_data = data.decode('utf-8', errors='ignore')
            def extract_floats(tags):
                for tag in tags:
                    matches = re.findall(rf'(?:[a-zA-Z0-9]+:)?{tag}\s*=\s*"([^"]+)"', decoded_data)
                    matches += re.findall(rf'<(?:[a-zA-Z0-9]+:)?{tag}>(.*?)</(?:[a-zA-Z0-9]+:)?{tag}>', decoded_data, re.DOTALL)
                    for content in matches:
                        floats = re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', content)
                        if floats: return [float(x) for x in floats]
                return None

            mapping = {
                "Gamma": (['Gamma'], self.gm_gamma),
                "GainMapMin": (['GainMapMin'], self.gm_min),
                "GainMapMax": (['GainMapMax'], self.gm_max),
                "BaseOffset": (['BaseOffset', 'OffsetSDR'], self.base_offset),
                "AltOffset": (['AlternateOffset', 'OffsetHDR'], self.alt_offset)
            }
            for _, (tags, var_list) in mapping.items():
                vals = extract_floats(tags)
                if vals:
                    if len(vals) == 1:
                        for i in range(3): var_list[i].set(vals[0])
                    elif len(vals) >= 3:
                        for i in range(3): var_list[i].set(vals[i])

            cap_min = extract_floats(['HDRCapacityMin', 'BaseHDRHeadroom'])
            if cap_min: self.gm_cap_min.set(cap_min[0])
            cap_max = extract_floats(['HDRCapacityMax', 'AltHDRHeadroom'])
            if cap_max: self.gm_cap_max.set(cap_max[0])

            def get_jpeg_main_end_offset(data_bytes):
                offset = 2 
                while offset < len(data_bytes):
                    if data_bytes[offset] != 0xFF:
                        idx = data_bytes.find(b'\xff\xd9', offset)
                        return idx + 2 if idx != -1 else -1
                    marker = data_bytes[offset+1]
                    offset += 2
                    if marker == 0xDA: 
                        idx = data_bytes.find(b'\xff\xd9', offset)
                        return idx + 2 if idx != -1 else -1
                    elif marker not in [0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x00, 0xFF]:
                        if offset + 2 > len(data_bytes): break
                        length = struct.unpack('>H', data_bytes[offset:offset+2])[0]
                        offset += length
                return -1

            primary_end = get_jpeg_main_end_offset(data)
            if primary_end != -1 and primary_end < len(data):
                next_soi = data.find(b'\xff\xd8', primary_end)
                if next_soi != -1:
                    self.base_img = Image.open(io.BytesIO(data[:primary_end])).convert("RGB")
                    self.gain_img = Image.open(io.BytesIO(data[next_soi:])).convert("RGB")
                    self.log(f"✅ 提取成功! Base:{self.base_img.size}, Gain:{self.gain_img.size}")
                    self.schedule_preview() 
                else:
                    self.log("⚠️ 未发现 Gain Map流。")
        except Exception as e:
            self.log(f"❌ 读取错误: {e}")

    def import_base_image(self):
        filepath = filedialog.askopenfilename(title="选择 SDR 基础层", filetypes=[("图片文件", "*.*")])
        if filepath:
            self.current_filepath = filepath
            self.base_img = Image.open(filepath).convert("RGB")
            self.log(f"✅ 基础层导入成功: {self.base_img.size}")
            self.schedule_preview()

    def import_gain_image(self):
        filepath = filedialog.askopenfilename(title="选择 Gain Map 层", filetypes=[("图片文件", "*.*")])
        if filepath:
            if not self.current_filepath: self.current_filepath = filepath
            self.gain_img = Image.open(filepath).convert("RGB")
            self.log(f"✅ 增益层导入成功: {self.gain_img.size}")
            self.schedule_preview()

    # ==========================================
    # 完整导出执行逻辑 (全分辨率)
    # ==========================================
    def calculate(self, output_fmt):
        if not self.base_img or not self.gain_img:
            messagebox.showwarning("缺少图像", "请导入 SDR 基础层和 Gain Map 增益层！")
            return
            
        self.log(f"\n🚀 开始全分辨率渲染 ({output_fmt.upper()})，请稍候...")
        self.root.update() 

        try:
            dir_path = os.path.dirname(self.current_filepath)
            base_name = os.path.splitext(os.path.basename(self.current_filepath))[0]

            if self.save_layers_var.get():
                self.base_img.save(os.path.join(dir_path, f"{base_name}_Base.png"))
                self.gain_img.save(os.path.join(dir_path, f"{base_name}_GainMap.png"))

            base_arr = np.array(self.base_img, dtype=np.float32) / 255.0
            gain_arr_raw = np.array(self.gain_img, dtype=np.float32) / 255.0
            
            if base_arr.shape[:2] != gain_arr_raw.shape[:2]:
                h, w = base_arr.shape[:2]
                gain_arr = cv2.resize(gain_arr_raw, (w, h), interpolation=cv2.INTER_LINEAR)
            else:
                gain_arr = gain_arr_raw

            sdr_eotf = self.sdr_eotf.get().lower()
            if sdr_eotf == 'srgb':
                linear_sdr_arr = np.where(base_arr <= 0.04045, base_arr / 12.92, np.power((base_arr + 0.055) / 1.055, 2.4))
            else:
                linear_sdr_arr = np.power(base_arr, 2.2)

            gamma_arr = np.array([v.get() for v in self.gm_gamma], dtype=np.float32)
            min_arr = np.array([v.get() for v in self.gm_min], dtype=np.float32)
            max_arr = np.array([v.get() for v in self.gm_max], dtype=np.float32)
            b_offset_arr = np.array([v.get() for v in self.base_offset], dtype=np.float32)
            a_offset_arr = np.array([v.get() for v in self.alt_offset], dtype=np.float32)

            hw_ratio = max(1.0, self.hw_hdr_ratio.get())
            cap_min, cap_max = self.gm_cap_min.get(), self.gm_cap_max.get()
            disp_log2 = math.log2(hw_ratio)
            weight_factor = max(0.0, min(1.0, (disp_log2 - cap_min) / (cap_max - cap_min))) if cap_max > cap_min else 1.0

            log_recovery_arr = np.power(gain_arr, 1.0 / np.where(gamma_arr==0, 1.0, gamma_arr))
            log_boost_arr = min_arr * (1.0 - log_recovery_arr) + max_arr * log_recovery_arr
            linear_hdr_rel_arr = np.maximum(0.0, (linear_sdr_arr + b_offset_arr) * np.exp2(log_boost_arr * weight_factor) - a_offset_arr)
            absolute_nits_arr = linear_hdr_rel_arr * self.sdr_white.get()

            sdr_gamut = self.sdr_gamut.get().lower().replace(" ", "")
            trans_matrix = np.array(MATRIX_SRGB_TO_2020 if sdr_gamut == 'srgb' else MATRIX_P3_TO_2020, dtype=np.float32)
            absolute_nits_arr = np.dot(absolute_nits_arr, trans_matrix.T)

            L_arr = np.clip(absolute_nits_arr / 10000.0, 0.0, 1.0)
            m1, m2 = 2610.0 / 16384.0, (2523.0 / 4096.0) * 128.0  
            c1, c2, c3 = 3424.0 / 4096.0, (2413.0 / 4096.0) * 32.0, (2392.0 / 4096.0) * 32.0
            pq_norm_arr = np.power((c1 + c2 * np.power(L_arr, m1)) / (1.0 + c3 * np.power(L_arr, m1)), m2)

            if output_fmt == 'png':
                pq_16bit_arr = np.round(pq_norm_arr * 65535.0).astype(np.uint16)
                pq_16bit_bgr = cv2.cvtColor(pq_16bit_arr, cv2.COLOR_RGB2BGR)
                is_success, buffer = cv2.imencode(".png", pq_16bit_bgr)
                if is_success:
                    hdr_png_bytes = inject_hdr_metadata_to_png(buffer.tobytes())
                    out_path = os.path.join(dir_path, f"{base_name}_NativeHDR.png")
                    with open(out_path, "wb") as f: f.write(hdr_png_bytes)
                    self.log(f"✅ 生成完毕: {os.path.basename(out_path)}")
            
            elif output_fmt == 'avif':
                pq_10bit_arr = np.round(pq_norm_arr * 1023.0).astype(np.uint16)
                out_path = os.path.join(dir_path, f"{base_name}_NativeHDR.avif")
                avif_bytes = imagecodecs.avif_encode(
                    np.ascontiguousarray(pq_10bit_arr),
                    level=95, speed=8, bitspersample=10, 
                    primaries=9, transfer=16, matrix=9, 
                )
                with open(out_path, "wb") as f: f.write(avif_bytes)
                self.log(f"✅ 生成完毕: {os.path.basename(out_path)}")

        except Exception as e:
            messagebox.showerror("计算错误", f"报错:\n{e}")
            self.log(f"❌ 运行报错堆栈:\n{traceback.format_exc()}")

    def on_closing(self):
        self.previewer.close()
        self.root.destroy()

if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = HDRCalculatorApp(root)
        root.mainloop()
    except Exception as e:
        err_msg = traceback.format_exc()
        print(err_msg)
