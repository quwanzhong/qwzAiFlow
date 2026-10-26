---
name: android-dev-guide
description: Android 应用开发技术指导，基于郭霖《第一行代码 Android 第3版》的技术体系。当进行 Android/Kotlin 开发时使用：新建项目、编写 Activity/Fragment、UI 布局、RecyclerView 列表、网络请求（Retrofit/OkHttp/协程）、数据持久化（SharedPreferences/Room/SQLite）、MVVM 架构搭建、Jetpack 组件（ViewModel/LiveData/WorkManager）、Material Design 界面、广播、Service、运行时权限、多媒体、通知等场景。
---

# Android 开发指南（《第一行代码》第3版技术体系）

技术选型基调：**Kotlin 优先 + Jetpack MVVM + Retrofit/OkHttp + 协程 + Material Design**。

本书所有示例均为 Kotlin。Java 写法只在维护旧代码时参考，新代码一律 Kotlin。

## 1. MVVM 分层架构（第15章实战标准）

```
ui/           → Activity、Fragment、Adapter（只持有 ViewModel 引用，只做展示）
  └── <feature>/  按功能再分子包
viewmodel/    → ViewModel：持有 UI 数据，调用仓库层，不持有 View/Context
logic/
  ├── model/    → data class 数据模型（@SerializedName 映射 JSON 字段）
  ├── network/  → Retrofit 接口 + ServiceCreator 单例 + Network 统一入口
  └── dao/      → Room DAO / SharedPreferences 封装
repository/   → 仓库层：判断走本地还是网络数据源，返回统一结果
```

**铁律**：引用只能指向相邻层、只能单向。UI → ViewModel → Repository → DataSource。ViewModel 层起不再有 Context 引用。

```kotlin
// 全局 Context：Application 单例（书中 14.1 节的技巧）
class MyApp : Application() {
    companion object {
        @SuppressLint("StaticFieldLeak")
        lateinit var context: Context
    }
    override fun onCreate() { super.onCreate(); context = applicationContext }
}
// AndroidManifest: android:name=".MyApp"
```

```kotlin
// Retrofit 构建器（书中 11.6.3 最佳写法）
object ServiceCreator {
    private const val BASE_URL = "https://api.example.com/"
    private val retrofit = Retrofit.Builder()
        .baseUrl(BASE_URL)
        .addConverterFactory(GsonConverterFactory.create())
        .build()
    fun <T> create(serviceClass: Class<T>): T = retrofit.create(serviceClass)
    inline fun <reified T> create(): T = create(T::class.java)  // 泛型实化
}

// 统一网络入口（11.7.3 模式）
object MyNetwork {
    private val placeService = ServiceCreator.create<PlaceService>()
    suspend fun searchPlaces(query: String) = placeService.searchPlaces(query)
}
// 注：Retrofit 2.6+/3.0 已原生支持 suspend 函数，接口直接声明
// suspend fun searchPlaces(@Query("query") q: String): PlaceResponse
// 书中的 Call<T> + await()/suspendCoroutine 封装仅用于兼容旧 Call 接口
```

## 2. Kotlin 语言要点（第2章 + 各章 Kotlin 课堂）

- 变量：`val` 优先，尽量不可变；`var` 只在必须可变时用
- 函数：单行函数用 `fun foo() = expr`；参数默认值替代重载
- 判空：`?.`、`?:`、`let`；慎用 `!!`；`lateinit` 延迟初始化 + `::x.isInitialized` 判断
- 逻辑：`when` 替代 switch，可做类型判断和带返回值表达式
- Lambda/集合：`filter/map/flatMap/maxByOrNull/sumOf/any/all` 链式处理
- 标准函数：`let`（判空）、`with`（连续操作同一对象）、`run`（let+with 结合）、`apply`（返回自身，初始化用）、`also`
- data class 自动生成 equals/hashCode/toString；`object` 单例
- 扩展函数：`fun String.lettersCount() = ...`；`infix` 函数构建可读语法 `a to b`
- 高阶函数 + `inline` 消除 Lambda 开销；`noinline`/`crossinline` 例外
- 密封类 `sealed class` 配合 when 穷尽分支（适合表示受限结果集）
- 泛型：`out`（协变/生产者）、`in`（逆变/消费者）、`reified` 泛型实化（限 inline）
- 委托：`by lazy`、自定义委托属性、`observable`
- DSL：`html { head { } body { } }` 式结构
- 字符串模板：`"值=$x"`、`"${obj.prop}"`

