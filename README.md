# GrimTools 构筑存档生成工具

本工具以 `_template` 完整角色存档为模板，读取 GrimTools 构筑链接，生成一个新的《恐怖黎明》角色目录。模板中的任务、传送点、声望、地图探索等进度保持不变；角色名称、职业、等级属性、技能、星座和装备会按构筑替换。

## 使用方法

### Windows 图形界面

直接运行：

```text
dist/GenerateSave.exe
```

填写 GrimTools 模拟器链接和角色名称，点击“生成角色存档”即可。模板已经打包进 EXE，结果保存在 EXE 同目录的 `output/_角色名称` 中。

如需重新构建 EXE，请先安装 PyInstaller，然后运行 `build_exe.bat`：

```powershell
python -m pip install pyinstaller
.\build_exe.bat
```

### 命令行

在工具目录运行：

```powershell
python .\GenerateSave.py `
  "https://www.grimtools.com/calc/NXl7KPWN" `
  --name "构筑测试"
```

也可以使用批处理入口：

```bat
GenerateSave.bat "https://www.grimtools.com/calc/NXl7KPWN" --name "构筑测试"
```

默认输出位置：

```text
tools/Archive-Generator/output/_构筑测试/
```

将整个 `_构筑测试` 目录复制到游戏的本地角色存档目录即可。复制前请退出游戏并备份原存档；使用本地存档测试时，还应避免 Steam 云存档立刻覆盖本地文件。

## 参数

```text
link                 GrimTools 构筑链接或构筑 ID
--name, -n           自定义角色名称（必填）
--template           自定义模板目录，默认使用 _template
--output, -o         输出根目录，默认使用 output
--force              覆盖已经存在的同名输出目录
```

## 当前行为

- 支持当前模板使用的 1.3 存档数据块版本。
- 支持不同长度的中文、英文角色名称，并在写入后执行完整回读校验。
- 写入等级属性、职业标记、技能、星座、装备、词缀、组件、附魔和遗物完成奖励。
- 内置 Grim Dawn 1.3.0.0 当前全部 989 个升华词缀映射，可将 GrimTools 短 ID 转换为存档需要的 `.dbr` 记录；不会把短 ID 或无效随机值直接写入存档。
- 双手武器会自动清空副手。
- 生成的 `player.gdc`、`.bak`、`.g00`、`.g01`、`.g02` 内容保持同步。
- 保留模板的地图、任务、声望、仓库及背包内容。
- 为避免经验值和等级不一致，当前只接受与模板相同的 100 级构筑。

## 已知限制

- GrimTools 使用的是网页内部接口；如果网站调整接口或字段，需要同步更新工具。
- 装备随机种子由工具确定性生成，实际浮动数值不保证与模拟器选择的平均/最大显示完全一致。
- GrimTools 的展开接口暂不返回升华词缀的 `.dbr` 路径。当前 1.3.0.0 数据已经完整收录；如果后续游戏版本新增了 ID，工具会停止并明确报错，避免生成无法解析或静默丢失词缀的存档。
- 已支持测试构筑使用的星座触发器。遇到尚未收录的星座触发器时，工具会生成存档并显示未绑定警告。
- 快捷栏会清除模板中不属于新构筑的技能；当前不会完整复刻模拟器快捷栏布局。
- 结构回读成功只能证明存档格式、加密和校验一致，最终仍需在目标游戏版本中做一次实际导入测试。

## 测试

```powershell
python -m unittest discover .\tests -v
```
