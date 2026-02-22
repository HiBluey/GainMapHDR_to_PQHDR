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
# 核心光电转换函数 (EOTF & OETF)
# ==========================================

def eotf_srgb(v_8bit):
    v = v_8bit / 255.0
    return v / 12.92 if v <= 0.04045 else math.pow((v + 0.055) / 1.055, 2.4)

def eotf_gamma22(v_8bit):
    v = v_8bit / 255.0
    return math.pow(v, 2.2)

# 色域转换矩阵
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

# ==========================================
# 二进制 HDR 标签强行注入函数 (PNG使用)
# ==========================================
def inject_hdr_metadata_to_png(png_bytes):
    # Color Primaries = 9 (Rec. 2020)
    # Transfer Characteristics = 16 (SMPTE ST 2084 / PQ)
    # Matrix Coefficients = 0 (RGB)
    # Video Full Range Flag = 1 (Full Range)
    cicp_data = struct.pack('>BBBB', 9, 16, 0, 1)
    cicp_type = b'cICP'
    crc = zlib.crc32(cicp_type + cicp_data) & 0xffffffff
    cicp_chunk = struct.pack('>I', len(cicp_data)) + cicp_type + cicp_data + struct.pack('>I', crc)
    return png_bytes[:33] + cicp_chunk + png_bytes[33:]

# ==========================================
# GUI 应用程序类
# ==========================================

class HDRCalculatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gain Map HDR转PQ HDR")
        self.root.geometry("860x720")
        self.root.configure(padx=20, pady=20)
        
        self.sdr_gamut = tk.StringVar(value="sRGB")
        self.sdr_eotf = tk.StringVar(value="sRGB")
        
        self.gm_gamma = [tk.DoubleVar(value=1.0) for _ in range(3)]
        self.gm_min = [tk.DoubleVar(value=0.0) for _ in range(3)]
        self.gm_max = [tk.DoubleVar(value=4.0) for _ in range(3)]
        self.base_offset = [tk.DoubleVar(value=0.015625) for _ in range(3)]
        self.alt_offset = [tk.DoubleVar(value=0.015625) for _ in range(3)]
        
        self.gm_cap_min = tk.DoubleVar(value=0.0) 
        self.gm_cap_max = tk.DoubleVar(value=4.0) 
        
        self.sdr_white = tk.DoubleVar(value=100.0)
        
        # 是否保存分层图片的复选框变量
        self.save_layers_var = tk.BooleanVar(value=False)
        
        self.base_img = None
        self.gain_img = None
        self.current_filepath = ""
        
        self.build_ui()

    def build_ui(self):
        style = ttk.Style()
        style.configure("TLabel", font=("Arial", 10))
        
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill="both", expand=True)
        
        lf1 = ttk.LabelFrame(main_frame, text=" 1. SDR 基础层色彩空间配置 ")
        lf1.pack(fill="x", pady=10)
        ttk.Label(lf1, text="色域:").grid(row=0, column=0, padx=5, pady=10)
        ttk.Combobox(lf1, textvariable=self.sdr_gamut, values=["sRGB", "Display P3"], width=15).grid(row=0, column=1)
        ttk.Label(lf1, text="EOTF:").grid(row=0, column=2, padx=(30,5))
        ttk.Combobox(lf1, textvariable=self.sdr_eotf, values=["sRGB", "Gamma 2.2"], width=15).grid(row=0, column=3)
        
        lf2 = ttk.LabelFrame(main_frame, text=" 2. Gain Map 元数据与图像导入")
        lf2.pack(fill="x", pady=10)
        def create_3_entries(parent, row, label_text, var_list):
            ttk.Label(parent, text=label_text).grid(row=row, column=0, padx=5, pady=5, sticky="e")
            ttk.Entry(parent, textvariable=var_list[0], width=10).grid(row=row, column=1, padx=5)
            ttk.Entry(parent, textvariable=var_list[1], width=10).grid(row=row, column=2, padx=5)
            ttk.Entry(parent, textvariable=var_list[2], width=10).grid(row=row, column=3, padx=5)

        create_3_entries(lf2, 0, "Gamma (R/G/B):", self.gm_gamma)
        create_3_entries(lf2, 1, "GainMapMin:", self.gm_min)
        create_3_entries(lf2, 2, "GainMapMax:", self.gm_max)
        create_3_entries(lf2, 3, "BaseOffset:", self.base_offset)
        create_3_entries(lf2, 4, "AlternateOffset:", self.alt_offset)
        
        ttk.Label(lf2, text="Base HDR Headroom:").grid(row=5, column=0, padx=5, pady=10, sticky="e")
        ttk.Entry(lf2, textvariable=self.gm_cap_min, width=10).grid(row=5, column=1, padx=5)
        ttk.Label(lf2, text="Alt HDR Headroom:").grid(row=5, column=2, padx=5, pady=10, sticky="e")
        ttk.Entry(lf2, textvariable=self.gm_cap_max, width=10).grid(row=5, column=3, padx=5)
        
        btn_frame = ttk.Frame(lf2)
        btn_frame.grid(row=0, column=4, rowspan=6, padx=20, pady=5, sticky="ns")
        
        import_auto_btn = ttk.Button(btn_frame, text="自动提取 Ultra HDR", command=self.import_image_metadata)
        import_auto_btn.pack(fill="x", pady=5)
        
        import_base_btn = ttk.Button(btn_frame, text="手动导入 SDR 基础层", command=self.import_base_image)
        import_base_btn.pack(fill="x", pady=5)
        
        import_gain_btn = ttk.Button(btn_frame, text="手动导入 Gain Map 层", command=self.import_gain_image)
        import_gain_btn.pack(fill="x", pady=5)
        
        lf3 = ttk.LabelFrame(main_frame, text=" 3. 物理环境与亮度锚点 ")
        lf3.pack(fill="x", pady=10)
        ttk.Label(lf3, text="SDR白点亮度 (nits):").grid(row=0, column=0, padx=5, pady=10)
        ttk.Entry(lf3, textvariable=self.sdr_white, width=15).grid(row=0, column=1)

        # 选项框：是否同时输出图层
        ttk.Checkbutton(main_frame, text="同时输出保存 SDR 基础层与 Gain Map 增益层图片", variable=self.save_layers_var).pack(pady=(10, 0))

        # 按钮容器
        action_frame = ttk.Frame(main_frame)
        action_frame.pack(pady=10, fill="x")
        
        calc_png_btn = ttk.Button(action_frame, text="输出 16-bit PNG (Rec.2020 PQ)", command=lambda: self.calculate('png'))
        calc_png_btn.pack(side="left", expand=True, padx=5, fill="x")

        calc_avif_btn = ttk.Button(action_frame, text="输出 10-bit AVIF (Rec.2020 PQ)", command=lambda: self.calculate('avif'))
        calc_avif_btn.pack(side="right", expand=True, padx=5, fill="x")

        self.res_frame = ttk.LabelFrame(main_frame, text=" 运行日志 ")
        self.res_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(self.res_frame, height=8, state="disabled", bg="#f0f0f0", font=("Courier", 10))
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    def log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    # ==========================================
    # 导入方法：自动一体化提取
    # ==========================================
    def import_image_metadata(self):
        filepath = filedialog.askopenfilename(
            title="选择 Ultra HDR 图片",
            filetypes=[("JPEG 图片", "*.jpg *.jpeg"), ("所有文件", "*.*")]
        )
        if not filepath:
            return
            
        self.current_filepath = filepath
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state="disabled")
        
        self.log(f"📁 正在自动处理图片: {filepath}")
        
        try:
            with Image.open(filepath) as img:
                icc_bytes = img.info.get('icc_profile')
                if icc_bytes:
                    prf = ImageCms.ImageCmsProfile(io.BytesIO(icc_bytes))
                    icc_name = ImageCms.getProfileName(prf).strip()
                    icc_lower = icc_name.lower()
                    if 'p3' in icc_lower or 'display' in icc_lower: self.sdr_gamut.set("Display P3")
                    elif 'srgb' in icc_lower or 'iec' in icc_lower: self.sdr_gamut.set("sRGB")
        except Exception:
            pass
            
        try:
            with open(filepath, 'rb') as f:
                data = f.read()

            decoded_data = data.decode('utf-8', errors='ignore')
            def extract_floats(tags):
                for tag in tags:
                    attr_pattern = rf'(?:[a-zA-Z0-9]+:)?{tag}\s*=\s*"([^"]+)"'
                    node_pattern = rf'<(?:[a-zA-Z0-9]+:)?{tag}>(.*?)</(?:[a-zA-Z0-9]+:)?{tag}>'
                    matches = re.findall(attr_pattern, decoded_data)
                    matches += re.findall(node_pattern, decoded_data, re.DOTALL)
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
            
            for log_key, (tags, var_list) in mapping.items():
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

            self.log("✅ 元数据提取完毕，准备提取双图层...")

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
                        if idx != -1:
                            return idx + 2
                        break
                    elif marker in [0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x00, 0xFF]:
                        pass 
                    else:
                        if offset + 2 > len(data_bytes): break
                        length = struct.unpack('>H', data_bytes[offset:offset+2])[0]
                        offset += length
                return -1

            primary_end = get_jpeg_main_end_offset(data)
            
            if primary_end != -1 and primary_end < len(data):
                next_soi = data.find(b'\xff\xd8', primary_end)
                if next_soi != -1:
                    f_io_base = io.BytesIO(data[:primary_end])
                    f_io_gain = io.BytesIO(data[next_soi:])
                    self.base_img = Image.open(f_io_base).convert("RGB")
                    self.gain_img = Image.open(f_io_gain).convert("RGB")
                    self.log(f"✅ 图层分离成功！Base: {self.base_img.size}, GainMap: {self.gain_img.size}")
                else:
                    self.log("⚠️ 尾部未发现追加的 Gain Map 图像流。")
            else:
                self.log("⚠️ 无法正确解析主图像边界。")

        except Exception as e:
            err_details = traceback.format_exc()
            messagebox.showerror("读取错误", f"读取异常:\n{e}")
            self.log(f"❌ 读取错误:\n{err_details}")

    # ==========================================
    # 手动导入
    # ==========================================
    def import_base_image(self):
        filepath = filedialog.askopenfilename(
            title="选择 SDR 基础层图片",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.tif *.tiff"), ("所有文件", "*.*")]
        )
        if not filepath: return
        self.current_filepath = filepath
        self.log(f"📁 正在导入 SDR 基础层: {filepath}")
        try:
            self.base_img = Image.open(filepath).convert("RGB")
            self.log(f"✅ 基础层导入成功！分辨率: {self.base_img.size}")
        except Exception as e:
            messagebox.showerror("读取错误", f"基础层导入异常:\n{e}")

    def import_gain_image(self):
        filepath = filedialog.askopenfilename(
            title="选择 Gain Map 增益层图片",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.tif *.tiff"), ("所有文件", "*.*")]
        )
        if not filepath: return
        if not self.current_filepath: self.current_filepath = filepath
        self.log(f"📁 正在导入 Gain Map 层: {filepath}")
        try:
            self.gain_img = Image.open(filepath).convert("RGB")
            self.log(f"✅ 增益层导入成功！分辨率: {self.gain_img.size}")
        except Exception as e:
            messagebox.showerror("读取错误", f"增益层导入异常:\n{e}")

    # ==========================================
    # 渲染执行逻辑
    # ==========================================
    def calculate(self, output_fmt):
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state="disabled")
        
        if not self.base_img or not self.gain_img:
            messagebox.showwarning("缺少图像", "请先确保同时导入了 SDR 基础层和 Gain Map 增益层！")
            return

        self.log(f"\n==================================================")
        self.log(f"🚀 开始全像素渲染 ({output_fmt.upper()} 格式)")
        self.root.update() 

        try:
            dir_path = os.path.dirname(self.current_filepath)
            base_name = os.path.splitext(os.path.basename(self.current_filepath))[0]

            # 根据勾选状态决定是否保存图层
            if self.save_layers_var.get():
                self.log("💾 正在将提取出的底层 SDR 与 Gain Map 独立保存为图片...")
                base_out_path = os.path.join(dir_path, f"{base_name}_Base.png")
                gain_out_path = os.path.join(dir_path, f"{base_name}_GainMap.png")
                self.base_img.save(base_out_path)
                self.gain_img.save(gain_out_path)
                self.log(f"✅ 双层图层已保存:\n - {base_out_path}\n - {gain_out_path}")

            base_arr = np.array(self.base_img, dtype=np.float32) / 255.0
            
            gain_arr_raw = np.array(self.gain_img, dtype=np.float32) / 255.0
            if base_arr.shape[:2] != gain_arr_raw.shape[:2]:
                h, w = base_arr.shape[:2]
                self.log("🔄 正在对齐 Gain Map 分辨率...")
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

            log_recovery_arr = np.power(gain_arr, 1.0 / gamma_arr)
            log_boost_arr = min_arr * (1.0 - log_recovery_arr) + max_arr * log_recovery_arr
            
            weight_factor = 1.0 
            
            linear_hdr_rel_arr = np.maximum(0.0, (linear_sdr_arr + b_offset_arr) * np.exp2(log_boost_arr * weight_factor) - a_offset_arr)
            absolute_nits_arr = linear_hdr_rel_arr * self.sdr_white.get()

            sdr_gamut = self.sdr_gamut.get().lower().replace(" ", "")
            if sdr_gamut == 'srgb':
                trans_matrix = np.array(MATRIX_SRGB_TO_2020, dtype=np.float32)
                absolute_nits_arr = np.dot(absolute_nits_arr, trans_matrix.T)
            elif sdr_gamut in ['displayp3', 'p3']:
                trans_matrix = np.array(MATRIX_P3_TO_2020, dtype=np.float32)
                absolute_nits_arr = np.dot(absolute_nits_arr, trans_matrix.T)

            L_arr = np.clip(absolute_nits_arr / 10000.0, 0.0, 1.0)
            m1 = 2610.0 / 16384.0
            m2 = (2523.0 / 4096.0) * 128.0  
            c1 = 3424.0 / 4096.0
            c2 = (2413.0 / 4096.0) * 32.0
            c3 = (2392.0 / 4096.0) * 32.0

            pq_norm_arr = np.power((c1 + c2 * np.power(L_arr, m1)) / (1.0 + c3 * np.power(L_arr, m1)), m2)

            # 分流格式导出
            if output_fmt == 'png':
                self.log("📦 正在生成无损 16-bit PQ PNG...")
                pq_16bit_arr = np.round(pq_norm_arr * 65535.0).astype(np.uint16)
                pq_16bit_bgr = cv2.cvtColor(pq_16bit_arr, cv2.COLOR_RGB2BGR)
                is_success, buffer = cv2.imencode(".png", pq_16bit_bgr)
                
                if is_success:
                    hdr_png_bytes = inject_hdr_metadata_to_png(buffer.tobytes())
                    out_path = os.path.join(dir_path, f"{base_name}_NativeHDR.png")
                    with open(out_path, "wb") as f:
                        f.write(hdr_png_bytes)
                    self.log(f"✅ 已生成png HDR 图位于: \n{out_path}")
                else:
                    self.log("❌ OpenCV 内存编码 PNG 图像失败。")

            elif output_fmt == 'avif':
                self.log("📦 正在生成 10-bit PQ AVIF 格式...")
                pq_10bit_arr = np.round(pq_norm_arr * 1023.0).astype(np.uint16)
                pq_10bit_arr_contiguous = np.ascontiguousarray(pq_10bit_arr)

                out_path = os.path.join(dir_path, f"{base_name}_NativeHDR.avif")
                avif_bytes = imagecodecs.avif_encode(
                    pq_10bit_arr_contiguous,
                    level=85,             
                    bitspersample=10,     
                    primaries=9,          
                    transfer=16,          
                    matrix=9,             
                )
                
                with open(out_path, "wb") as f:
                    f.write(avif_bytes)
                self.log(f"✅ 已生成10-bit AVIF HDR 位于: \n{out_path}")

        except Exception as e:
            # 完整输出 Traceback 到界面和弹窗
            err_details = traceback.format_exc()
            messagebox.showerror("计算错误", f"渲染过程中发生错误:\n\n{e}\n\n请查看运行日志获取详细报错堆栈。")
            self.log(f"❌ 运行报错堆栈:\n{err_details}")

if __name__ == "__main__":
    # ==========================================
    # 终极错误兜底策略 (拦截任何未知导致的顶层崩溃)
    # ==========================================
    try:
        root = tk.Tk()
        app = HDRCalculatorApp(root)
        root.mainloop()
    except Exception as e:
        err_msg = traceback.format_exc()
        try:
            # 尝试通过弹窗显示致命错误
            import tkinter.messagebox as mb
            error_root = tk.Tk()
            error_root.withdraw()
            mb.showerror("致命崩溃", f"程序运行遇到未捕获的致命错误:\n\n{err_msg}")
        except:
            # 连弹窗库都无法调用的情况，直接打印并阻塞命令行关闭
            print("=== 发生致命错误 ===")
            print(err_msg)
            input("\n按回车键退出程序...")