## 3. Activity（第3章）

- 必须在 AndroidManifest 注册；主 Activity 配 `MAIN`+`LAUNCHER` intent-filter；**Android 12+ 凡带 intent-filter 的组件必须显式 `android:exported="true/false"`**
- 显式 Intent：`Intent(this, SecondActivity::class.java)`；隐式靠 action/category 匹配
- 传数据：`putExtra` 基本类型；对象用 `@Parcelize`+`Parcelable`（优先）或 `Serializable`
- 回传：**`ActivityResultLauncher` + `registerForActivityResult(StartActivityForResult())`**；`startActivityForResult`/`onActivityResult` 已 deprecated
- 生命周期：`onCreate → onStart → onResume → onPause → onStop → onDestroy`；被回收用 `onSaveInstanceState`（或 ViewModel + `SavedStateHandle`）存数据
- 启动模式：standard（默认）/ singleTop（栈顶复用）/ singleTask（栈内复用清上面）/ singleInstance（独立栈共享）
- `ActivityResultLauncher` 写法：
  ```kotlin
  private val launcher = registerForActivityResult(StartActivityForResult()) { result ->
      if (result.resultCode == RESULT_OK) { val data = result.data }
  }
  launcher.launch(Intent(this, SecondActivity::class.java))
  ```
- 最佳实践：`BaseActivity` 记录当前 Activity 便于调试；`ActivityCollector` 随时退出程序；启动目标页用 `companion object fun actionStart(context, data1, data2)` 静态方法

## 4. UI 开发（第4章）

- 布局三件套：LinearLayout（权重 layout_weight 占比）、FrameLayout（层叠）、RelativeLayout（相对定位）；新项目优先 ConstraintLayout
- 常用控件：TextView、Button、EditText（`inputType` 限制键盘）、ImageView（scaleType）、ProgressBar、AlertDialog（用 `MaterialAlertDialogBuilder`）
- 视图绑定：**ViewBinding**（`buildFeatures { viewBinding = true }`，编译期生成 `XxxBinding.inflate`）；Kotlin synthetics 与 ButterKnife 均已废弃；findViewById 仅遗留代码用
- RecyclerView（替代 ListView，必用）：
  - `LinearLayoutManager`（默认纵向，`orientation=HORIZONTAL` 横向）、`StaggeredGridLayoutManager`（瀑布流）、`GridLayoutManager`
  - Adapter 模式：`ViewHolder(itemView)` + `onCreateViewHolder` inflate + `onBindViewHolder` 绑定 + `getItemCount`
  - 点击事件在 `onCreateViewHolder` 里对 itemView/子控件 setOnClickListener
  - ViewHolder 复用机制天然高效，无需手动 convertView 判空
- 自定义控件：`<include layout>` 引入公共布局；继承 View/现有控件写自定义 View
- 9-Patch（.9.png）：左/上边框画拉伸区，右/下边框画内容区，聊天气泡必备
- 限定符适配：`layout-large/`、`res/layout-sw600dp/`（最小宽度限定符，手机平板同代码不同布局）

## 5. Fragment（第5章）

- 静态嵌入：`<fragment android:name>`（或 `FragmentContainerView`，推荐）；动态：`supportFragmentManager.commit { replace(id, frag) }`（fragment-ktx）
- 返回栈：`addToBackStack(null)` 按返回键回退 Fragment
- 生命周期比 Activity 多：`onAttach → onCreate → onCreateView → onViewCreated → ... → onDestroyView → onDetach`（`onActivityCreated` 已 deprecated，逻辑写 `onViewCreated`）
- 与 Activity 通信：Fragment 内 `(activity as? MainActivity)` 或共享 `by activityViewModels()` 的 ViewModel；Activity 内 `findFragmentById`
- 获取 ViewModel 用委托：`private val vm: MyViewModel by viewModels()`（fragment-ktx）
- 平板/手机适配：单页模式（手机）vs 双页模式（平板 `layout-sw600dp` 放两个 Fragment）

