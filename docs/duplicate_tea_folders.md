# 1. 从工作区删除
rm -rf "（我将只给出最终完整的文件。）tea" "Windows 用 dir 和" "请运行：" "python -m tea --help" "git commit -m \"fix: remove duplicate tea folders\"" "docs/duplicate_tea_folders.md" "└── tea" 2>/dev/null

# 2. 从 Git 暂存区移除
git rm --cached -r "（我将只给出最终完整的文件。）tea" "Windows 用 dir 和" "请运行：" "python -m tea --help" "git commit -m \"fix: remove duplicate tea folders\"" "docs/duplicate_tea_folders.md" "└── tea" 2>/dev/null

# 3. 提交
git commit -m "chore: 删除误提交的临时对话/命令文件"
