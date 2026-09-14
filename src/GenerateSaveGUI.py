#!/usr/bin/env python3
"""Small Windows GUI for the GrimTools character save generator."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app_version import APP_VERSION, branded_window_title
from generator import EQUIPMENT_SLOTS, GenerationError, generate_save, validate_character_name, _load_crafting_bonus
from grimtools import GrimToolsError, fetch_build
from save_format import CharacterSave, SaveFormatError
from i18n import LanguageManager
from runtime_paths import data_directory, gui_config_path, program_directory


CONFIG_FILE = gui_config_path()
I18N = LanguageManager(CONFIG_FILE)


def tr(key: str, **values: object) -> str:
    return I18N.text(key, **values)


TRANSLATED_SLOTS = (
    "weapon1", "weapon1Alt", "weapon2", "weapon2Alt", "amulet", "ring1",
    "ring2", "head", "chest", "shoulders", "hands", "legs", "feet",
    "waist", "relic", "medal",
)
APP_TITLE = ""
APP_TITLE_AND_AUTHOR = ""
SLOT_LABELS: dict[str, str] = {}


def refresh_translated_constants() -> None:
    global APP_TITLE, APP_TITLE_AND_AUTHOR
    APP_TITLE = tr("app.title")
    APP_TITLE_AND_AUTHOR = branded_window_title(APP_TITLE)
    SLOT_LABELS.clear()
    SLOT_LABELS.update({slot: tr(f"slot.{slot}") for slot in TRANSLATED_SLOTS})


refresh_translated_constants()

# 高级面板中的槽位排列顺序
SLOT_ORDER = [
    "weapon1", "weapon1Alt", "weapon2", "weapon2Alt",
    "amulet", "ring1", "ring2",
    "head", "chest", "shoulders", "hands", "legs", "feet", "waist",
    "relic", "medal",
]

def writable_directory() -> Path:
    """Directory next to the executable, used for generated characters."""
    return program_directory()


class SaveGeneratorApp:
    COLLAPSED_WIDTH = 660
    EXPANDED_WIDTH = 1000
    CONTENT_HEIGHT = 560
    WINDOW_HEIGHT = 563

    def __init__(
        self,
        root: tk.Tk,
        window_title: str | None = None,
    ):
        self.root = root
        self.disposed = False
        self._after_id: str | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_output: Path | None = None
        self.running = False
        self.template_var = tk.StringVar()
        self.slot_seed_vars: dict[str, tk.StringVar] = {
            slot: tk.StringVar() for slot in SLOT_ORDER
        }
        # 加载锻造奖励数据
        self.crafting_bonus_data = _load_crafting_bonus()
        self.crafting_bonus_options = self._build_crafting_options()
        self.slot_crafting_vars: dict[str, tk.StringVar] = {
            slot: tk.StringVar(value="") for slot in SLOT_ORDER
        }
        self.template_placeholder = tr("placeholder.template")
        self.template_placeholder_active = False
        self.advanced_panel_visible = False

        root.title(window_title or APP_TITLE_AND_AUTHOR)
        root.minsize(self.COLLAPSED_WIDTH, self.WINDOW_HEIGHT)
        root.protocol("WM_DELETE_WINDOW", self._close)

        self.menu_bar = tk.Menu(root, tearoff=False)
        self.language_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.language_code_var = tk.StringVar(value=I18N.language)
        self.language_codes: list[str] = []
        for code, name in I18N.language_choices():
            self.language_codes.append(code)
            self.language_menu.add_radiobutton(
                label=name,
                variable=self.language_code_var,
                value=code,
                command=lambda selected=code: self._switch_language(selected),
            )
        self.menu_bar.add_cascade(
            label=tr("app.language"), menu=self.language_menu
        )
        root.configure(menu=self.menu_bar)

        # Visually separate the native menu bar from the application content.
        self.menu_separator = ttk.Separator(root, orient=tk.HORIZONTAL)
        self.menu_separator.pack(fill=tk.X, pady=(0, 1))

        # 首次启动时居中；语言切换仅原地更新文本，不会再次执行这里。
        root.withdraw()
        root.update_idletasks()
        window_width = self.COLLAPSED_WIDTH
        window_height = self.WINDOW_HEIGHT
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        root.deiconify()

        # 主容器，左右布局
        self.main_container = ttk.Frame(root)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # 左侧主面板（固定宽度，不跟随窗口拉伸）
        frame = ttk.Frame(
            self.main_container,
            padding=20,
            width=self.COLLAPSED_WIDTH,
            height=self.CONTENT_HEIGHT,
        )
        frame.place(x=0, y=0, width=self.COLLAPSED_WIDTH, relheight=1)
        frame.grid_propagate(False)  # 主页面固定宽度，不受翻译文本长度影响
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, minsize=118)
        frame.rowconfigure(7, weight=1)  # 日志框行可拉伸

        self.link_label = ttk.Label(frame, text=tr("field.link"))
        self.link_label.grid(row=1, column=0, sticky=tk.W, pady=6)
        self.link_var = tk.StringVar(value="https://www.grimtools.com/calc/")
        self.link_entry = ttk.Entry(frame, textvariable=self.link_var)
        self.link_entry.grid(row=1, column=1, sticky=tk.EW, pady=6)
        self.advanced_button = ttk.Button(
            frame, text=tr("field.advanced"), command=self._toggle_advanced_panel
        )
        self.advanced_button.grid(row=1, column=2, sticky=tk.EW, padx=(6, 0), pady=6)

        self.name_label = ttk.Label(frame, text=tr("field.character_name"))
        self.name_label.grid(row=2, column=0, sticky=tk.W, pady=6)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(frame, textvariable=self.name_var, width=32)
        self.name_entry.grid(row=2, column=1, sticky=tk.EW, pady=6)
        self.name_entry.bind("<Return>", lambda _event: self.start_generation())

        # 记住名称按钮
        self.remember_name_var = tk.BooleanVar()
        self.remember_name_button = ttk.Button(
            frame, text=tr("field.remember_name"), command=self._on_remember_name_toggle
        )
        self.remember_name_button.grid(row=2, column=2, sticky=tk.EW, padx=(6, 0), pady=6)

        # 性别和材料选项行
        options_frame = ttk.Frame(frame)
        options_frame.grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=6)
        
        self.gender_label = ttk.Label(options_frame, text=tr("field.gender"))
        self.gender_label.pack(side=tk.LEFT, padx=(0, 10))
        self.gender_var = tk.StringVar(value="male")
        self.male_radio = ttk.Radiobutton(
            options_frame, text=tr("field.male"), variable=self.gender_var, value="male"
        )
        self.male_radio.pack(side=tk.LEFT, padx=(0, 10))
        self.female_radio = ttk.Radiobutton(
            options_frame, text=tr("field.female"), variable=self.gender_var, value="female"
        )
        self.female_radio.pack(side=tk.LEFT, padx=(0, 20))
        
        self.keep_materials_var = tk.BooleanVar(value=True)
        self.keep_materials_check = ttk.Checkbutton(
            options_frame, text=tr("field.materials"), variable=self.keep_materials_var
        )
        self.keep_materials_check.pack(side=tk.LEFT, padx=(0, 15))
        
        self.keep_iron_var = tk.BooleanVar(value=True)
        self.keep_iron_check = ttk.Checkbutton(
            options_frame, text=tr("field.iron"), variable=self.keep_iron_var
        )
        self.keep_iron_check.pack(side=tk.LEFT)

        self.template_label = ttk.Label(frame, text=tr("field.template"))
        self.template_label.grid(row=4, column=0, sticky=tk.W, pady=6)
        self.template_entry = ttk.Entry(frame, textvariable=self.template_var)
        self.template_entry.grid(row=4, column=1, sticky=tk.EW, pady=6)
        self.template_browse_button = ttk.Button(
            frame, text=tr("common.browse"), command=self._browse_template
        )
        self.template_browse_button.grid(row=4, column=2, sticky=tk.EW, padx=(6, 0), pady=6)
        
        # 设置占位符提示
        self._setup_template_placeholder()

        self.output_label = ttk.Label(frame, text=tr("field.output"))
        self.output_label.grid(row=5, column=0, sticky=tk.W, pady=6)
        self.output_var = tk.StringVar()
        self.output_entry = ttk.Entry(frame, textvariable=self.output_var)
        self.output_entry.grid(row=5, column=1, sticky=tk.EW, pady=6)
        self.output_browse_button = ttk.Button(
            frame, text=tr("common.browse"), command=self._browse_output
        )
        self.output_browse_button.grid(row=5, column=2, sticky=tk.EW, padx=(6, 0), pady=6)
        
        # 设置输出目录占位符
        self.output_placeholder = tr("placeholder.output")
        self.output_placeholder_active = False
        self._setup_output_placeholder()

        # 加载保存的配置（必须在所有变量初始化之后）
        self._load_config()

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=6, column=0, columnspan=3, sticky=tk.EW, pady=6)
        self.generate_button = ttk.Button(
            button_frame, text=tr("action.generate"), command=self.start_generation
        )
        self.generate_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(
            button_frame,
            text=tr("action.open_output"),
            command=self.open_output,
            state=tk.DISABLED,
        )
        self.open_button.pack(side=tk.LEFT, padx=(10, 0))
        self.progress = ttk.Progressbar(button_frame, mode="determinate", length=180, maximum=100)
        self.progress.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(20, 0))

        self.log_frame = ttk.LabelFrame(frame, text=tr("log.title"), padding=8)
        self.log_frame.grid(row=7, column=0, columnspan=3, sticky=tk.NSEW, pady=6)
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(
            self.log_frame,
            height=10,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Microsoft YaHei UI", 9),
        )
        scrollbar = ttk.Scrollbar(self.log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)

        # 右侧高级面板（初始隐藏）
        self.advanced_panel = ttk.LabelFrame(
            self.main_container, text=tr("advanced.title"), padding=10
        )
        self.slot_seed_entries: dict[str, ttk.Entry] = {}
        self.slot_crafting_combos: dict[str, ttk.Combobox] = {}

        # 用 Canvas + Scrollbar 实现可滚动的面板
        canvas = tk.Canvas(self.advanced_panel, highlightthickness=0, width=280)
        scrollbar_adv = ttk.Scrollbar(self.advanced_panel, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        scroll_window = canvas.create_window((0, 0), window=scroll_frame, anchor=tk.NW)
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(scroll_window, width=event.width),
        )
        canvas.configure(yscrollcommand=scrollbar_adv.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_adv.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮支持
        def _on_mousewheel(event: tk.Event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # 标题行
        self.advanced_slot_header = ttk.Label(scroll_frame, text=tr("advanced.slot"), font=("Microsoft YaHei UI", 9, "bold"))
        self.advanced_slot_header.grid(
            row=0, column=0, sticky=tk.W, pady=(0, 4), padx=(0, 4)
        )
        self.advanced_seed_header = ttk.Label(scroll_frame, text=tr("advanced.seed"), font=("Microsoft YaHei UI", 9, "bold"))
        self.advanced_seed_header.grid(
            row=0, column=1, sticky=tk.W, pady=(0, 4), padx=(0, 4)
        )
        self.advanced_crafting_header = ttk.Label(scroll_frame, text=tr("advanced.crafting"), font=("Microsoft YaHei UI", 9, "bold"))
        self.advanced_crafting_header.grid(
            row=0, column=2, sticky=tk.W, pady=(0, 4)
        )

        crafting_display_names = [opt[0] for opt in self.crafting_bonus_options]

        # 输入验证：只允许十六进制字符（0-9, a-f, A-F）
        hex_validate_cmd = (scroll_frame.register(self._validate_hex_input), "%P")
        self.slot_labels: dict[str, ttk.Label] = {}
        for i, slot in enumerate(SLOT_ORDER):
            row = i + 1
            label_text = SLOT_LABELS.get(slot, slot)
            slot_label = ttk.Label(scroll_frame, text=label_text)
            slot_label.grid(
                row=row, column=0, sticky=tk.W, pady=2, padx=(0, 4)
            )
            self.slot_labels[slot] = slot_label
            # 种子输入框（十六进制输入）
            entry = ttk.Entry(
                scroll_frame, textvariable=self.slot_seed_vars[slot], width=10,
                validate="key", validatecommand=hex_validate_cmd,
            )
            entry.grid(row=row, column=1, sticky=tk.EW, pady=2, padx=(0, 4))
            self.slot_seed_entries[slot] = entry

            # 锻造奖励下拉框
            combo = ttk.Combobox(
                scroll_frame,
                textvariable=self.slot_crafting_vars[slot],
                values=crafting_display_names,
                state="readonly",
                width=16,
            )
            combo.set("")  # 默认空
            combo.grid(row=row, column=2, sticky=tk.EW, pady=2)
            self.slot_crafting_combos[slot] = combo

        scroll_frame.columnconfigure(1, weight=0)
        scroll_frame.columnconfigure(2, weight=1)



        # 初始显示占位符
        self._show_template_placeholder()

        self._after_id = root.after(100, self._process_events)
        self.link_entry.selection_range(0, tk.END)
        self.link_entry.focus_set()

    @staticmethod
    def _validate_hex_input(value: str) -> bool:
        """验证输入是否为合法的十六进制格式"""
        if not value:
            return True
        # 最多8位十六进制（最大值 0xFFFFFFFF）
        if len(value) > 8:
            return False
        return all(c in "0123456789abcdefABCDEF" for c in value)

    def _build_crafting_options(self) -> list[tuple[str, str]]:
        """构建锻造奖励选项列表：(显示名称, 路径)"""
        options = [("", "")]  # 空选项
        for path, info in sorted(self.crafting_bonus_data.items()):
            default_name = info.get("display_name", path.split("/")[-1])
            display_name = I18N.crafting_name(path, default_name)
            options.append((display_name, path))
        return options

    def _switch_language(self, code: str) -> None:
        if not code or code == I18N.language:
            return
        try:
            config = {}
            if CONFIG_FILE.exists():
                config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            config["language"] = code
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not I18N.set_language(code):
            return
        refresh_translated_constants()
        self.language_code_var.set(code)
        self._apply_translations()

    def _apply_translations(self) -> None:
        """Update widget text in place without recreating or flashing the window."""
        self.root.title(APP_TITLE_AND_AUTHOR)
        self.menu_bar.entryconfigure(0, label=tr("app.language"))
        language_names = dict(I18N.language_choices())
        for index, code in enumerate(self.language_codes):
            self.language_menu.entryconfigure(index, label=language_names.get(code, code))

        widget_texts = (
            (self.link_label, "field.link"),
            (self.advanced_button, "field.advanced"),
            (self.name_label, "field.character_name"),
            (self.gender_label, "field.gender"),
            (self.male_radio, "field.male"),
            (self.female_radio, "field.female"),
            (self.keep_materials_check, "field.materials"),
            (self.keep_iron_check, "field.iron"),
            (self.template_label, "field.template"),
            (self.template_browse_button, "common.browse"),
            (self.output_label, "field.output"),
            (self.output_browse_button, "common.browse"),
            (self.generate_button, "action.generate"),
            (self.open_button, "action.open_output"),
            (self.log_frame, "log.title"),
            (self.advanced_panel, "advanced.title"),
            (self.advanced_slot_header, "advanced.slot"),
            (self.advanced_seed_header, "advanced.seed"),
            (self.advanced_crafting_header, "advanced.crafting"),
        )
        for widget, key in widget_texts:
            widget.configure(text=tr(key))
        self._update_remember_button_text()

        if self.template_placeholder_active:
            self.template_placeholder = tr("placeholder.template")
            self.template_entry.delete(0, tk.END)
            self.template_entry.insert(0, self.template_placeholder)
        else:
            self.template_placeholder = tr("placeholder.template")
        if self.output_placeholder_active:
            self.output_placeholder = tr("placeholder.output")
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, self.output_placeholder)
        else:
            self.output_placeholder = tr("placeholder.output")

        old_name_to_path = {
            name: path for name, path in self.crafting_bonus_options if name
        }
        selected_paths = {
            slot: old_name_to_path.get(variable.get(), "")
            for slot, variable in self.slot_crafting_vars.items()
        }
        self.crafting_bonus_options = self._build_crafting_options()
        crafting_names = [name for name, _path in self.crafting_bonus_options]
        path_to_name = {
            path: name for name, path in self.crafting_bonus_options if path
        }
        for slot, combo in self.slot_crafting_combos.items():
            combo.configure(values=crafting_names)
            self.slot_crafting_vars[slot].set(
                path_to_name.get(selected_paths.get(slot, ""), "")
            )
        for slot, label in self.slot_labels.items():
            label.configure(text=SLOT_LABELS.get(slot, slot))

    def _setup_template_placeholder(self) -> None:
        """设置模板输入框的占位符提示"""
        self.template_entry.bind("<FocusIn>", self._on_template_focus_in)
        self.template_entry.bind("<FocusOut>", self._on_template_focus_out)

    def _load_config(self) -> None:
        """加载保存的配置"""
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                saved_name = config.get("character_name", "")
                remember = config.get("remember_name", False)
                if remember and saved_name:
                    self.name_var.set(saved_name)
                    self.remember_name_var.set(True)
                # 加载性别和材料选项
                gender = config.get("gender", "male")
                self.gender_var.set(gender)
                keep_materials = config.get("keep_materials", True)
                self.keep_materials_var.set(keep_materials)
                keep_iron = config.get("keep_iron", True)
                self.keep_iron_var.set(keep_iron)
                # 加载模板目录
                saved_template = config.get("template_directory", "")
                if saved_template:
                    self.template_var.set(saved_template)
                    self.template_entry.delete(0, tk.END)
                    self.template_entry.insert(0, saved_template)
                    self.template_placeholder_active = False
                    self.template_entry.configure(foreground="black")
                # 加载输出目录
                saved_output = config.get("output_directory", "")
                if saved_output:
                    self.output_var.set(saved_output)
                    self.output_entry.delete(0, tk.END)
                    self.output_entry.insert(0, saved_output)
                    self.output_placeholder_active = False
                    self.output_entry.configure(foreground="black")
            self._update_remember_button_text()
        except (json.JSONDecodeError, OSError):
            pass

    def _save_config(self) -> None:
        """保存配置到文件"""
        try:
            config = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
            config["remember_name"] = self.remember_name_var.get()
            if self.remember_name_var.get():
                config["character_name"] = self.name_var.get().strip()
            # 保存性别和材料选项
            config["gender"] = self.gender_var.get()
            config["keep_materials"] = self.keep_materials_var.get()
            config["keep_iron"] = self.keep_iron_var.get()
            # 保存模板目录
            template_input = self.template_var.get().strip()
            if template_input and not self.template_placeholder_active:
                config["template_directory"] = template_input
            # 保存输出目录
            output_input = self.output_var.get().strip()
            if output_input and not self.output_placeholder_active:
                config["output_directory"] = output_input
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _on_remember_name_toggle(self) -> None:
        """记住名称按钮点击时切换状态"""
        self.remember_name_var.set(not self.remember_name_var.get())
        self._update_remember_button_text()
        self._save_config()

    def _update_remember_button_text(self) -> None:
        """更新记住按钮的文本"""
        if self.remember_name_var.get():
            self.remember_name_button.configure(text=tr("field.remember_name_on"))
        else:
            self.remember_name_button.configure(text=tr("field.remember_name_off"))

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
        # 如果有保存的值，恢复显示（无论占位符是否激活）
        saved_value = self.template_var.get()
        if saved_value:
            self.template_entry.delete(0, tk.END)
            self.template_entry.insert(0, saved_value)

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
            self.advanced_panel.place_forget()
            self.advanced_panel_visible = False
            self.root.minsize(self.COLLAPSED_WIDTH, self.WINDOW_HEIGHT)
            self.root.geometry(f"{self.COLLAPSED_WIDTH}x{self.WINDOW_HEIGHT}")
        else:
            self.root.minsize(self.EXPANDED_WIDTH, self.WINDOW_HEIGHT)
            self.root.geometry(f"{self.EXPANDED_WIDTH}x{self.WINDOW_HEIGHT}")
            self.advanced_panel.place(
                x=self.COLLAPSED_WIDTH,
                y=20,
                relwidth=1,
                width=-(self.COLLAPSED_WIDTH + 10),
                relheight=1,
                height=-40,
            )
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
            title=tr("dialog.select_template"),
            initialdir=initial_dir,
            parent=self.root,
        )
        
        if directory:
            template_path = Path(directory)
            # 验证模板目录是否包含 player.gdc 文件
            if not (template_path / "player.gdc").is_file():
                messagebox.showwarning(
                    APP_TITLE,
                    tr("error.selected_template_invalid", path=template_path),
                    parent=self.root,
                )
                return
            # 隐藏占位符并设置选择的路径
            self._hide_template_placeholder()
            self.template_var.set(str(template_path))

    def _browse_output(self) -> None:
        """打开文件对话框选择输出目录"""
        current_output = self.output_var.get()
        # 如果当前显示的是占位符或为空，使用用户文档目录作为初始目录
        if not current_output or self.output_placeholder_active:
            # 使用用户的文档目录，如果不存在则使用用户主目录
            documents_dir = Path.home() / "Documents"
            if not documents_dir.is_dir():
                documents_dir = Path.home()
            initial_dir = str(documents_dir)
        else:
            initial_dir = str(Path(current_output).parent)
        
        directory = filedialog.askdirectory(
            title=tr("dialog.select_output"),
            initialdir=initial_dir,
            parent=self.root,
        )
        
        if directory:
            # 隐藏占位符并设置选择的路径
            self._hide_output_placeholder()
            self.output_var.set(str(directory))

    def _setup_output_placeholder(self) -> None:
        """设置输出目录输入框的占位符提示"""
        self.output_entry.bind("<FocusIn>", self._on_output_focus_in)
        self.output_entry.bind("<FocusOut>", self._on_output_focus_out)
        # 初始显示占位符
        self._show_output_placeholder()

    def _show_output_placeholder(self) -> None:
        """显示输出目录占位符提示"""
        if not self.output_var.get() and not self.output_placeholder_active:
            self.output_placeholder_active = True
            self.output_entry.configure(foreground="gray")
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, self.output_placeholder)

    def _hide_output_placeholder(self) -> None:
        """隐藏输出目录占位符提示"""
        if self.output_placeholder_active:
            self.output_placeholder_active = False
            self.output_entry.configure(foreground="black")
            self.output_entry.delete(0, tk.END)
        # 如果有保存的值，恢复显示（无论占位符是否激活）
        saved_value = self.output_var.get()
        if saved_value:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, saved_value)

    def _on_output_focus_in(self, event) -> None:
        """输出目录输入框获得焦点时"""
        if self.output_placeholder_active:
            self._hide_output_placeholder()

    def _on_output_focus_out(self, event) -> None:
        """输出目录输入框失去焦点时"""
        if not self.output_var.get():
            self._show_output_placeholder()

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
        self.menu_bar.entryconfigure(0, state=tk.DISABLED if running else tk.NORMAL)
        entry_state = tk.DISABLED if running else tk.NORMAL
        combo_state = "disabled" if running else "readonly"
        for entry in self.slot_seed_entries.values():
            entry.configure(state=entry_state)
        for combo in self.slot_crafting_combos.values():
            combo.configure(state=combo_state)
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
            messagebox.showwarning(APP_TITLE, I18N.message(exc), parent=self.root)
            self.name_entry.focus_set()
            return
        if not link or link == "https://www.grimtools.com/calc/":
            messagebox.showwarning(APP_TITLE, tr("error.link_required"), parent=self.root)
            self.link_entry.focus_set()
            return
        
        # 验证模板目录
        template_input = self.template_var.get().strip()
        # 如果输入框显示占位符或为空，使用默认模板
        if not template_input or self.template_placeholder_active:
            template_path = data_directory() / "_template"
            # 恢复占位符显示
            self._show_template_placeholder()
        else:
            template_path = Path(template_input)
            if not template_path.is_dir():
                messagebox.showwarning(
                    APP_TITLE,
                    tr("error.template_missing", path=template_path),
                    parent=self.root,
                )
                self.template_entry.focus_set()
                return
            if not (template_path / "player.gdc").is_file():
                messagebox.showwarning(
                    APP_TITLE,
                    tr("error.template_invalid", path=template_path),
                    parent=self.root,
                )
                self.template_entry.focus_set()
                return

        # 验证输出目录
        output_input = self.output_var.get().strip()
        # 如果输入框显示占位符或为空，使用默认输出目录
        if not output_input or self.output_placeholder_active:
            output_root = writable_directory() / "output"
            # 恢复占位符显示
            self._show_output_placeholder()
        else:
            output_root = Path(output_input)
            if not output_root.is_dir():
                messagebox.showwarning(
                    APP_TITLE,
                    tr("error.output_missing", path=output_root),
                    parent=self.root,
                )
                self.output_entry.focus_set()
                return

        output_directory = output_root / f"_{name}"
        overwrite = False
        if output_directory.exists():
            overwrite = messagebox.askyesno(
                APP_TITLE,
                tr("confirm.overwrite", path=output_directory),
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
                value = int(raw, 16)  # 十六进制
                if value < 0 or value > 0xFFFFFFFF:
                    raise ValueError("超出范围")
                slot_seeds[slot] = value
            except ValueError:
                label = SLOT_LABELS.get(slot, slot)
                messagebox.showwarning(
                    APP_TITLE,
                    tr("error.seed_invalid", slot=label),
                    parent=self.root,
                )
                self.slot_seed_entries[slot].focus_set()
                return

        # 收集锻造奖励选择
        slot_crafting: dict[str, str] = {}
        for slot in SLOT_ORDER:
            selected_name = self.slot_crafting_vars[slot].get()
            if not selected_name:
                continue
            # 查找对应的路径
            for display_name, path in self.crafting_bonus_options:
                if display_name == selected_name:
                    slot_crafting[slot] = path
                    break

        self.last_output = None
        self._clear_log()
        self._set_running(True)
        is_default_template = (template_path == data_directory() / "_template")
        gender = self.gender_var.get()
        keep_materials = self.keep_materials_var.get()
        keep_iron = self.keep_iron_var.get()
        worker = threading.Thread(
            target=self._generate,
            args=(link, name, template_path, is_default_template, output_root, overwrite, slot_seeds or None, slot_crafting or None, gender, keep_materials, keep_iron),
            daemon=True,
        )
        worker.start()

    def _generate(
        self, link: str, name: str, template_directory: Path, is_default_template: bool, output_root: Path, overwrite: bool, slot_seeds: dict[str, int] | None, slot_crafting: dict[str, str] | None, gender: str = "male", keep_materials: bool = True, keep_iron: bool = True
    ) -> None:
        try:
            self.events.put(("progress", 10))
            self.events.put(("log", tr("log.fetch")))
            build = fetch_build(link)
            self.events.put(
                ("log", tr("log.build", build_id=build.build_id, game_version=build.game_version))
            )
            if is_default_template:
                self.events.put(("log", tr("log.default_template")))
            else:
                self.events.put(("log", tr("log.custom_template", path=template_directory)))
            if slot_seeds:
                labels = [SLOT_LABELS.get(s, s) for s in slot_seeds]
                self.events.put(("log", tr("log.custom_seeds", slots=", ".join(labels))))
            if slot_crafting:
                display_by_path = {path: name for name, path in self.crafting_bonus_options}
                labels = [f"{SLOT_LABELS.get(s, s)}->{display_by_path.get(p, p.split('/')[-1])}" for s, p in slot_crafting.items()]
                self.events.put(("log", tr("log.crafting", bonuses=", ".join(labels))))
            
            self.events.put(("progress", 30))
            self.events.put(("log", tr("log.generate")))
            result = generate_save(
                build,
                name,
                template_directory,
                output_root,
                overwrite=overwrite,
                slot_seeds=slot_seeds,
                slot_crafting=slot_crafting,
                male=(gender == "male"),
                keep_materials=keep_materials,
                keep_iron=keep_iron,
            )
            
            self.events.put(("progress", 70))
            self.events.put(("log", tr("log.verified")))
            
            self.events.put(("progress", 90))
            self.events.put(("log", tr("log.success")))
            self.events.put(("log", tr("log.character", name=result.character_name)))
            self.events.put(("log", tr("log.class", class_tag=result.class_tag)))
            self.events.put(("log", tr("log.level", level=result.level)))
            self.events.put(
                (
                    "log",
                    tr(
                        "log.counts",
                        equipment=result.equipment_count,
                        skills=result.skill_count,
                        devotions=result.devotion_count,
                    ),
                )
            )
            for warning in result.warnings:
                self.events.put(("log", tr("log.warning", message=I18N.message(warning))))
            self.events.put(("progress", 100))
            self.events.put(("success", result.output_directory))
            self.events.put(("log", tr("log.output", path=result.output_directory)))
        except (GenerationError, GrimToolsError, SaveFormatError, OSError) as exc:
            self.events.put(("error", I18N.message(exc)))
        except Exception as exc:  # Keep the packaged GUI from exiting silently.
            self.events.put(("error", tr("error.unexpected", message=exc)))

    def _process_events(self) -> None:
        if self.disposed:
            return
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
                    self._save_config()  # 保存配置（包括记住的名称）
                    messagebox.showinfo(
                        APP_TITLE,
                        tr("dialog.success", path=self.last_output),
                        parent=self.root,
                    )
                elif kind == "error":
                    self._append_log(tr("log.failed", message=value))
                    self._set_running(False)
                    messagebox.showerror(APP_TITLE, str(value), parent=self.root)
        except queue.Empty:
            pass
        if not self.disposed:
            self._after_id = self.root.after(100, self._process_events)

    def open_output(self) -> None:
        if self.last_output and self.last_output.is_dir():
            # 打开输出根目录（output目录），而不是具体的存档目录
            output_root = self.last_output.parent
            if output_root.is_dir():
                os.startfile(output_root)  # type: ignore[attr-defined]

    def _close(self) -> None:
        if self.running and not messagebox.askyesno(
            APP_TITLE, tr("confirm.exit_running"), parent=self.root
        ):
            return
        self._save_config()  # 退出时保存配置
        self.root.destroy()


def main(*, window_title: str = APP_TITLE_AND_AUTHOR) -> int:
    if "--self-test" in sys.argv:
        template = data_directory() / "_template"
        player_file = template / "player.gdc"
        if not player_file.is_file():
            return 2
        CharacterSave.load(player_file)
        if sum(1 for path in template.rglob("*") if path.is_file()) < 1:
            return 3
        return 0

    root = tk.Tk()
    app = SaveGeneratorApp(root, window_title=window_title)
    setattr(root, "_save_generator_app", app)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
