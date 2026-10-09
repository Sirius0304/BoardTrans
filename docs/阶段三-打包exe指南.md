# 阶段三：打包成 exe · 保姆级指南

## 目标

把程序打包成**一个 exe 文件**，以后双击就能用，**连 Python 都不用装**，也可以直接拷给别人用。

---

## 一键打包

**双击 `3-打包exe.bat`**，然后等 1～3 分钟。

预期输出（最后几行）：

```
22106 INFO: Fixing EXE headers
22252 INFO: Building EXE from EXE-00.toc completed successfully.
22255 INFO: Build complete! The results are available in: F:\shunyi\dist

============================================================
  打包结束。生成的 exe 在 dist 文件夹里：dist\键译.exe
  双击即可运行，不需要安装 Python。
============================================================
```

打包完成后，项目里会多出：

```
F:\shunyi\
├── dist\
│   └── 键译.exe          ← 最终成果，约 50 MB
└── build\                ← 打包临时文件，可以删掉
```

---

## 实测结果（本机已跑通）

| 验证项 | 结果 |
|---|---|
| 打包耗时 | 约 24 秒 |
| 产物大小 | **47.4 MB**（不含设置界面的版本；加了设置界面后为 50.1 MB。均为 Windows 显示值） |
| 打包后能否翻译 | ✅ 实测通过：模型加载 0.12 s，中→英 22 ms/43 ms |
| 打包后能否注册全局热键 | ✅ 实测通过：`热键已注册: alt+j` |
| 打包后能否常驻托盘 | ✅ 实测通过：托盘图标、菜单、通知均正常 |

---

## ⚠️ 打包命令里最关键的一行（如果你要自己改命令）

打包命令必须带 **`--collect-all ctranslate2`**：

```bat
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name "键译" ^
    --icon "app_icon.ico" ^
    --hidden-import pystray._win32 ^
    --collect-all ctranslate2 ^
    --collect-all sentencepiece ^
    --distpath "dist" --workpath "build" --specpath "build" ^
    run_stage2.py
```

> 注（2026-09-28 起）：**现在推荐直接双击项目根的 `3-打包exe.bat`**（它读取 `键译_精简.spec`），
> 产物**直接生成在项目根目录** `键译.exe`，上一版自动归档到 `dist\键译_上一版.exe`。
> 上面这段是最早那版的原始命令记录，其中的 `--distpath "dist"` 已不是现在的输出位置。

**原因（实测踩过的坑）**：`ctranslate2` 的核心是一个 56.5 MB 的原生动态库 `ctranslate2.dll`。
如果不加 `--collect-all ctranslate2`，PyInstaller 可能不把它收进去，
打出来的 exe 只有 46 MB，看起来"打包成功了"，**但一运行就报 DLL 加载失败，根本不能翻译**。
加上这个参数后，exe 会有约 50 MB（DLL 被压缩在包里），运行时正常。

**其它参数的作用**：

| 参数 | 作用 |
|---|---|
| `--onefile` | 打包成单个 exe（而不是一堆文件的文件夹） |
| `--windowed` | 不显示黑色控制台窗口（托盘程序不需要） |
| `--name "键译"` | exe 的名字 |
| `--icon "app_icon.ico"` | 给 exe 设置图标 |
| `--hidden-import pystray._win32` | 托盘库在 Windows 下的后端，PyInstaller 有时分析不到，要手动指定 |
| `--collect-all sentencepiece` | 同样是为了收集分词器的原生模块 |

> 小提醒：`--onefile` 模式下，每次启动都要先把约 50 MB 的内容解压到系统临时目录，
> 所以**首次启动会慢 3～8 秒**（之后有缓存会快一些）。这是正常现象，不是卡死。
> 如果嫌启动慢，可以把 `--onefile` 换成 `--onedir`（生成一个文件夹，启动只要 1 秒，但要整个文件夹一起拷贝）。

---

## 测试打包好的 exe

