#!/usr/bin/env bash
# 一键启动（macOS / Linux）。
#
# 用法：
#   ./run.sh                进入数字菜单
#   ./run.sh selftest       跑离线自测
#   ./run.sh eval 600519    任意子命令原样透传
#
# 直接跑源码，不装包、不建虚拟环境——运行时零第三方依赖，没有可省的步骤。
#
# 两个可选环境变量：
#   TEA_PYTHON   指定解释器（默认按 python3 → python 顺序找）
#   TEA_HOME     指定数据目录（默认锁到本脚本所在目录，见下）

set -euo pipefail

# 引擎的配置与数据默认落在"当前工作目录"。这里把它锁到仓库根目录，
# 否则从不同路径点这个脚本会生出好几套互不相干的配置和持仓。
# 已经自己设了 TEA_HOME 的照旧。
#
# 取目录全用 bash 内建（参数展开 + cd/pwd），不调 dirname——PATH 异常时
# 外部命令会找不到，而那一失败是隐形的，ROOT 会默默退化成当前目录。
case "${BASH_SOURCE[0]}" in
    */*) SELF_DIR="${BASH_SOURCE[0]%/*}" ;;   # 带路径
    *)   SELF_DIR="." ;;                      # 被当成 PATH 里的命令直接叫
esac
ROOT="$(cd -- "$SELF_DIR" && pwd)"
export TEA_HOME="${TEA_HOME:-$ROOT}"
cd -- "$ROOT"

# -------------------------------------------------- 找解释器
PY="${TEA_PYTHON:-}"
if [ -z "$PY" ]; then
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    done
fi

if [ -z "$PY" ]; then
    echo "找不到 Python。请先安装 Python 3.8 或更高版本：https://www.python.org/downloads/" >&2
    echo "已装但不在 PATH 里的话，可以指定：TEA_PYTHON=/path/to/python3 ./run.sh" >&2
    exit 1
fi

# 版本别只看名字，python3 也可能是 3.7。
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    echo "Python 版本过低：$("$PY" -V 2>&1)，本项目需要 3.8+。" >&2
    echo "可用 TEA_PYTHON 指向新版本，例如：TEA_PYTHON=python3.12 ./run.sh" >&2
    exit 1
fi

# -------------------------------------------------- 启动
# exec 把进程交出去，Ctrl-C 直接送到 Python，不会被这层脚本吞掉。
exec "$PY" -m tea "$@"
