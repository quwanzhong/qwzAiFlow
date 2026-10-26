# qwzAiFlow

个人 AI 工作流资产库:跨设备、跨 AI 工具(Codex / Claude Code / Cursor / ZCode / Gemini CLI / Trae …)复用的 skill 与全局约定,**真身只有这一份**,各工具目录里都是软链接。

## 布局

```
AGENTS.md            ← 所有 AI 工具的统一入口:全局工作约定 + 关键词工作模式 + skill 清单 + 接入方式
skills/              ← skill 真身(每个子目录一个,含 SKILL.md)
  work-report/         日报/周报/月报:从内网 GitLab API 拉取提交/MR/议题自动生成中文报告
  android-dev-guide/   Android/Kotlin 开发指南
  vue3-dev-guide/      Vue 3 开发指南
  hard-bug-diagnosis/  疑难缺陷诊断方法论
  软考模拟/            备考笔记
config/              ← 各 skill 的敏感配置(gitignore,不入库;本机路径 ~/.config/* 软链接至此)
reports/             ← 生成的报告(gitignore)
```

## 接入新 AI 工具

一行接入,不复制文件,详见 [AGENTS.md](AGENTS.md) 的「接入新 AI 工具的方式」:

- 支持 AGENTS.md 标准的工具:把它的全局说明文件软链接到本仓库的 `AGENTS.md`
- 只扫自家 skill 目录的工具:`ln -s <本仓库>/skills/<name> <该工具的 skills 目录>/<name>`

## 换机恢复

```bash
git clone https://github.com/quwanzhong/qwzAiFlow ~/aiFlow
# 1. 按各 skill 的 SKILL.md 重建 config/(含 token,勿提交)
# 2. 为每个工具重建软链接(见 AGENTS.md)
```

## License

Apache License 2.0