## 6. 广播 Broadcast（第6章）

- 动态注册：`registerReceiver`（代码里，跟随组件生命周期）+ 必须 `unregisterReceiver`；**Android 13+ 必须传 flag**：`registerReceiver(r, filter, Context.RECEIVER_NOT_EXPORTED)`（或 `RECEIVER_EXPORTED`）
- 静态注册：manifest `<receiver>` + intent-filter + `android:exported`；Android 8+ 大部分隐式广播禁止静态注册
- 自定义：`sendBroadcast`（标准，同时到）/ `sendOrderedBroadcast`（有序，可 `abortBroadcast` 截断）
- `LocalBroadcastManager` 已废弃 → 同应用内通信用 Flow/SharedFlow/LiveData 替代
- 典型用途：强制下线——`ActivityCollector` 管理所有 Activity + BaseActivity 注册下线广播 + 发广播后 finishAll + 跳登录页

## 7. 数据持久化（第7章 + 13.5 Room）

| 方案 | 场景 | 用法 |
|------|------|------|
| 文件 | 原始文本/二进制 | `openFileOutput`/`openFileInput`，MODE_PRIVATE |
| SharedPreferences | 键值对配置 | `getSharedPreferences("data", MODE_PRIVATE).edit { putString(...) }`；`apply()` 异步提交；**新代码优先 Preferences DataStore**（`dataStore` 委托 + Flow 读取） |
| SQLiteOpenHelper | 复杂关系数据 | `onCreate` 建表、`onUpgrade` 升级；CRUD：`insert/update/delete/query` + `ContentValues` |
| **Room（推荐）** | 现代 ORM | `@Entity` + `@Dao`（@Insert/@Update/@Delete/@Query）+ `Room.databaseBuilder().build()`；升级写 `Migration` 或 `fallbackToDestructiveMigration` |

- SQLite 事务：`beginTransaction → setTransactionSuccessful → endTransaction`
- 升级数据库最佳写法：`onUpgrade` 里 `when(oldVersion) { 1→..., 2→... }` 逐级升级
- 简化写法：`edit {}` 高阶函数封装 SharedPreferences；`contentValuesOf()`（KTX）

## 8. ContentProvider + 运行时权限（第8章）

- 运行时权限（Android 6+ 危险权限必须），新写法用 ActivityResultContract：
  ```kotlin
  private val permissionLauncher = registerForActivityResult(
      ActivityResultContracts.RequestMultiplePermissions()
  ) { grants -> if (grants.all { it.value }) { /* 全部允许 */ } }
  permissionLauncher.launch(arrayOf(Manifest.permission.CAMERA))
  // 旧写法 ActivityCompat.requestPermissions + onRequestPermissionsResult 已 deprecated
  ```
- 存储权限变化：Android 10 分区存储、13+ 细分 `READ_MEDIA_IMAGES/VIDEO/AUDIO` 替代 `READ_EXTERNAL_STORAGE`、14+ 部分照片访问 `READ_MEDIA_VISUAL_USER_SELECTED`
- 访问他方数据：`contentResolver.query(uri, projection, ...)`，Uri 格式 `content://authority/path`
- 自建 Provider：继承 ContentProvider 实现 6 个方法 + manifest `<provider android:authorities>`；`UriMatcher` 匹配路径；`ContentUris.withAppendedId`

## 9. 多媒体（第9章）

- 通知（Android 8+ 必须建渠道；**Android 13+ 还需运行时权限 `POST_NOTIFICATIONS`**）：
  ```kotlin
  val channel = NotificationChannel(channelId, name, NotificationManager.IMPORTANCE_DEFAULT)
  manager.createNotificationChannel(channel)
  val notification = NotificationCompat.Builder(this, channelId)
      .setContentTitle(...).setContentText(...).setSmallIcon(...).build()
  manager.notify(id, notification)
  ```