1. 打开 `F:\shunyi\dist` 文件夹，**双击 `键译.exe`**
2. 首次启动可能需要 5～10 秒（解压 + 加载模型），然后右下角出现蓝色"译"图标
3. 打开记事本，打中文，按 `Alt + J`，验证是否原地变英文

### 想确认 exe 内部引擎是否正常？用这个自检程序

项目里额外提供了一键自检，可在打包环境下逐项验证：

```bat
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --console ^
    --name "引擎自检" --collect-all ctranslate2 --collect-all sentencepiece ^
    --distpath dist --workpath build_selftest --specpath build_selftest selftest.py
```

然后运行 `dist\引擎自检.exe`，正常会输出：

```
  ctranslate2: 4.8.2
  sentencepiece: OK
  数据目录: C:\Users\你的用户名\AppData\Local\BoardTrans
  [en/fast] 你好，今天天气不错。  ->  Hello, it's a nice day.   (22 ms)
  结论：EXE 环境翻译引擎工作正常。
```

---

## 关于翻译模型（重要）

**exe 里不包含翻译模型**（模型有 186 MB，塞进去会让 exe 变成 240 MB，而且后续不好换）。
模型统一放在：

```
C:\Users\你的用户名\AppData\Local\BoardTrans\packages\
```

| 情况 | 表现 |
|---|---|
| 你自己电脑上已经跑过阶段一/二 | 模型早就装好了，**exe 双击直接用，无需任何下载** |
| 把 exe 拷给别人的电脑 | 对方首次启动会自动下载模型（中→英 71 MB，切日文时再下 115 MB），需要联网一次，之后永久离线 |

**想彻底离线分发（连模型一起给别人）**：把下面两个文件夹一起拷过去即可

```
C:\Users\你的用户名\AppData\Local\BoardTrans\packages\        ← 复制到对方同样的位置
```

---

## 打包常见问题

| 现象 | 原因与解决办法 |
|---|---|
| exe 双击没反应，也不报错 | 用 `--onedir` 重新打包，或者把 `--windowed` 临时去掉（改成 `--console`），这样能看到报错信息 |
| exe 启动后没托盘图标 | 首次启动要 5～10 秒解压，稍等；若一直没有，看日志文件 `...\AppData\Local\BoardTrans\logs\shunyi.log` |
| 杀毒软件/Windows Defender 报毒或直接删除 exe | PyInstaller 打包的程序经常被误报（因为它会"模拟键盘输入"，行为特征敏感）。解决：在杀软里把 `dist` 文件夹加白名单；或把 exe 提交给杀软厂商申诉 |
| 打包时报 `ModuleNotFoundError` | 该模块没被 PyInstaller 分析到，用 `--hidden-import 模块名` 补上 |
| exe 有 120 MB+，比预期大 | 检查是不是误把 `--collect-all` 加到了 numpy 等大库上；或者之前打包的 `build` 目录没清理，加 `--clean` 参数 |
| 运行 exe 后打字按快捷键没反应 | 大概率是**安全软件拦截了模拟按键**。运行项目里的 `0-自检.bat`（或打包自检 exe）看第 8 项结果 |

---

## 最终交付清单

打包完成后，你手上会有：

| 文件 | 用途 |
|---|---|
| `dist\键译.exe` | ⭐ 最终成果，双击即用，可拷给别人 |
| `0-自检.bat` / `checkup.py` | 出问题时的一键体检工具 |
| `1-控制台版.bat` / `run_stage1.py` | 阶段一控制台版（排查问题用，出错误能直接看到） |
| `2-托盘版.bat` / `run_stage2.py` | 阶段二脚本版（改代码后不用重新打包就能试） |
| `docs\` | 技术方案 + 三阶段指南 + 常见问题 |
| `app\` | 全部源码 |
| `requirements.txt` | 依赖清单（换电脑重装环境时用） |

`build\`、`build_selftest\`、`.venv\`、`__pycache__` 都是中间产物，可以随时删。
