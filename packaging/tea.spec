# -*- mode: python ; coding: utf-8 -*-
"""TEA 打包规格（PyInstaller）。

不要直接 `pyinstaller` 手敲参数，统一走 `python3 packaging/build.py`：
平台判断、依赖检查、产物收尾都在那边。

平台差异（PyInstaller 不能交叉编译，只能在目标平台上各跑一次）：
- macOS  ：onedir + BUNDLE → dist/TEA.app
- Windows：onefile        → dist/TEA.exe

TEA 是交互式 CLI，两个平台都必须留控制台（console=True）：--windowed
会让 .app 双击后没有任何输入输出，等于打了个哑巴。
"""
import os
import sys

# ------------------------------------------------------------------ 项目根
# SPECPATH 由 PyInstaller 注入（spec 文件所在目录），回落到 CWD 兜底。
_here = globals().get("SPECPATH") or os.getcwd()
ROOT = os.path.dirname(os.path.abspath(_here))

BUNDLE_ID = "com.dataosir.tea"
APP_NAME = "TEA"
ENTRY = os.path.join(ROOT, "tea", "__main__.py")

# build.py 通过环境变量把 CLI 开关递进来（spec 本身不接受自定义参数）
ONEFILE = os.environ.get("TEA_ONEFILE") == "1"
CONSOLE = os.environ.get("TEA_NO_CONSOLE") != "1"
BUNDLE_CONFIG = os.environ.get("TEA_BUNDLE_CONFIG") == "1"
IS_MAC = sys.platform == "darwin"

# ------------------------------------------------------------------ 隐式导入
# 全部子模块逐个列出。tea 内部大量走「运行时按名取源」（data.providers 按
# market.data_sources 里的字符串选实现、phases 由菜单分派），PyInstaller 的
# 静态分析看不到这些字符串，漏掉的模块要到用户点菜单时才 ImportError 崩。
# 新增模块记得同步这里 —— selftest 的「打包 · spec 隐式导入」会盯着，漏了就红。
HIDDEN = [
    "tea",
    "tea.__main__",
    "tea.selftest",
    # 分析层
    "tea.analysis",
    "tea.analysis.expectancy",
    "tea.analysis.followthrough",
    "tea.analysis.identity",
    "tea.analysis.sentiment",
    "tea.analysis.stats",
    # 配置层
    "tea.config",
    "tea.config.config_store",
    "tea.config.onboarding",
    # 基础层
    "tea.core",
    "tea.core.paths",
    "tea.core.timing",
    "tea.core.utils",
    # 行情层
    "tea.data",
    "tea.data.cache",
    "tea.data.errors",
    "tea.data.fetcher",
    "tea.data.indicators",
    "tea.data.market",
    # 数据源实现：按配置里的名字动态挑，静态分析必漏
    "tea.data.providers",
    "tea.data.providers.base",
    "tea.data.providers.eastmoney",
    "tea.data.providers.ifeng",
    "tea.data.providers.netease",
    "tea.data.providers.sina",
    "tea.data.providers.tencent",
    # 四阶段流程
    "tea.phases",
    "tea.phases.phase1",
    "tea.phases.phase2",
    "tea.phases.phase3",
    "tea.phases.phase4",
    "tea.phases.prompt",
    "tea.phases.results",
    "tea.phases.session",
    # 组合与台账
    "tea.portfolio",
    "tea.portfolio.accumulator",
    "tea.portfolio.plan",
    "tea.portfolio.portfolio",
    "tea.portfolio.trades",
    "tea.portfolio.watch_pool",
    # 报告
    "tea.reporting",
    "tea.reporting.report",
    "tea.reporting.seed_trace",
    "tea.reporting.weekly",
    # 运行时入口
    "tea.runtime",
    "tea.runtime.cli",
    "tea.runtime.runner",
    # 筛选与风控
    "tea.screening",
    "tea.screening.gates",
    "tea.screening.preflight",
    "tea.screening.screener",
    "tea.screening.seed_report",
    "tea.screening.veto",
]

# ------------------------------------------------------------------ 随包资源
# 默认不带 tea_config.json：它被 .gitignore 排除正是因为可能含代理账号密码与
# 本金，打进分发包等于把这些随二进制一起发出去。需要时 --bundle-config 显式开。
DATAS = []
if BUNDLE_CONFIG:
    _cfg = os.path.join(ROOT, "tea_config.json")
    if os.path.isfile(_cfg):
        DATAS.append((_cfg, "."))

# ------------------------------------------------------------------ 体积裁剪
# 开发环境里装的测试/lint/科学计算/GUI 全家桶跟运行时无关，不排掉会让包从
# 十几 MB 涨到几百 MB。tea 运行时是标准库零依赖，排这些绝对安全。
EXCLUDES = [
    "pytest", "_pytest", "py", "pluggy", "nose", "unittest2",
    "ruff", "mypy", "flake8", "pylint", "black", "isort",
    "numpy", "pandas", "scipy", "matplotlib", "sklearn",
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "IPython", "jupyter", "notebook", "jedi",
    "PIL", "Pillow", "cv2",
    "setuptools._distutils", "distutils.command", "lib2to3",
    "sqlite3", "pydoc_data", "curses",
]

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

if ONEFILE or not IS_MAC:
    # 单文件：Windows 默认走这条，产出 dist/TEA.exe
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    # 目录模式：macOS 默认走这条，COLLECT 之后再包成 .app
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=APP_NAME,
    )
    if IS_MAC:
        app = BUNDLE(
            coll,
            name=APP_NAME + ".app",
            icon=None,
            bundle_identifier=BUNDLE_ID,
            info_plist={
                "CFBundleIdentifier": BUNDLE_ID,
                "CFBundleName": APP_NAME,
                "CFBundleDisplayName": APP_NAME,
                "CFBundleExecutable": APP_NAME,
                "CFBundleShortVersionString": "1.0.0",
                "CFBundleVersion": "1.0.0",
                # False：TEA 要在终端里跟用户一问一答，不能当后台代理跑
                "LSBackgroundOnly": False,
                "LSMinimumSystemVersion": "10.13",
                "NSHighResolutionCapable": True,
            },
        )