- 拍照：`FileProvider` 共享 Uri（Android 7+ 文件权限收紧）→ 新写法 `registerForActivityResult(ActivityResultContracts.TakePicture())`；相册选图用 **Photo Picker** `PickVisualMedia`（无需存储权限）或 `ACTION_OPEN_DOCUMENT`
- **PendingIntent mutability（Android 12+ 强制）**：`PendingIntent.getActivity(ctx, 0, intent, PendingIntent.FLAG_IMMUTABLE or FLAG_UPDATE_CURRENT)`
- 播放：音频 `MediaPlayer`；视频 `VideoView`；**新项目用 Media3 ExoPlayer**（`ExoPlayer.Builder(context).build()` + `PlayerView`）

## 10. Service + 多线程（第10章）

- 子线程更新 UI 三选一：`runOnUiThread` / `view.post` / Handler 消息机制（sendMessage + handleMessage）
- AsyncTask 已废弃，新项目用协程
- Service：`startService`/`stopService`；`bindService` + Binder 通信（前台调后台方法）；生命周期 `onCreate → onStartCommand → onDestroy`
- 前台 Service（防被杀）：`startForeground(id, notification)`；**Android 10+ manifest 需 `foregroundServiceType`（camera/location/dataSync 等）+ 14+ 必须声明对应权限**
- ~~IntentService~~ 已废弃（连同 JobIntentService）→ 用 **WorkManager**（`CoroutineWorker`）或 `CoroutineScope` 替代

## 11. 网络（第11章）

- 首选 **Retrofit**：接口注解定义 API
  ```kotlin
  interface ApiService {
      @GET("path/{id}")
      suspend fun getData(@Path("id") id: String, @Query("q") q: String): Data  // suspend 直接返回，Retrofit 2.6+/3.0 原生支持
      @POST("path")
      suspend fun post(@Body body: Req): Resp
  }
  // 旧写法返回 Call<T> + enqueue/await 封装仅兼容场景用
  ```
  - `@Path` 路径占位、`@Query`/`@QueryMap` 查询参数、`@Body` JSON body、`@Headers/@Header` 头、`@FormUrlEncoded`+`@Field` 表单
- 底层 OkHttp：`OkHttpClient().newCall(request).execute()`（同步）/ `enqueue`（异步）
- JSON 解析：**Gson** `fromJson`/`toJson` 自动映射 data class，字段名不一致用 `@SerializedName`
- 协程处理网络：接口声明 `suspend` 函数直接返回（§1）；`Dispatchers.IO` 切线程；`viewModelScope`/`lifecycleScope` 自动取消
- WebView：`settings.javaScriptEnabled = true` + `WebViewClient` 页面内跳转

## 12. Material Design（第12章）

- 主题 parent：View 体系 `Theme.MaterialComponents.*` / `Theme.Material3.*`；Compose 用 Material3 组件库
- Toolbar 替代 ActionBar：`setSupportActionBar(toolbar)` + menu `app:showAsAction`
- DrawerLayout + NavigationView 侧滑菜单
- FloatingActionButton 悬浮按钮（`app:elevation` 高度阴影）
- Snackbar 替代 Toast（可交互，带 action 按钮）；需 CoordinatorLayout 父容器联动 FAB 上移
- MaterialCardView 卡片（圆角 app:cardCornerRadius + 阴影 app:cardElevation）
- AppBarLayout + CollapsingToolbarLayout 折叠标题栏（配合 NestedScrollView + `app:layout_scrollFlags`）
- SwipeRefreshLayout 下拉刷新：`setOnRefreshListener` + `isRefreshing = false` 结束

## 13. Jetpack（第13章）

- **ViewModel**：`private val vm: MyViewModel by viewModels()`（ktx 委托，推荐）或 `ViewModelProvider(this)[MyViewModel::class.java]`；屏幕旋转数据不丢；`onCleared` 释放资源；传参用 `ViewModelProvider.Factory` 或 `viewModelFactory`
- **Lifecycle**：`lifecycleScope` 协程作用域；**`repeatOnLifecycle(STARTED)` 收集 Flow**（官方推荐的 UI 收集模式）；`@OnLifecycleEvent` 已 deprecated → 用 `DefaultLifecycleObserver`
  ```kotlin
  lifecycleScope.launch {
      repeatOnLifecycle(Lifecycle.State.STARTED) {
          vm.uiState.collect { state -> /* 更新 UI */ }
      }
  }
  ```
