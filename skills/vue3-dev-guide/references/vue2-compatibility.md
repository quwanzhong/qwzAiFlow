# Vue 2 兼容与迁移

当资料或项目出现 Vue 2 API 时读取本文件。不要把本文件当成自动迁移授权；用户未要求迁移时，保持现有技术栈。

## 资料边界

《Vue.js 从入门到项目实践：超值版》出版于 2021 年，主要采用 Vue 2 时代的实例、Vuex 和 Webpack 工程方式。它适合参考组件化、数据绑定、路由、状态管理需求和项目拆分思想，但代码 API 不能直接作为 Vue 3 默认写法。

## 常见替换关系

| Vue 2 写法 | Vue 3 判断 |
| --- | --- |
| `new Vue(...)` | 使用 `createApp(...)` 创建应用实例 |
| `Vue.extend(...)` | 优先单文件组件；需要类型辅助时使用 `defineComponent` |
| `Vue.component/use/directive` 全局调用 | 使用 `app.component/use/directive`，并控制全局注册范围 |
| 模板过滤器 `{{ value \| filter }}` | 已移除；使用 computed、普通函数或格式化工具 |
| `$on`、`$off`、`$once` 事件总线 | 已移除；优先 props/emits、Store、provide/inject，确需总线时使用项目已有方案 |
| 组件 `value` + `input` 的默认 v-model | 使用 `modelValue` + `update:modelValue`，或项目版本支持的宏 |
| `.sync` | 使用带参数的 `v-model` 与对应 `update:*` 事件 |
| `beforeDestroy` / `destroyed` | `beforeUnmount` / `unmounted` |
| `$listeners` | 监听器合并进 `$attrs`；先确认透传边界和 `inheritAttrs` |
| `Vue.set/delete`、`$set/$delete` | Proxy 响应式下通常不需要 |
| `render(h)` 的参数 `h` | 从 `vue` 导入 `h`；模板仍应是默认选择 |
| Vuex 作为默认状态方案 | 新 Vue 3 项目优先考虑 Pinia；已有 Vuex 不自动迁移 |
| Vue CLI/Webpack 作为默认工具链 | 新项目优先考虑 Vite；已有构建链不自动迁移 |
| Vetur | 当前 Vue 语言工具优先遵循项目和官方推荐，不擅自改 IDE 配置 |

## 迁移策略

1. 先锁定当前 Vue 版本、依赖和构建链。
2. 区分“让现有 Vue 2 功能继续工作”和“迁移到 Vue 3”两个不同目标。
3. 按功能切片迁移，优先处理全局 API、组件 v-model、事件、生命周期、路由和状态管理兼容。
4. 不在一次普通需求中同时切换 Vue、Router、Store、构建工具和 UI 库。
5. 对行为变化写出回归点，尤其是数组 watch、attrs/监听器透传、v-if/v-for、key、异步组件和 TransitionGroup。

## 可以跨版本保留的思想

- 页面由可复用组件组合而成；
- 数据流、事件和组件职责应清晰；
- 路由、状态、请求和 UI 各自有边界；
- 表单、异常反馈和项目目录需要一致规范；
- 先理解业务流程，再决定抽象和复用方式。
