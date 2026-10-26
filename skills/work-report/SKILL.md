---
name: work-report
description: >-
  生成用户的工作日报/周报/月报:从内网 GitLab(地址见
  ~/.config/work-report/config.json 的 gitlab_url)拉取用户账号在指定时间范围内的
  提交、合并请求、议题记录,聚类归纳成中文
  markdown 报告,保存到 /Users/qwz/aiFlow/reports/。Use whenever the user
  mentions 日报、周报、月报、工作总结、工作汇报、述职、"总结一下我(昨天/这周/这个月)
  做了什么"、"看看我的提交记录"、GitLab 记录、commit 记录,或想知道自己某天/某周/
  某月干了什么 — even if they never say the word "报告". 配置在
  ~/.config/work-report/config.json(需要 read_api 权限的 personal access token)。
---

# work-report:GitLab 提交记录 → 日报/周报/月报

四步流程:**确定时间范围 → 检查配置 → 拉取数据 → 写报告存盘**。
数据只来自 GitLab(或本地 git 仓库),绝不编造。

## 1. 确定时间范围

先 `date +%F` 拿到今天日期,再按用户说法换算(闭区间,格式 YYYY-MM-DD):

| 用户说法 | since / until |
|---|---|
| 日报(默认)| 今天 / 今天 |
| 昨天的日报 | 昨天 / 昨天 |
| 周报(默认)| 上周一 / 上周日 |
| 本周周报 | 本周一 / 今天 |
| 月报(默认)| 上月 1 号 / 上月最后一天 |
| 本月月报 | 本月 1 号 / 今天 |
| 直接给了日期或范围 | 原样使用 |

## 2. 检查配置

读 `~/.config/work-report/config.json`(gitlab_url / token / username / reports_dir / repos_dir)。

- token 为空,或脚本报 401 → 停下,告诉用户:登录 GitLab(config 的 `gitlab_url`)
  → 右上角头像 → Preferences → Access Tokens → 新建 token(**勾选 read_api 权限**)→
  填入 config 的 `token` 字段(新版入口路径 `/-/user_settings/personal_access_tokens`,
  旧版 `/-/profile/personal_access_tokens`)。不要替用户编造数据或跳过这步。
- token 是敏感信息:永远不写进报告、不在对话里回显完整值、不提交到任何仓库。

## 3. 拉取数据

脚本真身在 `~/aiFlow/skills/work-report/scripts/`(各工具目录下的同名路径是软链接,指向同一份):

```bash
python3 ~/aiFlow/skills/work-report/scripts/fetch_gitlab.py \
  --since 2026-10-19 --until 2026-10-25
```

- 默认输出 markdown,直接可读;需要程序化处理时加 `--format json`。
- 用户名默认取 config 的 `username`;月报范围大,脚本会慢一些,属正常。
- 备用路径:token 不可用、但本地克隆了仓库(config 的 `repos_dir`,如 ~/aiFlow/git)时,
  改用 `bash ~/aiFlow/skills/work-report/scripts/fetch_local_commits.sh SINCE UNTIL`
  (只有提交记录,没有 MR/议题)。
- 连接失败 → 提示用户检查内网 / VPN 是否连通。

## 4. 写报告并保存

保存到 config 的 `reports_dir`,文件名规范:`日报-YYYY-MM-DD.md`、
`周报-YYYY-MM-DD_YYYY-MM-DD.md`、`月报-YYYY-MM.md`。报告用中文,模板:

```markdown
# 工作日报 2026-10-26

## 今日概览
(2~4 句:今天主线做了什么、推进到哪一步)

## 工作明细
### <项目/模块名>
- **<归纳出的工作项>**:<做了什么、到什么程度>(依据:3 次提交,feat: xxx / fix: xxx)

## 数据统计
- 提交 N 次(+X / -Y 行代码),合并请求 M 个,议题 K 个

## 明日计划
(提交记录推不出来时写"(待补充)",不要编造)
```

周报/月报用同一模板:概览改"本期概览",明细可按天或按主题分块,概览的分量更重。

写作规则:

1. **聚类而不是罗列**:把同一件事的多次 commit 合并成一条工作项,用业务语言概括;
   commit message 的关键原文(feat/fix/refactor 等)作为依据附在后面。
2. **只写有证据的**:内容全部来自脚本输出;该时间段没有记录就如实写
   "该时间段无提交记录",不要凑内容。
3. 英文 commit message 保留原文,旁边给中文概括。
4. 写完把报告文件的 markdown 链接发给用户,并附 1~2 句本期要点。
