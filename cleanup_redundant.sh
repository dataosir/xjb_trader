#!/bin/bash
# ------------------------------------------------------------
# 一键清除所有因对话/终端输出片段误提交的冗余文件与目录。
#
# 用法：
#   chmod +x cleanup_redundant.sh
#   ./cleanup_redundant.sh
#
# 执行后手动提交推送：
#   git add -A
#   git commit -m "chore: 清理所有误提交的冗余文件"
#   git push origin main
# ------------------------------------------------------------
set -euo pipefail

echo "=== 开始清理冗余文件 ==="

# 要删除的文件/目录清单（按实际误提交条目填写）
# 如需增加新发现的冗余文件，直接在下方数组中添加即可
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
)

echo "++++ 从工作区删除 ++++"
for entry in "${FILES_TO_DELETE[@]}"; do
  if [ -e "$entry" ] || [ -L "$entry" ]; then
    echo "  删除: $entry"
    rm -rf "$entry"
  else
    echo "  不存在（跳过）: $entry"
  fi
done

echo ""
echo "++++ 从 Git 索引中移除 ++++"
# 使用 --ignore-unmatch 避免因文件不存在而报错中断
git rm --cached -r --ignore-unmatch "${FILES_TO_DELETE[@]}" 2>/dev/null || true
if [ $? -eq 0 ]; then
  echo "  索引移除完成"
else
  echo "  部分文件可能已在索引中不存在（正常）"
fi

echo ""
echo "=== 清理完成 ==="
echo "请手动执行以下命令完成提交："
echo "  git add -A"
echo "  git commit -m \"chore: 清理所有误提交的冗余文件\""
echo "  git push origin main"
