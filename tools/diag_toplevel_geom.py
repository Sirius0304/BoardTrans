# -*- coding: utf-8 -*-
"""观测 Toplevel 的坐标语义：wm geometry 设的值 vs winfo_rootx/winfo_x 的读数。"""
import sys
import time
import tkinter as tk

sys.path.insert(0, r"F:\shunyi")

from app import ui_kit as ui   # noqa: E402

root = tk.Tk()
root.geometry("540x400+900+300")
root.update_idletasks()
root.update()
print("父窗口: rootx/y =", root.winfo_rootx(), root.winfo_rooty(),
      " x/y =", root.winfo_x(), root.winfo_y(),
      " w/h =", root.winfo_width(), root.winfo_height())

top = tk.Toplevel(root)
top.title("t")
top.geometry("206x117")
top.transient(root)
print("\n设置前: mapped =", top.winfo_ismapped())
for i in range(3):
    root.update_idletasks()
    root.update()
    time.sleep(0.12)
    print(f"  轮{i}: mapped={top.winfo_ismapped()} rootx/y={top.winfo_rootx()},{top.winfo_rooty()}"
          f"  x/y={top.winfo_x()},{top.winfo_y()}")

print("\n--- 设 geometry('+1185+661') 之后 ---")
top.geometry("+1185+661")
for i in range(3):
    root.update_idletasks()
    root.update()
    time.sleep(0.12)
    print(f"  轮{i}: mapped={top.winfo_ismapped()} rootx/y={top.winfo_rootx()},{top.winfo_rooty()}"
          f"  x/y={top.winfo_x()},{top.winfo_y()}")

print("\n→ 结论：geometry 的 x/y 与 winfo_rootx 的差值（= 窗口框架宽度）:")
print("   横向:", top.winfo_rootx() - 1185, " 纵向:", top.winfo_rooty() - 661)
top.destroy()
root.destroy()
