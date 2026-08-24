# GrimTools 构筑存档生成工具

## 离线授权

采用“用户提供机器码、作者离线签发”的授权方式：

1. 用户首次运行 `GenerateSave.exe`，复制授权窗口显示的机器码并发送给作者。
2. 作者在工程目录执行以下命令：

   ```powershell
   python .\tools\license_issuer.py "用户机器码"
   ```

3. 签发结果默认位于 `artifacts\licenses\GDAG-机器码.lic`。将该 `.lic` 文件发给用户。
4. 用户在授权窗口点击“导入许可证”，以后启动无需再次导入。

用户端许可证安装在 `%LOCALAPPDATA%\GrimDawnArchiveGenerator\license.lic`。许可证绑定 Windows MachineGuid、系统卷序列号和产品标识，复制到其他电脑后无效。Windows 重装或系统盘变化后需要提供新机器码重新签发。

许可证使用 Ed25519 数字签名；用户 EXE 只包含公钥，不包含签发私钥。`license_issuer.py` 保存了签发私钥，拿到该文件的人可以签发任意机器的许可证，因此该文件及整个源码目录仅供作者保存，绝对不要与用户 EXE 一同分发。建议至少使用操作系统账户权限、加密磁盘和私有代码仓库保护源码。

> 纯离线程序仍不能绝对阻止二进制补丁。正式分发时建议再配合 Nuitka/PyArmor、Authenticode 代码签名和安装包 ACL。没有服务器时也无法在线撤销已经签发的许可证。

本工具以 `_template` 完整角色存档为模板，读取 GrimTools 构筑链接，生成一个新的《恐怖黎明》角色目录。模板中的任务、传送点、声望、地图探索等进度保持不变；角色名称、职业、等级属性、技能、星座和装备会按构筑替换。

## 使用方法

构建后直接运行（版本号以 `src\app_version.py` 为准）：

```text
artifacts/releases/GenerateSave-v0.7.0/licensed/GenerateSave-v0.7.0.exe
```

填写 GrimTools 模拟器链接和角色名称，模板目录输入框显示提示"可选择存档模板，未选时使用自带模板"（可选择自定义模板目录，不选择则使用工具自带的 `_template` 模板），点击"生成角色存档"即可。模板已经打包进 EXE，结果保存在 EXE 同目录的 `output/_角色名称` 中。生成成功后，点击"打开输出目录"按钮会打开 `output` 目录（存档根目录），方便查看所有生成的存档。点击"浏览..."按钮选择模板目录时，默认打开用户的文档目录。

如需重新构建 EXE，请先安装依赖，然后选择授权版或免授权版：

```powershell
python -m pip install -r packaging/requirements.txt
.\build_exe.bat licensed
.\build_exe.bat free
```

- `licensed`（或不传参数）：生成 `artifacts\releases\GenerateSave-版本号\licensed\GenerateSave-版本号.exe`，启动时必须校验机器许可证。
- `free`：生成 `artifacts\releases\GenerateSave-版本号\free\GenerateSave-Free-版本号.exe`，完全跳过许可证校验，窗口标题会标记“免授权版”。该版本一旦转发便可被任何人使用，应仅按需提供给可信人员。

每个发布目录都包含 EXE 和 `languages` 目录。软件会优先读取 EXE 同目录下的外置 JSON 语言包，语言包缺项时回退到内置简体中文。可直接修改 JSON 文案或复制现有文件新增语言；`_meta.code` 必须唯一，`strings` 中的占位符（例如 `{path}`、`{version}`）应保留。界面顶部可选择语言并立即生效，切换时会保留当前输入和高级设置。也可在启动前设置 `GENERATESAVE_LANG` 环境变量临时指定语言代码。

## 工程结构

```text
build_exe.bat       主构建入口
README.md           项目说明
src/                GUI 与核心源码
resources/          模板、数据库及语言包
tools/              作者侧许可证工具
tests/              自动化测试
packaging/          构建依赖配置
artifacts/          构建产物、运行输出及许可证
```

## 当前行为

- 支持当前模板使用的 1.3 存档数据块版本。
- 支持不同长度的中文、英文角色名称，并在写入后执行完整回读校验。
- 写入等级属性、职业标记、技能、星座、装备、词缀、组件、附魔和遗物完成奖励。
- 内置 Grim Dawn 1.3.0.0 当前全部 989 个飞升词缀映射，可将 GrimTools 短 ID 转换为存档需要的 `.dbr` 记录；不会把短 ID 或无效随机值直接写入存档。
- 双手武器会自动清空副手。
- 生成的 `player.gdc`、`.bak`、`.g00`、`.g01`、`.g02` 内容保持同步。
- 保留模板的地图、任务、声望、仓库及背包内容。
- 为避免经验值和等级不一致，当前只接受与模板相同的 100 级构筑。
- 支持在 GUI 中浏览选择自定义模板目录。
- GUI 进度条显示实际生成进度：读取构筑（10%）、写入角色数据（30%）、回读校验（70%）、生成成功（100%）。

## 已知限制

- GrimTools 使用的是网页内部接口；如果网站调整接口或字段，需要同步更新工具。
- 装备随机种子由工具确定性生成，实际浮动数值不保证与模拟器选择的平均/最大显示完全一致。
- GrimTools 的展开接口暂不返回飞升词缀的 `.dbr` 路径。当前 1.3.0.0 数据已经完整收录；如果后续游戏版本新增了 ID，工具会停止并明确报错，避免生成无法解析或静默丢失词缀的存档。
- 已支持测试构筑使用的星座触发器。遇到尚未收录的星座触发器时，工具会生成存档并显示未绑定警告。
- 快捷栏会清除模板中不属于新构筑的技能；当前不会完整复刻模拟器快捷栏布局。
- 结构回读成功只能证明存档格式、加密和校验一致，最终仍需在目标游戏版本中做一次实际导入测试。

## 测试

```powershell
python -m unittest discover .\tests -v
```
