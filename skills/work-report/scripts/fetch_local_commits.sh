#!/bin/bash
# 备用取数路径:扫描 repos_dir 下所有 git 仓库,输出指定作者、时间范围内的提交。
# 用法: fetch_local_commits.sh SINCE UNTIL [AUTHOR] [REPOS_DIR]
set -euo pipefail

since="${1:?用法: fetch_local_commits.sh SINCE UNTIL [AUTHOR] [REPOS_DIR]}"
until="${2:?缺少 UNTIL}"
author="${3:-quwanzhong}"
dir="${4:-$HOME/aiFlow/git}"

[ -d "$dir" ] || { echo "[fetch_local_commits] 目录不存在: $dir" >&2; exit 1; }

found=0
for repo in "$dir"/*/; do
  [ -d "${repo}.git" ] || continue
  name=$(basename "$repo")
  out=$(git -C "$repo" log --all \
    --since="${since} 00:00:00" --until="${until} 23:59:59" \
    --author="$author" \
    --date=format:'%Y-%m-%d %H:%M' \
    --pretty=format:'- %h %ad %s' 2>/dev/null || true)
  if [ -n "$out" ]; then
    found=1
    echo "## $name"
    echo "$out"
    echo
  fi
done

if [ "$found" -eq 0 ]; then
  echo "在 $dir 下未找到 $author 在 ${since} ~ ${until} 的提交。"
fi
