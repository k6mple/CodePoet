"""R3 前置探针：Tk/Turtle 能不能在这台机器上稳定导出 PNG。

细则要求「先证明目标 Tk/导出依赖可部署，再写适配器」，所以这里只做三件事：
能不能开出离屏画布、能不能导出 EPS、EPS 能不能变成 PNG。任何一步失败就
如实记录并给出「停止」判定，不硬凑环境。

判定标准（细则 R3 段）：几何必须由 Turtle 产生，Pillow 只做合成转换。
用 Pillow 直接画整图 = 伪称 Turtle，文档明令禁止，不在备选里。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / ".smoke" / "probe-turtle"  # 项目内隔离目录，已在 .gitignore
OUT.mkdir(parents=True, exist_ok=True)

report: list[str] = []


def note(line: str) -> None:
    print(line)
    report.append(line)


def main() -> int:
    note(f"# 环境：{platform.platform()}｜Python {platform.python_version()}")
    note("")

    # --- 1. tkinter 能不能导入并开画布 ---
    note("## 1. Tk 画布")
    try:
        import tkinter as tk
        from turtle import RawTurtle

        root = tk.Tk()
        root.withdraw()  # 不弹窗，尽量接近无头
        canvas = tk.Canvas(root, width=800, height=600, background="#FFFFFF")
        canvas.pack()
        turtle = RawTurtle(canvas)
        turtle.penup()
        turtle.goto(-100, 0)
        turtle.pendown()
        turtle.circle(100)  # 真 Turtle 几何
        turtle.penup()
        turtle.goto(0, 150)
        turtle.pendown()
        turtle.forward(200)
        root.update()
        note(f"  OK 离屏画布可用，Tk {tk.TkVersion}，已用 RawTurtle 画圆+线")
    except Exception as exc:  # noqa: BLE001
        note(f"  FAIL 画布不可用：{type(exc).__name__}: {exc}")
        note("  → 判定：Turtle 路径在本机不可用，R3 应延期")
        OUT.joinpath("report.md").write_text("\n".join(report), encoding="utf-8")
        return 1

    # --- 2. 导出 EPS ---
    note("")
    note("## 2. PostScript 导出")
    eps = OUT / "probe.eps"
    start = time.perf_counter()
    try:
        canvas.postscript(file=str(eps), colormode="color", width=800, height=600)
        elapsed = (time.perf_counter() - start) * 1000
        size = eps.stat().st_size
        head = eps.read_bytes()[:24]
        note(f"  OK 生成 {eps.name}，{size} 字节，{elapsed:.0f}ms，头 {head!r}")
    except Exception as exc:  # noqa: BLE001
        note(f"  FAIL 导出失败：{type(exc).__name__}: {exc}")
        OUT.joinpath("report.md").write_text("\n".join(report), encoding="utf-8")
        return 1
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass

    # --- 3. EPS → PNG 需要栅格化器 ---
    note("")
    note("## 3. EPS → PNG 栅格化")
    gs = None
    for name in ("gswin64c", "gswin32c", "gs", "gswin64c.exe"):
        found = shutil.which(name)
        if found:
            gs = found
            break
    note(f"  Ghostscript: {gs or '未安装'}")

    if gs:
        png = OUT / "probe-gs.png"
        proc = subprocess.run(
            [gs, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m",
             "-r144", f"-sOutputFile={png}", str(eps)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode == 0 and png.exists():
            note(f"  OK Ghostscript 转出 {png.name}，{png.stat().st_size} 字节")
            return probe_verdict_ok()
        note(f"  FAIL Ghostscript 退出码 {proc.returncode}: {proc.stderr[:200]}")
        return probe_verdict_stop("Ghostscript 存在但转换失败")

    # 没有 Ghostscript：实测 Pillow 会不会自己成功（文档说它依赖 GS，但要看真报错）
    note("  实测 Pillow 直接打开 EPS：")
    try:
        from PIL import Image

        with Image.open(eps) as image:
            image.load()
            note(f"  PASS 意外成功：{image.size} {image.mode}")
            png = OUT / "probe-pil.png"
            image.save(png)
            note(f"  已存 {png.name}")
            return probe_verdict_ok()
    except Exception as exc:  # noqa: BLE001
        note(f"  FAIL Pillow 打不开：{type(exc).__name__}: {exc}")

    return probe_verdict_stop("缺少 EPS 栅格化器（Ghostscript）")


def probe_verdict_ok() -> int:
    note("")
    note("## 判定：可以继续 R3")
    OUT.joinpath("report.md").write_text("\n".join(report), encoding="utf-8")
    return 0


def probe_verdict_stop(reason: str) -> int:
    note("")
    note(f"## 判定：Turtle 路径在本机不可行（{reason}）")
    note("")
    note("按细则「无法稳定导出则延期」，R3 的 8h 预算应转投 Q1/集成。")
    note("若要继续，需要的**额外**条件（本机都没有）：")
    note("  * Ghostscript（`gswin64c`）—— EPS 是 Tk 画布唯一可导出的中间格式，")
    note("    Pillow 的 EPS 解码器也依赖它，没有它 EPS 就是死文件；")
    note("  * 目标部署环境同样的 Tk + 字体 + 权限，本机装上不等于比赛机可部署。")
    OUT.joinpath("report.md").write_text("\n".join(report), encoding="utf-8")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
