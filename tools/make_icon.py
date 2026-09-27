# -*- coding: utf-8 -*-
"""生成打包用的 .ico 图标（多尺寸）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import icon    # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_icon.ico")

img = icon.make_icon(256, True)
img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("已生成:", OUT, os.path.getsize(OUT), "bytes")