- **LiveData**：可观察数据持有类，自动跟随生命周期、防内存泄漏（新代码也可用 StateFlow + repeatOnLifecycle 替代）
  ```kotlin
  val counter = MutableLiveData(0)
  counter.observe(this) { value -> textView.text = value.toString() }
  counter.value = counter.value?.plus(1)
  // Transformations.map 转换、switchMap 切换源；Flow 对应 map/flatMapLatest
  ```
- **Room**：见 §7
- **WorkManager**：后台定时任务（即使应用退出也保证执行）
  ```kotlin
  val request = OneTimeWorkRequest.Builder<SimpleWorker>()
      .setInitialDelay(5, TimeUnit.MINUTES).build()
  WorkManager.getInstance(context).enqueue(request)
  // Worker: override fun doWork(): Result { ...; return Result.success() }
  ```

## 14. 高级技巧（第14章）

- 全局 Context：`Application` companion `lateinit var context`（§1）
- Intent 传对象：`@Parcelize data class X(...) : Parcelable` 一行搞定（需 `kotlin-parcelize` 插件，`plugins { id("kotlin-parcelize") }`）
- 日志工具封装：自定义 Log 类 + `level` 开关，release 版关掉 verbose；新项目直接用 **Timber**（`Timber.plant(DebugTree())`）
- 深色主题：`values-night/` 资源目录 + `AppCompatDelegate.setDefaultNightMode`

## 15. 常用依赖清单（2026-09 最新稳定版）

```kotlin
dependencies {
    // ===== Kotlin & 协程 =====
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")

    // ===== AndroidX 基础 =====
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("androidx.activity:activity-ktx:1.13.0")
    implementation("androidx.fragment:fragment-ktx:1.9.0")
    implementation("androidx.recyclerview:recyclerview:1.4.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.1")
    implementation("com.google.android.material:material:1.13.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0") // 已停更，新项目用 Compose pullToRefresh

    // ===== Jetpack 架构组件 =====
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.11.0")
    implementation("androidx.lifecycle:lifecycle-livedata-ktx:2.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")  // lifecycleScope
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0") // Compose 里 collectAsStateWithLifecycle
    implementation("androidx.navigation:navigation-fragment-ktx:2.10.1")
    implementation("androidx.navigation:navigation-ui-ktx:2.10.1")
    implementation("androidx.paging:paging-runtime-ktx:3.5.1")

    // ===== Room（注意新坐标 room3）=====
    implementation("androidx.room:room-runtime:2.8.5")
    implementation("androidx.room:room-ktx:2.8.5")
    ksp("androidx.room:room-compiler:2.8.5")
    // 新项目可直接用 Room3：androidx.room3:room3-runtime:3.0.3（必须 KSP）

    // ===== DataStore（替代 SharedPreferences，推荐）=====
    implementation("androidx.datastore:datastore-preferences:1.2.1")

    // ===== WorkManager =====
    implementation("androidx.work:work-runtime:2.11.2") // 2.11+ 起 ktx 已合并，CoroutineWorker 在主包

    // ===== 网络 =====
    implementation("com.squareup.retrofit2:retrofit:3.0.0")
    implementation("com.squareup.retrofit2:converter-gson:3.0.0")
    implementation(platform("com.squareup.okhttp3:okhttp-bom:5.4.0"))
    implementation("com.squareup.okhttp3:okhttp")
    implementation("com.squareup.okhttp3:logging-interceptor")
    implementation("com.google.code.gson:gson:2.13.1")
    // 或用 kotlinx-serialization + retrofit kotlinx-serialization-converter
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.9.0")

    // ===== 依赖注入（Hilt，官方推荐）=====
    implementation("com.google.dagger:hilt-android:2.57.2")
    ksp("com.google.dagger:hilt-android-compiler:2.57.2")
    implementation("androidx.hilt:hilt-navigation-compose:1.4.0")

    // ===== 图片加载 =====
    implementation("io.coil-kt.coil3:coil:3.3.0")           // Coil3（Kotlin 协程，推荐）
    implementation("io.coil-kt.coil3:coil-compose:3.3.0")   // Compose 用
    implementation("io.coil-kt.coil3:coil-network-okhttp:3.3.0")
    // 或 Glide：com.github.bumptech.glide:glide:5.0.4 + ksp compiler

    // ===== Jetpack Compose（2026 主流 UI 方案，书中未覆盖）=====
    implementation(platform("androidx.compose:compose-bom:2026.08.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")  // Material You
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // ===== 多媒体/相机（替代书中 VideoView/MediaPlayer 直操）=====
    implementation("androidx.media3:media3-exoplayer:1.11.0")   // ExoPlayer 合并入 Media3
    implementation("androidx.media3:media3-ui:1.11.0")
    implementation("androidx.camera:camera-camera2:1.6.1")      // CameraX
    implementation("androidx.camera:camera-lifecycle:1.6.1")
    implementation("androidx.camera:camera-view:1.6.1")

    // ===== 工具 =====
    implementation("com.jakewharton.timber:timber:5.0.1")       // 日志（替代 §14.3 自封装）
    debugImplementation("com.squareup.leakcanary:leakcanary-android:2.14") // 内存泄漏检测
    implementation("com.tencent:mmkv:2.2.4")                    // 高性能键值存储
}
```

