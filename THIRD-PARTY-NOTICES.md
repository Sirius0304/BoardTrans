# 第三方组件与许可声明

本项目源码以 **MIT 许可证**发布（见 `LICENSE`）。
但项目**分发的成品中还包含第三方组件**，它们各自遵循不同的许可证。
以下是完整清单与署名要求。

> 若你只是使用本软件，无需关心本文件；
> 若你要**再分发**（含打包后的 exe），请保留本文件与 `LICENSE`。

---

## 一、翻译模型（署名是硬性要求）

程序使用的翻译模型来自 **OPUS-MT** 项目，**并非 MIT 许可**：

| 模型 | 来源 | 许可证 | 要求 |
|---|---|---|---|
| 中→英 `zh_en` | OPUS-MT / Helsinki-NLP | **CC-BY 4.0** | **必须署名** |
| 英→日 `en_ja` | OPUS-MT / Helsinki-NLP | **CC-BY 4.0** | **必须署名** |

**原始作者与出处**（CC-BY 4.0 要求保留）：

> Authors: Jörg Tiedemann and Santhosh Thottingal
> Title: "OPUS-MT — Building open translation services for the World"
> Book Title: Proceedings of the 22nd Annual Conference of the European
> Association for Machine Translation (EAMT), Year: 2020, Lisbon, Portugal
>
> The original OPUS model from which this packaged model is derived is
> licensed CC-BY 4.0.

许可证全文：https://creativecommons.org/licenses/by/4.0/

**含义**：CC-BY 4.0 允许商用与再分发，但**必须注明原作者**（如上）。
若你再分发本软件，请一并转发本节内容或保留模型目录内的 `README.md`
（模型包内已自带该署名文件）。

---

## 二、Python 依赖

| 组件 | 用途 | 许可证 |
|---|---|---|
| CTranslate2 | 翻译推理引擎 | MIT |
| SentencePiece | 分词器 | Apache-2.0 |
| keyboard | 全局热键钩子 | MIT |
| pyperclip | 剪贴板读写 | BSD |
| Pillow | 图像渲染（自绘界面） | MIT-CMU (HPND) |
| pystray | 系统托盘 | **LGPLv3** |
| PyInstaller | 打包工具（仅构建期） | GPLv2 + 特殊例外 |

### 需要留意的一项：pystray（LGPLv3）

LGPLv3 允许在专有软件中使用**未经修改**的库，前提是使用者有权替换该库版本。
本项目未修改 pystray 源码。

### PyInstaller 的例外条款

PyInstaller 虽是 GPLv2，但附带**明确的例外条款**，允许用它打包非 GPL 程序
而不使被打包程序受 GPL 约束——这正是本项目采用它的原因，符合开源规范。

---

## 三、本项目自身

```
MIT License
Copyright (c) 2026 yz050304 (键译 / BoardTrans) <yz050304@163.com>
https://gitee.com/yz050304/BoardTrans
```

完整条款见根目录 `LICENSE`。
