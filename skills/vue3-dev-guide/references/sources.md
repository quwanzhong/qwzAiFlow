# 资料来源与时效

本 Skill 只提炼规则和决策依据，不复制或重新分发书籍正文。

## 用户提供的资料

### 《Vue.js 设计与实现》

- 作者：霍春阳（HcySunYang）
- 本地来源：`/Users/qwz/书/VUE/Vue.js设计与实现 (霍春阳（HcySunYang）) (Z-Library).epub`
- 文件时间信息：2022-08-25
- 采用内容：框架设计权衡、响应系统、ref/toRef/toRefs、computed、watch 与过期副作用、渲染器、key 与 Diff、组件生命周期、props/emits/slots、异步组件、KeepAlive、Teleport、Transition、编译优化和 SSR 状态隔离。
- 使用边界：主要用于理解 Vue 3 原理，不把书中教学实现当作业务项目应自行实现的框架代码。

### 《Vue.js 从入门到项目实践：超值版》

- 作者：聚慕课教育研发中心
- 本地来源：`/Users/qwz/书/VUE/Vue.js从入门到项目实践：超值版 (聚慕课教育研发中心) (Z-Library).pdf`
- 出版信息：2021 年第 1 版，内容以 Vue 2 生态为主。
- 采用内容：组件化、指令与数据绑定的通用思想、组件通信、路由与状态管理需求、工程目录、表单和项目实践流程。
- 排除或转换：`new Vue`、`Vue.extend`、过滤器、实例事件 API、旧 v-model、Vuex 默认选型、Vue CLI/Webpack 默认选型等必须按 Vue 3 规则重新判断。

## 当前官方依据

验证日期：2026-09-21。

- Vue 指南与 API 风格：<https://vuejs.org/guide/introduction>
- Composition API：<https://vuejs.org/guide/extras/composition-api-faq>
- TypeScript 与 Composition API：<https://vuejs.org/guide/typescript/composition-api>
- Vue 3 迁移变化：<https://v3-migration.vuejs.org/breaking-changes/>
- Vue 3 工具链建议：<https://v3-migration.vuejs.org/recommendations>
- Vue Router：<https://router.vuejs.org/guide/>
- Pinia：<https://pinia.vuejs.org/introduction>
- Vite：<https://vite.dev/guide/>

## 更新原则

- 每次开发先读取项目实际依赖版本，不根据本文件自动升级。
- 遇到版本相关 API 时查对应版本的官方文档和 Release Notes。
- 书籍提供稳定原理与历史背景，官方文档决定当前 API，项目代码决定最终落地方式。