**关键变化（相对 2020 年书中版本）**：

- `work-runtime-ktx` 已废弃并入 `work-runtime`，`CoroutineWorker` 直接用主包
- Room 推出 `androidx.room3` 新坐标（3.0.x），老坐标 2.8.x 仍维护
- Retrofit 3.0 / OkHttp 5.x（KMP 化，用 `okhttp-bom` 统一管理）
- SharedPreferences → **Preferences DataStore**（协程/Flow，官方推荐替代）
- ListView/自封装 Log → 彻底淘汰；日志用 Timber
- UI 新范式：**Jetpack Compose + Material3**（BOM 统一版本），View 体系仍在维护（material 1.13）
- ExoPlayer → `androidx.media3`；相机用 CameraX；通知权限 Android 13+ 需 `POST_NOTIFICATIONS` 运行时申请

## 使用指引

- 回答 Android 问题时默认 Kotlin 语法，遵循本书风格（简洁、单例 object、扩展函数、lateinit）
- 涉及列表用 RecyclerView，不用 ListView；涉及架构用 MVVM 分层（§1）
- 网络请求统一走 Retrofit `suspend` 函数 + 协程；禁止主线程网络
- 持久化选型按 §7 表格；新项目数据库优先 Room 而非裸 SQLiteOpenHelper
- 权限、通知渠道、FileProvider 注意版本适配（Android 6/7/8/13 各有限制点）；Android 13+ 通知本身也要运行时权限 `POST_NOTIFICATIONS`，Android 14+ 前台 Service 需声明类型
- 版本细节以项目实际 SDK/依赖版本为准，本书出版于 2020 年（Android 10 时代），API 28+ 新特性（如 Activity Result API、Photo Picker、分区存储、Predictive Back）书中未覆盖时按官方最新文档补充
- **书中未覆盖的 2026 主流技术**：
  - **Jetpack Compose** — 声明式 UI 已是官方主推，新项目优先 Compose + Material3；存量 View 项目可互操作（ComposeView / ViewCompositionStrategy）
  - **Preferences DataStore** — SharedPreferences 的官方替代，Flow + 协程 API
  - **Hilt** — 官方推荐 DI 方案（@HiltAndroidApp / @AndroidEntryPoint / @HiltViewModel + hiltViewModel()）
  - **StateFlow/SharedFlow** — LiveData 之外的可选方案，ViewModel 层暴露 StateFlow 更常见（UI 用 `collectAsStateWithLifecycle` 或 `repeatOnLifecycle` 收集）
  - **Kotlinx Serialization** — Gson/Moshi 之外的官方 JSON 方案
  - **Media3 / CameraX** — 替代书中 MediaPlayer/VideoView/Camera2 直操
  - **Coil3** — Kotlin 协程图片加载，替代 Glide/Picasso
