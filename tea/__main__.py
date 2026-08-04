"""python -m tea 入口。"""
import sys

try:
    from .runtime.cli import main
except ImportError:  # PyInstaller 冻结环境无包上下文，降级为绝对导入
    from tea.runtime.cli import main

if __name__ == "__main__":
    sys.exit(main())
