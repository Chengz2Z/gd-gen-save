#!/usr/bin/env python3
"""Small Windows GUI for the GrimTools character save generator."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from generator import EQUIPMENT_SLOTS, GenerationError, generate_save, validate_character_name
from grimtools import GrimToolsError, fetch_build
from save_format import CharacterSave, SaveFormatError
from license_manager import LicenseError, install_license, validate_license


APP_TITLE = "Grim Dawn 存档生成器"
APP_TITLE_AND_AUTHOR = "Grim Dawn 存档生成器 ——by橙子"

SLOT_LABELS: dict[str, str] = {
    "weapon1": "武器1",
    "weapon1Alt": "武器2",
    "weapon2": "副手1",
    "weapon2Alt": "副手2",
    "amulet": "项链",
    "ring1": "戒指1",
    "ring2": "戒指2",
    "head": "头盔",
    "chest": "胸甲",
    "shoulders": "护肩",
    "hands": "护手",
    "legs": "护腿",
    "feet": "鞋子",
    "waist": "腰带",
    "relic": "圣物",
    "medal": "勋章",
}

# 高级面板中的槽位排列顺序
SLOT_ORDER = [
    "weapon1", "weapon1Alt", "weapon2", "weapon2Alt",
    "amulet", "ring1", "ring2",
    "head", "chest", "shoulders", "hands", "legs", "feet", "waist",
    "relic", "medal",
]

def ensure_activated(root: tk.Tk) -> bool:
    """Validate the local license or let the user import an author-issued one."""
    status = validate_license()
    if status.valid:
        return True

    accepted = False
    dialog = tk.Toplevel(root)
    dialog.title("软件离线授权")
    dialog.resizable(False, False)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=20)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        frame,
        text="此电脑尚未授权。请将下面的机器码发送给软件作者，\n收到许可证文件后点击“导入许可证”。",
        justify=tk.LEFT,
    ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 12))
    ttk.Label(frame, text=f"当前状态：{status.reason}", foreground="#9A3412").grid(
        row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 10)
    )
    code_var = tk.StringVar(value=status.machine_code)
    code_entry = ttk.Entry(frame, textvariable=code_var, width=43, state="readonly")
    code_entry.grid(row=2, column=0, sticky=tk.EW)

    def copy_code() -> None:
        root.clipboard_clear()
        root.clipboard_append(status.machine_code)
        root.update()
        copy_button.configure(text="已复制")

    copy_button = ttk.Button(frame, text="复制机器码", command=copy_code)
    copy_button.grid(row=2, column=1, padx=(8, 0))

    def import_selected() -> None:
        nonlocal accepted
        selected = filedialog.askopenfilename(
            title="选择作者签发的许可证",
            filetypes=(("许可证文件", "*.lic"), ("所有文件", "*.*")),
            parent=dialog,
        )
        if not selected:
            return
        try:
            installed = install_license(Path(selected))
        except (LicenseError, OSError) as exc:
            messagebox.showerror("导入失败", str(exc), parent=dialog)
            return
        accepted = True
        messagebox.showinfo(
            "授权成功",
            f"许可证已安装。\n\n机器码：{installed.machine_code}",
            parent=dialog,
        )
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=3, column=0, columnspan=2, sticky=tk.E, pady=(18, 0))
    ttk.Button(buttons, text="退出", command=dialog.destroy).pack(side=tk.RIGHT)
    ttk.Button(buttons, text="导入许可证…", command=import_selected).pack(
        side=tk.RIGHT, padx=(0, 8)
    )
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2
    y = (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2
    dialog.geometry(f"+{x}+{y}")
    root.wait_window(dialog)
    return accepted


def resource_directory() -> Path:
    """Directory containing bundled read-only resources."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


