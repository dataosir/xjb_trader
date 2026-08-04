#!/usr/bin/env python3
"""TEA 一键跨平台打包。

    python3 packaging/build.py

自动按当前平台选产物形态（PyInstaller 不支持交叉编译，只能在目标平台上各跑一次）：

- macOS   → dist/TEA.app
- Windows → dist/TEA.exe

每次运行都会先删掉 dist/ 与 build/ 再重新生成，不用担心上一轮残留；
想留旧产物就先自己把 dist/ 拷走。

打完的程序把运行时数据写在 ~/.tea/（自动创建），不再往 .app / .exe 内部写 ——
那里是只读的。想换位置就设 TEA_HOME 环境变量。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import zipfile
from typing import List, NoReturn, Optional

# 不管从哪个工作目录调用，都以本文件的位置定位项目根（packaging/ 的上一层）
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC = HERE / "tea.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

APP_NAME = "TEA"
SUPPORTED = {"darwin": "macOS", "win32": "Windows"}


# ---------------------------------------------------------------- 输出
def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"  ⏳ {msg}")


def ok(msg: str) -> None:
    say(f"  ✓ {msg}")


def die(msg: str, *hints: str) -> NoReturn:
    say(f"  ✗ {msg}")
    for h in hints:
        say(f"    {h}")
    raise SystemExit(1)


# ---------------------------------------------------------------- 环境检查
def check_python() -> None:
    if sys.version_info < (3, 8):
        die(f"Python 版本过低：{sys.version.split()[0]}", "TEA 需要 Python 3.8 以上")
    ok(f"Python {sys.version.split()[0]}")


def check_sources() -> None:
    """确认在完整的项目树里，而不是只捞了 packaging/ 出来。"""
    pkg = ROOT / "tea"
    entry = pkg / "__main__.py"
    if not pkg.is_dir():
        die(f"找不到源码目录 {pkg}", "请确认 packaging/ 与 tea/ 在同一个项目根下")
    if not entry.is_file():
        die(f"找不到打包入口 {entry}")
    if not (ROOT / "pyproject.toml").is_file():
        die(f"找不到 {ROOT / 'pyproject.toml'}", "项目根不完整，无法确认依赖")
    if not SPEC.is_file():
        die(f"找不到打包规格 {SPEC}")
    n = len(list(pkg.rglob("*.py")))
    ok(f"源码就位：tea/（{n} 个模块）、pyproject.toml、packaging/tea.spec")


def check_deps() -> None:
    """运行时零第三方依赖，所以这里只提示可选加速件，不拦。"""
    try:
        import requests  # noqa: F401
        ok("可选依赖 requests 已装（打包版将带上，抓取更稳）")
    except ImportError:
        ok("未装 requests，运行时自动回退 urllib（不影响打包）")


def ensure_pyinstaller() -> str:
    """返回 PyInstaller 版本号，没装就自动装一次。"""
    try:
        import PyInstaller  # noqa: F401
        ok(f"PyInstaller {PyInstaller.__version__}")
        return PyInstaller.__version__
    except ImportError:
        pass
    step("未检测到 PyInstaller，正在自动安装（首次打包需要，仅此一次）...")
    cmd = [sys.executable, "-m", "pip", "install", "pyinstaller"]
    if subprocess.call(cmd) != 0:
        die("PyInstaller 安装失败",
            f"请手动执行：{' '.join(cmd)}",
            "或 pip install '.[build]'")
    try:
        import PyInstaller  # noqa: F811
        ok(f"PyInstaller {PyInstaller.__version__} 安装完成")
        return PyInstaller.__version__
    except ImportError:
        die("PyInstaller 装上了却导入不了", "可能装到了别的解释器，请检查虚拟环境")


def check_config(bundle: bool) -> None:
    cfg = ROOT / "tea_config.json"
    if not bundle:
        if cfg.is_file():
            ok("跳过 tea_config.json（含代理/本金等隐私，默认不打进分发包）")
        return
    if cfg.is_file():
        ok("将把 tea_config.json 作为默认配置模板打进包")
        say("    ⚠ 该文件可能含代理账号密码与本金，勿把产物发给他人")
    else:
        ok("未找到 tea_config.json，打包版首次启动将走内置默认值")


# ---------------------------------------------------------------- 打包
def clean() -> None:
    """每次打包都从干净的 dist/ 与 build/ 开始，避免上一轮残留混进产物。"""
    removed = False
    for d in (DIST, BUILD):
        if d.exists():
            step(f"删除旧的 {d.relative_to(ROOT)}/")
            shutil.rmtree(d, ignore_errors=True)
            removed = True
    ok("已删除旧产物，dist/ 与 build/ 将重新生成" if removed
       else "没有旧产物，dist/ 与 build/ 直接新建")


def run_pyinstaller(args: argparse.Namespace) -> None:
    env = dict(os.environ)
    env["TEA_ONEFILE"] = "1" if args.onefile else "0"
    env["TEA_NO_CONSOLE"] = "1" if args.no_console else "0"
    env["TEA_BUNDLE_CONFIG"] = "1" if args.bundle_config else "0"
    cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC)]
    step(f"调用 PyInstaller（预计 1~3 分钟）：{' '.join(cmd[2:])}")
    say()
    rc = subprocess.call(cmd, cwd=str(ROOT), env=env)
    say()
    if rc != 0:
        die(f"PyInstaller 退出码 {rc}",
            "上面的日志里通常有具体原因（缺模块 / 磁盘空间 / 权限）")
    ok("PyInstaller 执行完成")


# ---------------------------------------------------------------- 产物收尾
def _size(p: pathlib.Path) -> str:
    total = 0
    if p.is_file():
        total = p.stat().st_size
    else:
        for f in p.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return f"{total / 1024 / 1024:.1f} MB"


def finish_macos(onefile: bool) -> pathlib.Path:
    if onefile:
        target = DIST / APP_NAME
        if not target.is_file():
            die(f"没找到预期产物 {target}")
        target.chmod(0o755)
        return target
    app = DIST / f"{APP_NAME}.app"
    if not app.is_dir():
        die(f"没找到预期产物 {app}", "确认 spec 里的 BUNDLE 段执行到了")
    # .app 里的真实可执行文件必须带 x 位，否则双击/命令行启动都是 Permission denied
    inner = app / "Contents" / "MacOS" / APP_NAME
    if inner.is_file():
        inner.chmod(0o755)
        ok(f"已置可执行位 {inner.relative_to(ROOT)}")
    else:
        say(f"  ⚠ 未找到 {inner}，请手动确认 .app 结构")
    return app


def finish_windows(onefile: bool) -> pathlib.Path:
    exe = DIST / f"{APP_NAME}.exe"
    if exe.is_file():
        return exe
    # 目录模式：产物是 dist/TEA/TEA.exe 一堆文件，压成 zip 才好发
    folder = DIST / APP_NAME
    inner = folder / f"{APP_NAME}.exe"
    if not inner.is_file():
        die(f"没找到预期产物 {exe} 或 {inner}")
    zip_path = DIST / f"{APP_NAME}-windows.zip"
    step(f"多文件产物，打包成 {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(DIST))
    ok(f"已压缩 {zip_path.relative_to(ROOT)}（{_size(zip_path)}）")
    return inner


# ---------------------------------------------------------------- 主流程
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build.py",
        description="TEA 一键跨平台打包：macOS 产出 .app，Windows 产出 .exe。",
        epilog="每次打包都会先删掉 dist/ 与 build/ 再重新生成；"
               "产物在 dist/ 下，运行时数据写 ~/.tea/，可用 TEA_HOME 覆盖。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--onefile", action="store_true",
                   help="单文件模式（默认 macOS 用 onedir 出 .app，Windows 用 onefile 出 .exe）")
    p.add_argument("--no-console", action="store_true",
                   help="隐藏控制台（仅 Windows；TEA 是交互式 CLI，默认保留）")
    p.add_argument("--bundle-config", action="store_true",
                   help="把 tea_config.json 作为默认模板打进包（含隐私，默认不打）")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    plat = SUPPORTED.get(sys.platform)
    say("=" * 56)
    say(f"TEA 一键打包 · {plat or sys.platform}")
    say("=" * 56)
    if plat is None:
        die(f"不支持在 {sys.platform} 上打包",
            "PyInstaller 无法交叉编译，只能在目标平台本机打包。",
            "当前支持：" + "、".join(f"{v}（{k}）" for k, v in SUPPORTED.items()),
            "Linux 用户可直接用源码运行：python3 -m tea")

    is_mac = sys.platform == "darwin"
    # onefile 是 Windows 的默认形态；mac 要出 .app 必须走 onedir
    onefile = args.onefile or not is_mac
    if args.no_console and is_mac:
        say("  ⚠ --no-console 在 macOS 上无效（.app 需要终端交互），已忽略")
        args.no_console = False
    if args.onefile and is_mac:
        say("  ⚠ macOS 单文件模式产出的是命令行可执行文件 dist/TEA，不是 .app")

    say(f"\n[1/4] 环境检查（项目根 {ROOT}）")
    check_python()
    check_sources()
    ensure_pyinstaller()
    check_deps()
    check_config(args.bundle_config)

    say("\n[2/4] 准备目录")
    clean()

    mode = "onefile" if onefile else "onedir"
    console = "保留控制台" if not args.no_console else "隐藏控制台"
    say(f"\n[3/4] 开始打包（{mode}，{console}）")
    run_pyinstaller(args)

    say("\n[4/4] 产物收尾")
    target = finish_macos(onefile) if is_mac else finish_windows(onefile)

    say()
    say("=" * 56)
    say(f"✓ 打包完成：{target}")
    say("=" * 56)
    say(f"  体积     {_size(target)}")
    if is_mac and target.name.endswith(".app"):
        say(f"  运行     open -a {target}")
        say(f"           或 {target}/Contents/MacOS/{APP_NAME}")
    else:
        say(f"  运行     {target}")
    say("  数据目录 ~/.tea/（自动创建，可用 TEA_HOME 环境变量覆盖）")
    say("  自测     在产物上跑一次 selftest 可验证打包完整性")
    return 0


if __name__ == "__main__":
    sys.exit(main())
