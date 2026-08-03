"""运行时数据目录解析：$TEA_HOME > ~/.tea/（打包版）> CWD（源码版）。

打包成 .app / .exe 之后，可执行文件所在目录不可写（macOS 还要过签名校验），
把 watch_pool.json / capital_state.json / .tea_sector_cache.json 写进去必然失败，
所以冻结运行时得换一个用户家目录下的落脚点。三级优先级：

1. ``$TEA_HOME`` —— 用户显式指定，最高优先级（离线自测也靠它切进临时沙盒）
2. ``~/.tea/``   —— 打包版默认，自动创建
3. ``CWD``       —— 源码版默认，保持历史行为不变

返回的是「运行时基准目录」而不是 data 子目录：``data/`` 与 ``reports/`` 由
config_store 按 ``paths.data_dir`` / ``paths.reports_dir`` 再往下拼一层，
所以源码版依旧落在 ``CWD/data``、打包版落在 ``~/.tea/data``。

本模块在 Layer 0（core），只用标准库，不反向依赖 tea.config。
"""
from __future__ import annotations

import os
import pathlib
import sys

#: 覆盖运行时基准目录的环境变量
HOME_ENV = "TEA_HOME"
#: 直接指定配置文件全路径的环境变量（优先级高于基准目录）
CONFIG_ENV = "TEA_CONFIG"
#: 配置文件名
CONFIG_NAME = "tea_config.json"
#: 打包版在用户家目录下的落脚点
USER_DIR = ".tea"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的 .app / .exe 里。"""
    return bool(getattr(sys, "frozen", False))


def _ensure(p: pathlib.Path) -> pathlib.Path:
    """尽力创建目录。

    创建失败（只读挂载、权限不足）时不在这里抛：真正落盘的 utils.atomic_write
    会带着完整路径报错，比在路径解析阶段炸掉更好定位。
    """
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def data_dir() -> pathlib.Path:
    """运行时基准目录（$TEA_HOME > ~/.tea/ > CWD），必要时自动创建。"""
    env = os.environ.get(HOME_ENV)
    if env:
        return _ensure(pathlib.Path(env).expanduser())
    if is_frozen():
        return _ensure(pathlib.Path.home() / USER_DIR)
    # 源码版：CWD 本来就存在，不额外创建，行为与迁移前完全一致
    return pathlib.Path.cwd()


def config_path() -> pathlib.Path:
    """配置文件路径（$TEA_CONFIG 优先，否则基准目录下的 tea_config.json）。"""
    env = os.environ.get(CONFIG_ENV)
    if env:
        return pathlib.Path(env).expanduser()
    return data_dir() / CONFIG_NAME


def bundled_dir() -> pathlib.Path:
    """打包时随包带上的只读资源目录。

    PyInstaller 把 datas 解到 ``sys._MEIPASS``（onefile 是临时目录，onedir 是
    可执行文件旁边）。源码运行时没有这个属性，回落到项目根。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return pathlib.Path(meipass)
    return pathlib.Path(__file__).resolve().parent.parent.parent


def bundled_config() -> "pathlib.Path | None":
    """随包携带的默认配置模板；没带就返回 None。

    仅在打包版首次启动、用户家目录里还没有配置时用作种子，避免新用户上手就是
    一份空配置。是否真的把模板打进包由 packaging/build.py 的 --bundle-config
    决定（默认不打，因为 tea_config.json 可能含代理账号密码与本金）。
    """
    p = bundled_dir() / CONFIG_NAME
    try:
        if p.is_file():
            return p
    except OSError:
        pass
    return None