def writable_directory() -> Path:
    """Directory next to the executable, used for generated characters."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class SaveGeneratorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_output: Path | None = None
        self.running = False
        self.template_var = tk.StringVar()
        self.slot_seed_vars: dict[str, tk.StringVar] = {
            slot: tk.StringVar() for slot in SLOT_ORDER
        }
        self.template_placeholder = "可选择存档模板，未选择时使用自带模板"
        self.template_placeholder_active = False
        self.advanced_panel_visible = False

        root.title(APP_TITLE_AND_AUTHOR)
        root.minsize(660, 520)
        root.protocol("WM_DELETE_WINDOW", self._close)

        # 窗口居中显示（先隐藏，设置好位置后再显示）
        root.withdraw()
        root.update_idletasks()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        window_width = 660
        window_height = 520
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        root.deiconify()

        # 主容器，左右布局
        self.main_container = ttk.Frame(root)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # 左侧主面板（固定宽度，不跟随窗口拉伸）
        frame = ttk.Frame(self.main_container, padding=20, width=620)
        frame.pack(side=tk.LEFT, fill=tk.Y)
        frame.pack_propagate(False)  # 保持固定宽度
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(frame, text=APP_TITLE, font=("Microsoft YaHei UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 18)
        )

        ttk.Label(frame, text="模拟器链接：").grid(row=1, column=0, sticky=tk.W, pady=6)
        self.link_var = tk.StringVar(value="https://www.grimtools.com/calc/")
        self.link_entry = ttk.Entry(frame, textvariable=self.link_var)
        self.link_entry.grid(row=1, column=1, sticky=tk.EW, pady=6)
        self.advanced_button = ttk.Button(
            frame, text="高级", command=self._toggle_advanced_panel
        )
        self.advanced_button.grid(row=1, column=2, sticky=tk.W, padx=(6, 0), pady=6)

        ttk.Label(frame, text="角色名称：").grid(row=2, column=0, sticky=tk.W, pady=6)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(frame, textvariable=self.name_var, width=32)
        self.name_entry.grid(row=2, column=1, sticky=tk.EW, pady=6)
        self.name_entry.bind("<Return>", lambda _event: self.start_generation())

        ttk.Label(frame, text="模板目录：").grid(row=3, column=0, sticky=tk.W, pady=6)
        self.template_entry = ttk.Entry(frame, textvariable=self.template_var)
        self.template_entry.grid(row=3, column=1, sticky=tk.EW, pady=6)
        self.template_browse_button = ttk.Button(
            frame, text="浏览…", command=self._browse_template
        )
        self.template_browse_button.grid(row=3, column=2, sticky=tk.W, padx=(6, 0), pady=6)
        
        # 设置占位符提示
        self._setup_template_placeholder()

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=(12, 12))
        self.generate_button = ttk.Button(
            button_frame, text="生成角色存档", command=self.start_generation
        )
        self.generate_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(
            button_frame,
            text="打开输出目录",
            command=self.open_output,
            state=tk.DISABLED,
        )
        self.open_button.pack(side=tk.LEFT, padx=(10, 0))
        self.progress = ttk.Progressbar(button_frame, mode="determinate", length=180, maximum=100)
        self.progress.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(20, 0))

        log_frame = ttk.LabelFrame(frame, text="生成日志", padding=8)
        log_frame.grid(row=5, column=0, columnspan=3, sticky=tk.NSEW)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(
            log_frame,
            height=13,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Microsoft YaHei UI", 9),
        )
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)

        ttk.Label(
            frame,
            text="本软件仅供内部测试人员使用，禁止传播！如果您通过付费或其它方式获取本软件，请立即删除并举报相关渠道。",
            foreground="#555555",
        ).grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))

        # 右侧高级面板（初始隐藏）
        self.advanced_panel = ttk.LabelFrame(
            self.main_container, text="装备种子（留空随机生成）", padding=10
        )
        self.slot_seed_entries: dict[str, ttk.Entry] = {}

        # 用 Canvas + Scrollbar 实现可滚动的种子面板
        canvas = tk.Canvas(self.advanced_panel, highlightthickness=0, width=200)
        scrollbar_adv = ttk.Scrollbar(self.advanced_panel, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar_adv.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_adv.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮支持
        def _on_mousewheel(event: tk.Event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        for i, slot in enumerate(SLOT_ORDER):
            label_text = SLOT_LABELS.get(slot, slot)
            ttk.Label(scroll_frame, text=f"{label_text}：").grid(
                row=i, column=0, sticky=tk.W, pady=2, padx=(0, 4)
            )
            entry = ttk.Entry(
                scroll_frame, textvariable=self.slot_seed_vars[slot], width=14
            )
            entry.grid(row=i, column=1, sticky=tk.EW, pady=2)
            self.slot_seed_entries[slot] = entry
        scroll_frame.columnconfigure(1, weight=1)



        root.after(100, self._process_events)
        self.link_entry.selection_range(0, tk.END)
        self.link_entry.focus_set()
        
        # 初始显示占位符
        self._show_template_placeholder()

    def _setup_template_placeholder(self) -> None:
        """设置模板输入框的占位符提示"""
        self.template_entry.bind("<FocusIn>", self._on_template_focus_in)
        self.template_entry.bind("<FocusOut>", self._on_template_focus_out)

    def _show_template_placeholder(self) -> None:
        """显示占位符提示"""
        if not self.template_var.get() and not self.template_placeholder_active:
            self.template_placeholder_active = True
            self.template_entry.configure(foreground="gray")
            self.template_entry.delete(0, tk.END)
            self.template_entry.insert(0, self.template_placeholder)

    def _hide_template_placeholder(self) -> None:
        """隐藏占位符提示"""
        if self.template_placeholder_active:
            self.template_placeholder_active = False
            self.template_entry.configure(foreground="black")
            self.template_entry.delete(0, tk.END)

    def _on_template_focus_in(self, event) -> None:
        """模板输入框获得焦点时"""
        if self.template_placeholder_active:
            self._hide_template_placeholder()

    def _on_template_focus_out(self, event) -> None:
        """模板输入框失去焦点时"""
        if not self.template_var.get():
            self._show_template_placeholder()

    def _toggle_advanced_panel(self) -> None:
        """切换高级面板的显示/隐藏"""
        if self.advanced_panel_visible:
            self.advanced_panel.pack_forget()
            self.advanced_panel_visible = False
            self.root.update_idletasks()
            self.root.geometry("660x520")
        else:
            self.root.geometry("840x520")
            self.root.update_idletasks()
            self.advanced_panel.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(0, 10), pady=20)
            self.advanced_panel_visible = True

    def _browse_template(self) -> None:
        """打开文件对话框选择模板目录"""
        current_template = self.template_var.get()
        # 如果当前显示的是占位符或为空，使用用户文档目录作为初始目录
        if not current_template or self.template_placeholder_active:
            # 使用用户的文档目录，如果不存在则使用用户主目录
            documents_dir = Path.home() / "Documents"
            if not documents_dir.is_dir():
                documents_dir = Path.home()
            initial_dir = str(documents_dir)
        else:
            initial_dir = str(Path(current_template).parent)
        
        directory = filedialog.askdirectory(
            title="选择模板目录",
            initialdir=initial_dir,
            parent=self.root,
        )
        
        if directory:
            template_path = Path(directory)
            # 验证模板目录是否包含 player.gdc 文件
            if not (template_path / "player.gdc").is_file():
                messagebox.showwarning(
                    APP_TITLE,
                    f"选择的目录不是有效的模板目录：\n{template_path}\n\n模板目录必须包含 player.gdc 文件。",
                    parent=self.root,
                )
                return
            # 隐藏占位符并设置选择的路径
            self._hide_template_placeholder()
            self.template_var.set(str(template_path))

    def _append_log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text.rstrip() + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.generate_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.link_entry.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.name_entry.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.template_entry.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.template_browse_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.advanced_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        entry_state = tk.DISABLED if running else tk.NORMAL
        for entry in self.slot_seed_entries.values():
            entry.configure(state=entry_state)
        if running:
            self.open_button.configure(state=tk.DISABLED)
            self.progress["value"] = 0
        else:
            self.progress["value"] = 100
            if self.last_output and self.last_output.is_dir():
                self.open_button.configure(state=tk.NORMAL)

    def start_generation(self) -> None:
        if self.running:
            return
        link = self.link_var.get().strip()
        try:
            name = validate_character_name(self.name_var.get())
        except GenerationError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            self.name_entry.focus_set()
            return
        if not link or link == "https://www.grimtools.com/calc/":
            messagebox.showwarning(APP_TITLE, "请输入完整的 GrimTools 构筑链接。", parent=self.root)
            self.link_entry.focus_set()
            return
        
        # 验证模板目录
        template_input = self.template_var.get().strip()
        # 如果输入框显示占位符或为空，使用默认模板
        if not template_input or self.template_placeholder_active:
            template_path = resource_directory() / "_template"
        else:
            template_path = Path(template_input)
            if not template_path.is_dir():
                messagebox.showwarning(
                    APP_TITLE,
                    f"模板目录不存在：\n{template_path}",
                    parent=self.root,
                )
                self.template_entry.focus_set()
                return
            if not (template_path / "player.gdc").is_file():
                messagebox.showwarning(
                    APP_TITLE,
                    f"模板目录不是有效的模板目录：\n{template_path}\n\n模板目录必须包含 player.gdc 文件。",
                    parent=self.root,
                )
                self.template_entry.focus_set()
                return

        output_root = writable_directory() / "output"
        output_directory = output_root / f"_{name}"
        overwrite = False
        if output_directory.exists():
            overwrite = messagebox.askyesno(
                APP_TITLE,
                f"角色目录已经存在：\n{output_directory}\n\n是否覆盖？",
                parent=self.root,
            )
            if not overwrite:
                return

        # 解析各槽位的用户输入种子
        slot_seeds: dict[str, int] = {}
        for slot in SLOT_ORDER:
            raw = self.slot_seed_vars[slot].get().strip()
            if not raw:
                continue
            try:
                slot_seeds[slot] = int(raw)
            except ValueError:
                label = SLOT_LABELS.get(slot, slot)
                messagebox.showwarning(
                    APP_TITLE,
                    f"「{label}」的随机种子必须是整数。",
                    parent=self.root,
                )
                self.slot_seed_entries[slot].focus_set()
                return

        self.last_output = None
        self._clear_log()
        self._set_running(True)
        is_default_template = (template_path == resource_directory() / "_template")
        worker = threading.Thread(
            target=self._generate,
            args=(link, name, template_path, is_default_template, output_root, overwrite, slot_seeds or None),
            daemon=True,
        )
        worker.start()

    def _generate(
        self, link: str, name: str, template_directory: Path, is_default_template: bool, output_root: Path, overwrite: bool, slot_seeds: dict[str, int] | None
    ) -> None:
        try:
            self.events.put(("progress", 10))
            self.events.put(("log", "[1/4] 正在读取 GrimTools 构筑……"))
            build = fetch_build(link)
            self.events.put(
                ("log", f"      构筑 ID: {build.build_id}；游戏版本: {build.game_version}")
            )
            if is_default_template:
                self.events.put(("log", "      使用默认模板"))
            else:
                self.events.put(("log", f"      使用自定义模板: {template_directory}"))
            if slot_seeds:
                labels = [SLOT_LABELS.get(s, s) for s in slot_seeds]
                self.events.put(("log", f"      使用自定义种子部位: {', '.join(labels)}"))
            
            self.events.put(("progress", 30))
            self.events.put(("log", "[2/4] 正在复制模板并写入角色数据……"))
            result = generate_save(
                build,
                name,
                template_directory,
                output_root,
                overwrite=overwrite,
                slot_seeds=slot_seeds,
            )
            
            self.events.put(("progress", 70))
            self.events.put(("log", "[3/4] 已完成解密后回读校验。"))
            
            self.events.put(("progress", 90))
            self.events.put(("log", "[4/4] 角色存档生成成功："))
            self.events.put(("log", f"      角色名称: {result.character_name}"))
            self.events.put(("log", f"      职业标记: {result.class_tag}"))
            self.events.put(("log", f"      等级: {result.level}"))
            self.events.put(
                (
                    "log",
                    f"      装备: {result.equipment_count}；技能: {result.skill_count}；"
                    f"星座节点: {result.devotion_count}",
                )
            )
            for warning in result.warnings:
                self.events.put(("log", f"[注意] {warning}"))
            self.events.put(("progress", 100))
            self.events.put(("success", result.output_directory))
            self.events.put(("log", f"输出目录: {result.output_directory}"))
        except (GenerationError, GrimToolsError, SaveFormatError, OSError) as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:  # Keep the packaged GUI from exiting silently.
            self.events.put(("error", f"未预期错误：{exc}"))

    def _process_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "progress":
                    self.progress["value"] = value
                elif kind == "success":
                    self.last_output = Path(value)
                    self._set_running(False)
                    messagebox.showinfo(
                        APP_TITLE,
                        f"角色存档生成成功！\n\n{self.last_output}",
                        parent=self.root,
                    )
                elif kind == "error":
                    self._append_log(f"[失败] {value}")
                    self._set_running(False)
                    messagebox.showerror(APP_TITLE, str(value), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def open_output(self) -> None:
        if self.last_output and self.last_output.is_dir():
            # 打开输出根目录（output目录），而不是具体的存档目录
            output_root = self.last_output.parent
            if output_root.is_dir():
                os.startfile(output_root)  # type: ignore[attr-defined]

    def _close(self) -> None:
        if self.running and not messagebox.askyesno(
            APP_TITLE, "角色存档仍在生成，确定要退出吗？", parent=self.root
        ):
            return
        self.root.destroy()


def main() -> int:
    if "--self-test" in sys.argv:
        template = resource_directory() / "_template"
        player_file = template / "player.gdc"
        if not player_file.is_file():
            return 2
        CharacterSave.load(player_file)
        if sum(1 for path in template.rglob("*") if path.is_file()) < 1:
            return 3
        return 0

    root = tk.Tk()
    root.withdraw()
    if not ensure_activated(root):
        root.destroy()
        return 1
    SaveGeneratorApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
