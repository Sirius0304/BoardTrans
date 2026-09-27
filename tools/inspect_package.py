# -*- coding: utf-8 -*-
"""探查 .argosmodel 包内部结构（zip 内容清单 + 关键文件定位）。"""
import os
import sys
import zipfile


def main(path: str):
    print(f"包文件: {path}  大小: {os.path.getsize(path)/1024/1024:.1f} MB\n")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        print(f"内部条目数: {len(names)}\n")
        for n in sorted(names):
            info = z.getinfo(n)
            flag = "  <-- 目录" if info.is_dir() else ""
            print(f"  {info.file_size:>12,}  {n}{flag}")
        print("\n--- config.json / metadata 内容 ---")
        for n in names:
            if n.lower().endswith((".json", ".yml", ".yaml")) and z.getinfo(n).file_size < 20000:
                print(f"\n### {n}")
                try:
                    print(z.read(n).decode("utf-8", errors="replace")[:4000])
                except Exception as e:  # noqa: BLE001
                    print(f"读取失败: {e}")


if __name__ == "__main__":
    main(sys.argv[1])
