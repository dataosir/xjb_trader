# 删除工作区副本
rm -rf "（我将只给出最终完整的文件。）tea" \
       "Windows 用 dir 和" \
       "请运行：" \
       "python -m tea --help" \
       "git commit -m \"fix: remove duplicate tea folders\"" \
       "docs/duplicate_tea_folders.md" \
       "└── tea" \
       "4. **推送" \
       "# 1. 从工作区删除" \
       "# 2. 从 Git 暂存区移除" \
       "# 3. 提交" 2>/dev/null

# 从 Git 索引中移除（防止再被跟踪）
git rm --cached -r "（我将只给出最终完整的文件。）tea" \
                 "Windows 用 dir 和" \
                 "请运行：" \
                 "python -m tea --help" \
                 "git commit -m \"fix: remove duplicate tea folders\"" \
                 "docs/duplicate_tea_folders.md" \
                 "└── tea" \
                 "4. **推送" \
                 "# 1. 从工作区删除" \
                 "# 2. 从 Git 暂存区移除" \
                 "# 3. 提交" 2>/dev/null
