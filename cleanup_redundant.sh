#!/bin/bash
# ------------------------------------------------------------
# 彻底清除所有因对话/终端输出误提交的文件与目录
# 不会触碰任何功能代码、配置或文档（CHANGELOG, CONTRIBUTING, docs/changelog/ 等）
# 用法：
#   chmod +x cleanup_redundant.sh
#   ./cleanup_redundant.sh
# 执行后手动提交推送：
#   git add -A
#   git commit -m "chore: 删除所有误提交的冗余文件"
#   git push origin main
# ------------------------------------------------------------
set -euo pipefail

echo "=== 清理开始 ==="

# 需要删除的工作区/Tree 条目（仅包含确认由终端对话产生的垃圾文件）
FILES_TO_DELETE=(
  "（我将只给出最终完整的文件。）tea"
  "Windows 用 dir 和"
  "请运行："
  "python -m tea --help"
  "git commit -m \"fix: remove duplicate tea folders\""
  "docs/duplicate_tea_folders.md"
  "└── tea"
  "2. 从 Git 索引中移除"
  "1. 工作区删除"
  "3. 提交清理"
  "4. **推送"
  "# 1. 从工作区删除"
  "# 2. 从 Git 暂存区移除"
  "# 3. 提交"
  # 以下为本次发现的额外伪文件
  "1. **工作区删除"
  "2. **从 Git 索引中移除"
  "3. **提交清理"
)

echo "--- 从工作区删除 ---"
for entry in "${FILES_TO_DELETE[@]}"; do
  if [ -e "$entry" ] || [ -L "$entry" ]; then
    echo "  删除: $entry"
    rm -rf "$entry"
  else
    echo "  不存在（跳过）: $entry"
  fi
done

echo ""
echo "--- 从 Git 索引中移除 ---"
git rm --cached -r --ignore-unmatch "${FILES_TO_DELETE[@]}" 2>/dev/null || true

echo ""
echo "=== 清理完成 ==="
echo "请手动执行以下命令完成提交："
echo "  git add -A"
echo "  git commit -m \"chore: 删除所有误提交的冗余文件\""
echo "  git push origin main"
