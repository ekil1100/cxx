# CXX 实现计划 v1.0

**需求依据**：[CXX_PRD_v1.0.md](CXX_PRD_v1.0.md)  
**架构依据**：[CXX_ARCHITECTURE_v1.0.md](CXX_ARCHITECTURE_v1.0.md)  
**状态**：建议计划，工作量、人选、版本下限及 OPEN 决策尚待确认。当前仓库没有实现，文中测试是待建设的验收项。

## 1. 交付原则与路线

目标是先交付可运行闭环，再扩展覆盖面。每个里程碑必须保留上一阶段通过的构建、测试与锁定行为；不以“Interface 已定义”替代功能交付。

- V0 允许 Linux/Clang、小规模范围；V1 必须完成 PRD 所有 MUST。
- 完整 V1 包括三平台、原生包模型、两类 Bridge、Modules/PCH/Unity、远程缓存与开发接口；实施中如需削减，必须单独修改产品范围，不在排期中默默降级。
- correctness-first：先用保守扫描/身份策略保证结果，再优化空构建与缓存命中率。
- 每阶段交付可运行程序、fixture、自动化测试、用户示例与已知限制；公开格式只在相应门槛后冻结。
- 不编造工期。采用依赖图和工作包排序；团队人数、投入比例、发行渠道及性能预算确认后再换算日历排期。

```mermaid
flowchart LR
    P[关键验证原型] --> M0[M0 可运行 V0]
    M0 --> M1[M1 可移植核心]
    M1 --> M2[M2 工具链与 Bootstrap]
    M2 --> M3[M3 包与 Lock]
    M3 --> M4[M4 本地与远程缓存]
    M4 --> M5[M5 现代 C++ 与开发体验]
    M5 --> M6[M6 V1 发布验证]
```

图表示集成验收顺序，不要求所有研发串行。模块能力探针、远程协议 fixture、Query Schema 草案可提前开展；M4 正式验收依赖 M3 的 Package/Toolchain 身份，M5 的 Modules 原型必须在 M0 前后启动，避免到最后才发现编译器边界。

## 2. 需求追踪矩阵

等级解释：MUST 为 PRD 已明确必须或最低验收行为；SHOULD 为明确建议；FUTURE/OUT 为后续方向或 V1 排除；OPEN 的确认流程独立列出。相关条目合并成测试族，具体参数化用例在实现阶段分解。

### 2.1 MUST 与强约束

| ID | PRD 来源 | 验收行为 | 架构位置 | 里程碑 | 测试族 |
|---|---|---|---|---|---|
| R01 | §1、4.1、6.1 | C++ 脚本、固定入口、自有 DAG/Scheduler，无默认外部执行后端 | §2–3 | M0 | T01 原生闭环 |
| R02 | §3、4.2 | TOML/脚本/Lock/状态职责分离；Header 拆分；结构化真实工具路径 | §3–5 | M0/M2 | T02 Bootstrap |
| R03 | §3.1、4.3、4.4 | Core C++17，脚本声明标准、Target 覆写；Edition/Query/Lock 分离 | §3.2–3.3 | M0/M2/M5 | T03 标准与兼容 |
| R04 | §3.3 | 内置/自定义 preset、项目优先、无继承、显式路径及组件覆盖 | §4.1 | M2 | T04 配置矩阵 |
| R05 | §3.4 | 全字符串变量展开、缺失报错、CLI 变量覆盖、脱敏及身份追踪 | §4.2 | M2 | T05 展开与秘密 |
| R06 | §3.5 | 自动安装/复用、全局默认、管理命令、LLVM 组件集、无 PATH/root 修改 | §4.3 | M2 | T06 安装生命周期 |
| R07 | §3.2、3.5、7.3 | fork/stdlib/SDK 内容身份；版本约束；不以 CLI 版本常规约束项目 | §3.3、4.3、7 | M2/M4 | T07 工具身份 |
| R08 | §4.3、6.1 | Executable/Static/Shared/Object、可见性、链接、生成文件、测试、运行、安装 | §5.1–5.2 | M0/M1 | T08 目标语义 |
| R09 | §4.3、10.12 | 统一异常诊断，脚本失败和状态一致性 | §3.2、7.4 | M0/M6 | T09 错误恢复 |
| R10 | §4.5 | Linux/macOS/Windows，Host/Target、CLI Target 覆盖、SDK/sysroot、交叉编译 | §3.2、4.3、9 | M1/M2/M6 | T10 平台矩阵 |
| R11 | §5.1 | 统一 Package Interface，Registry/Git/URL/Local/Conan/vcpkg，无 system 包 | §6.1–6.2 | M3 | T11 来源与污染 |
| R12 | §5.2 | 版本、传递依赖、冲突、源码/二进制、校验、Store、ABI 回退、强制源码 | §6.1–6.3 | M3 | T12 包解析与构建 |
| R13 | §5.2 | Bridge 真实解析入统一 Lock，原生 Registry 可落地 | §6.2、6.4 | M3 | T13 Bridge 回锁 |
| R14 | §5.3 | 单 Lock、首次生成、普通不升级、locked 不写、install 补项、定向 update | §6.4 | M2/M3 | T14 Lock 状态机 |
| R15 | §5.3 | offline 零网络、失败复用验证对象、Git Commit/摘要、本地变更追踪 | §6.2、6.4 | M3/M4 | T15 离线与本地包 |
| R16 | §6.1、6.4 | 统一 Action，循环/缺输入/多写者诊断；Custom 工具和输入显式声明 | §5、7 | M1 | T16 图约束 |
| R17 | §6.2 | -j、内存预算、关键路径、用户/IDE 优先级、防饥饿 | §9.1 | M1/M5 | T17 资源调度 |
| R18 | §6.2 | 默认停止准入、keep-going、失败后继阻塞、Ctrl-C 进程树和状态 | §9.2 | M1/M6 | T18 失败与取消 |
| R19 | §6.3 | 空构建无 Compile/Link、源码/头变更最小失效、动态依赖正确 | §7.1–7.2 | M0/M4 | T19 增量反例 |
| R20 | §6.3 | Modules Interface/Partition/Import、BMI、PCH、Unity 排除/批大小/CLI | §7.2 | M5 | T20 现代 C++ |
| R21 | §6.3 | 多 Profile、相同 Action 去重、Sanitizer/LTO/PGO 冲突诊断 | §5.2、7.1 | M1/M5 | T21 配置组合 |
| R22 | §6.3 | build/test/run Watch、防抖、重新规划、重启 | §10.3 | M5 | T22 Watch 代次 |
| R23 | §6.5 | --out/-o、clean/all、cache gc/clear、安全边界 | §7.4 | M1/M4 | T23 清理与所有权 |
| R24 | §6.5 | TTY/CI 日志、verbose、JSON、Explain | §10.2–10.3 | M1/M5 | T24 诊断输出 |
| R25 | §6.5、11 | 性能报告、关键路径/资源/缓存/传输、Trace、Graph DOT/JSON | §9–10 | M1/M5 | T25 可观测性 |
| R26 | §7.1 | 共享 CAS、ActionCache/PackageStore/ToolchainStore 分层 | §7.4 | M3/M4 | T26 Store 引用 |
| R27 | §7.2 | Native HTTP(S) 读写/只读、TLS、认证、摘要、并发、压缩、Namespace | §8 | M4 | T27 远程协议 |
| R28 | §7.2–7.3 | 缓存来源信任、凭据隔离、封闭输入、完整 Key、远程结果验证 | §7–8 | M4 | T28 缓存攻击与污染 |
| R29 | §8.1 | 版本化 Query、刷新/失效、完整模型字段、compile_commands 与 clangd | §5.3、10.1 | M1/M5 | T29 Query 合约 |
| R30 | §8.2 | validate、dry-run、doctor、JSON 与动态盲区；不自动修复 | §10.2 | M2/M5 | T30 诊断边界 |
| R31 | §9 | new/configure/build/run/test/install/update 等 CLI 与最小项目模板 | §10.2 | M0–M5 | T31 CLI 场景 |
| R32 | §10.12 | 中断、损坏、下载失败、部分失败不破坏 Lock 和有效产物 | §6.4、7.4、9.2 | 各阶段/M6 | T32 故障注入 |
| R33 | §11 | 自动化全量/空/增量/头/Modules/PCH/Unity/Profile/缓存基准与对照 | §11 | M0/M6 | T33 性能基准 |

测试族中的“最小失效”以实际依赖语义为准；允许正确性所需的依赖扫描，不允许将每次全量编译伪装成增量构建。模块相关组合仅在受支持能力矩阵中要求成功，不支持的组合必须执行前明确诊断。

### 2.2 SHOULD、FUTURE 与范围排除

| ID | 级别 | PRD 来源 | 处理 |
|---|---|---|---|
| S01 | SHOULD | §3.2、4.4 | 稳定 Edition 下源码兼容；构建命令不因 CLI 升级无故变化，维护跨版本 fixture |
| S02 | SHOULD | §5.3 | CI 推荐 locked；初版显式配置，不擅自基于环境自动改变行为 |
| S03 | SHOULD | §8.1 | 模型可复用，前提是声明输入可验证；默认刷新避免陈旧 |
| F01 | FUTURE | §6.1 | Ninja 导出可后续实现，V1 无此依赖 |
| F02 | FUTURE | §7.2 | Bazel HTTP/REAPI Adapter 先做差异实验，完整兼容深度另立计划 |
| F03 | FUTURE | §7.4 | RemoteExecutor 仅保留请求/结果接缝，无 Worker、RPC 或远程调度实现 |
| F04 | OPEN/FUTURE | §8.1 | 官方 IDE 插件另定功能和日期；V1 Query 与编译数据库照常交付 |
| X01 | OUT | §1.3 | Task Runner、Hooks、通用 Rule DSL、复杂 preset 继承与表达式排除 |
| X02 | OUT | §1.3、4.5 | 完整 SDK 安装平台、CMake 自动迁移器、doctor 自动修复排除 |
| X03 | OUT | §1.3、6.3 | 自研编译器/链接器/增量链接算法、严格字节复现与二次验证模式排除 |
| X04 | OUT | §4.1、7.4 | 自动多 TU Driver、图 IPC/daemon、容器/安全沙箱执行排除 |
| X05 | 边界 | §6.3 | 编译器插件走普通参数并追踪工具输入；不新增专用插件 API |

### 2.3 OPEN 跟踪

PRD §13 的 12 个 OPEN 均映射到架构 §12 的 D01–D12；额外 D13 记录清理语义张力。确认负责人角色如下：

- 产品负责人：D02 脚本 Modules/脚本依赖范围、D03 环境优先级、D04 Host 选择、D06 信任与 Registry 交付、D10 内存限制语义、D13 清理边界。
- 核心负责人：D01/D02/D04/D08/D10/D11 的技术选择与兼容成本。
- 依赖负责人：D05/D06/D07 的来源、版本、ABI 和 Lock 行为。
- 发行/基础设施负责人：D05 的可信发布物、D09 的端点与认证、D12 的资源排期。

一个人可兼任多角色。任何确认都要更新 ADR 或决策表状态并附对应证据，不以任务完成状态代替产品决定。

## 3. 前置原型与风险消减

原型保存为可重复 fixture，结论必须记录“实际工具版本、命令、平台、结果、限制”。没有实测就保持待验证。

| 原型 | 问题与实验 | 成功证据 | 失败后的处理 | 最晚门槛 |
|---|---|---|---|---|
| P01 Driver ABI | C++17 Runtime 与 C++17/20/更新模式脚本直接链接；异常/字符串/容器/分配释放；Windows CRT；Host 启动 | 三平台各一套可重复构建；ABI 不匹配被拒绝 | 收紧发行 ABI 矩阵或改用同工具链源码构建，禁止绕过校验 | M0 初证，M2 完整 |
| P02 声明与 Lock | 单次脚本声明包句柄、元数据传递依赖、配置分域；两个进程竞争生成 Lock | 无重跑求解；旧项稳定；崩溃点前后 Lock 均有效 | 调整阶段 Interface/事务，M3 前冻结 | M0/M3 |
| P03 动态输入 | 头文件遮蔽、缺失变存在、同 mtime 改内容、SDK/fork 改内容、路径差异 | 增量与干净构建一致；不存在错误命中 | 回退完整扫描和路径敏感键，性能优化后置 | M0/M4 |
| P04 Modules | 至少一套工具链完成 Interface/Partition/Import、生成输入扫描、BMI 生命周期 | 可构建且变更传播正确；PCH/Unity 能力边界清楚 | 调整 Adapter/支持矩阵，不把目标 Modules 从 V1 移除 | M0 启动，M5 完整 |
| P05 Remote | 最小 HTTPS 服务实现 CAS/result；中断、鉴权、错误摘要、缺 blob | 拒绝污染结果、可本地回退；读写权限隔离 | 修正原生协议后再冻结 | M4 |
| P06 Package Bridge | 同一 fmt fixture 经原生、Conan、vcpkg 导入；锁定传递图和 ABI | 第二台/洁净环境可重建，无系统库污染 | 缩小明确支持的外部语义，保留两类桥接交付与限制说明 | M3 |
| P07 交叉构建 | Host 生成器产生源码，异构 Target 工具链构建库/程序 | Host 工具可运行，Target 产物不在 Host 误执行，Lock 分域 | 完善角色模型和显式 Host 配置 | M2/M6 |
| P08 进程与发布 | 多子进程、Windows 占用文件、多输出动作中断、GC 并发 | 无孤儿进程；旧有效结果可用；GC 不删活动对象 | 修正代次发布与平台实现 | M1/M4 |

最高风险不是“功能多”，而是错误缓存结果看起来成功，以及 ABI/包解析阶段设计造成的后期返工。P01–P04 是最先验证的设计假设。

## 4. 里程碑工作包、验收与退出条件

### M0 — 可运行 V0：Linux + Clang 的原生闭环

**输入**：PRD、D01/D02 初步选择、可用 Linux/Clang 环境。

工作包：

1. `M0.1` 建立 Core C++17 种子构建、单元测试、CI、fixture runner；固定开发依赖。
2. `M0.2` 最小 `cxxrc.toml` 解析，SDK 头文件/Runtime 与 Driver 编译，`void build(Build&)` 调用。
3. `M0.3` Executable/StaticLibrary、Source/Include/Define、目标引用、Compile/Archive/Link DAG。
4. `M0.4` Linux 进程执行、顺序调度、标准错误与退出码；`new/build/run`。
5. `M0.5` 内容指纹、头文件扫描、基本增量；Driver include 改动失效。
6. `M0.6` 跑 P01/P02/P03 最小实验，启动 P04，记录全量/空/单源码增量基线。

**可运行交付**：`new` → `build` → `run` Hello World，以及 app 链接自有静态库。

**验收**：T01/T02/T08/T09/T19 的 V0 子集。空构建 Compile/Link 数为 0；改一个 `.cpp` 只编译相关单元并链接；修改被 include 的脚本头使 Driver 重建；源代码错误给出真实诊断。

**退出条件**：端到端 fixture 通过，Driver ABI 与阶段闭环没有未解决的根本阻碍。V0 不承诺包管理、远程缓存或三平台能力；这些限制必须写入版本说明。

### M1 — 可移植核心与完整 Target 语义

**依赖**：M0。

工作包：

1. `M1.1` POSIX/Windows Process、Filesystem、进程树取消、响应文件、Unicode/长路径；三平台持续构建。
2. `M1.2` SharedLibrary/ObjectLibrary、可见性、生成文件、Custom Action、测试、安装、运行时布局。
3. `M1.3` 就绪队列、`-j`、资源估计、内存预算、关键路径与防饥饿、keep-going。
4. `M1.4` 不可变模型、Profile 展开、多 Profile 基础去重、图验证和 DOT/JSON。
5. `M1.5` 输出所有权、暂存/发布、clean/all、项目互斥和恢复日志。
6. `M1.6` 统一诊断事件、TTY/行式/JSON；Query 与编译数据库最小切片。

**交付**：三平台可运行多目标项目，支持本地显式工具链；Query 可枚举目标和编译命令。

**验收**：T08/T10/T16/T17/T18/T21/T23/T24/T29。重点覆盖静态库私有链接依赖、菱形传播、Windows DLL/import library、多写者与环、失败后继阻塞、取消子进程树。

**退出条件**：三平台基础编译/链接 smoke 必须通过；资源预算语义已确认；不以 Linux 通过代表 Windows 进程与文件发布正确。

### M2 — Toolchain、Bootstrap 与工具链 Lock

**依赖**：M1；D03/D04/D05/D07 确认。

工作包：

1. `M2.1` 严格 Schema、环境展开算法、preset 层级与组件合并、能力诊断。
2. `M2.2` 工具链版本解析、发行清单、下载/校验/安全解包、托管 Store；全局默认与管理命令。
3. `M2.3` fork/stdlib/SDK 身份、真实工具路径、标准模式探针；完整 LLVM preset 发布物。
4. `M2.4` Lock 格式与原子事务，工具链部分、锁定/离线/显式更新；未知格式处理。
5. `M2.5` Host/Target 分离、交叉目标 Interface、CLI Target 覆盖、Host 生成器。
6. `M2.6` configure/validate/doctor 的 Bootstrap 检查；构建 CXX 自举 fixture。

**交付**：干净用户目录下安装并构建，重复构建复用已验证工具链；项目不改变系统 PATH。

**验收**：T03–T07/T10/T14/T30。断网、半下载、损坏归档、路径穿越、同版本 fork 二进制替换、变量缺失/秘密、preset 重名、组件改名、`--locked` 文件字节不变。

**退出条件**：Core 最低编译器版本实测后冻结；每个验收平台的工具链来源与必要 SDK 前置条件有文档；工具链缺失的错误路径与安装行为都通过。无法提供合法、可验证发布物属于发布阻塞。

### M3 — 原生 Package、Bridge 与完整 Lock

**依赖**：M2；P02/P06，D06/D07 确认。

工作包：

1. `M3.1` PackageRequirement/Instance、版本约束求解、传递冲突路径、Host/Target 域。
2. `M3.2` 静态 HTTPS Registry Schema、元数据验证、最小发布/镜像工具和本地测试 Registry。
3. `M3.3` Git/URL/Local 来源、源码校验、安全解包；原生包 recipe 和导出 Target 映射。
4. `M3.4` PackageStore、二进制精确 ABI 匹配、源码回退、强制源码构建。
5. `M3.5` Conan/vcpkg 各一个版本化 Adapter，明确支持范围与不支持错误。
6. `M3.6` 工具链/包统一 Lock，首次生成、声明变化、新平台集合、install、定向 update、offline。

**交付**：以 fmt fixture 展示首次拉取、锁定、源码构建、兼容二进制复用及两类 Bridge；提供最小传递依赖冲突示例。

**验收**：T11–T15/T26。机器预装不同版本同名系统库仍得到相同解析；包下载/recipe 失败不写部分 Lock；Git 标签移动不改变有效锁定构建；Local Package 改动触发必要动作；只更新指定范围及必需闭包。

**退出条件**：原生 Registry 流程成立，Bridge 的实际依赖身份进入 Lock；二进制 ABI 不匹配确实源码回退。官方 Registry 服务上线日期可独立，但 V1 必须有可部署、可镜像且经过验收的原生协议和发行渠道决定。

### M4 — 正确的共享缓存与 Native Remote Cache

**依赖**：M3 的完整身份；P03/P05/P08，D08/D09 确认。

工作包：

1. `M4.1` SHA-256 CAS/tree、Action Key 规范编码、ActionResult 清单、本地索引和并发发布。
2. `M4.2` 动态扫描先于命中、负依赖/搜索路径失效、生成输入重新规划；非封闭动作策略。
3. `M4.3` 路径映射、跨项目复用、多配置 Action 去重验证；BMI/PCH 的键接缝。
4. `M4.4` HTTP(S) Adapter、TLS/认证/Namespace、只读、并发传输、压缩及脱敏。
5. `M4.5` 参考测试服务、错误注入、完整性验证与回退；禁止将远程缓存当可信执行环境。
6. `M4.6` 租约/引用、GC/clear、临时对象恢复和多输出事务。
7. `M4.7` Bazel HTTP/REAPI 差异报告，仅实验，不挤占原生协议验收。

**交付**：两个不同根目录项目安全共享本地编译结果，干净输出目录可从受信远程缓存恢复。

**验收**：T07/T19/T23/T26–T28/T32。新增更靠前同名头、`__has_include` 翻转、同 mtime 改内容、编译器/SDK/插件变更、恶意输出路径、错误 digest、缺 blob、401/403/429/5xx、证书失败、跨 Namespace 访问、只读零 PUT、offline 零请求、GC 与构建并发。

**退出条件**：不出现已知错误 Cache Hit；无法证明安全的配置明确降级并解释原因。缓存命中必须校验并完整恢复，速度指标不能豁免正确性失败。

### M5 — 现代 C++、Watch 与完整开发接口

**依赖**：M4；P04 完成，D10/D11 确认。

工作包：

1. `M5.1` Clang/GCC/MSVC 的能力驱动扫描 Adapter，Modules/Partition/BMI、PCH、Unity。
2. `M5.2` 多 Profile 完整去重，Sanitizer/LTO/PGO 组合、PGO 输入与原生增量链接状态处理。
3. `M5.3` 三平台 Watch、防抖、事件溢出恢复、配置/脚本/Local Package 变更、run/test 重启。
4. `M5.4` Query Schema v1、协商、快照状态和全字段；各配置 compile_commands、clangd 集成 fixture。
5. `M5.5` validate/dry-run 动态盲区、doctor、Explain、完整 Graph 和 JSON 诊断。
6. `M5.6` 耗时/关键路径/资源/缓存/传输报告，Chrome Trace/Perfetto 数据；CLI 帮助冻结。

**交付**：至少一个真实项目同时包含 Module Target、PCH 消费者和 Unity 普通源文件；发布可运行组合矩阵，而不是声称所有源文件可同时启用所有特性。

**验收**：T20–T22/T24/T25/T29/T30/T31。Module 重名/环/缺失、Partition 更新、生成头改变 import、BMI 参数变化、Unity 排除、PCH 失效、Profile 冲突、快速连续保存、事件丢失、旧代次完成晚于新代次、Query 陈旧状态、clangd 对生成头解析。

**退出条件**：Modules/PCH/Unity 各能力与至少一组共存场景通过；不支持的组合提前诊断；所有 PRD Developer Tools 可用。官方 IDE 插件保持独立范围。

### M6 — 跨平台验收与 V1 发布

**依赖**：M0–M5 全部退出，所有阻塞 OPEN 已确认。

工作包：

1. `M6.1` 三平台真实工具链/SDK 矩阵；大小写敏感差异、非 ASCII/空格/长路径、离线与受限权限。
2. `M6.2` 故障注入全链路：Driver 失败、进程终止、断电模拟、缓存损坏、网络中断、部分构建失败。
3. `M6.3` 旧 Edition/Query/Lock fixture、迁移与未知版本错误；CLI 升级后行为稳定性。
4. `M6.4` 自动化性能基准和 CMake+Ninja 对照；记录资源而非预设领先比例。
5. `M6.5` 发行包、可信下载元数据、许可证/SBOM、用户手册、迁移验证示例、支持矩阵。

**退出条件**：下节 PRD 12 场景全部通过；T01–T33 的 V1 用例全部有 CI 或可重复平台证据；没有未解释的缓存污染、Lock/产物损坏、跨平台基础功能缺口。剩余非阻塞问题附限制与后续计划。

## 5. PRD 最低验收情景对应

| PRD §10 | 最终证明 | 主要里程碑 |
|---|---|---|
| 1 Bootstrap | 三系统 new/build/run；声明语言模式与真实环境一致 | M2/M6 |
| 2 Build Graph | 多类库/程序、生成源、可见性、测试/安装、环/冲突诊断 | M1 |
| 3 Incremental | 空构建零编译/链接；源码/公共头/工具/参数失效精确 | M0/M4 |
| 4 Package | 原生和 Bridge 的 fmt 与传递依赖；系统安装污染测试 | M3 |
| 5 Lock | 首次、locked、update、offline 全状态机 | M3 |
| 6 Toolchain | 官方/自定义 preset、覆盖、fork、变量、自动安装 | M2 |
| 7 Modern C++ | 一组 Modules/PCH/Unity 共存，能力限制诊断 | M5 |
| 8 Cache | 本地跨项目、远程命中/失败/权限/校验/离线/读写 | M4 |
| 9 Scheduler | 并行、keep-going、多配置、资源预算、关键路径与防饥饿 | M1/M5 |
| 10 Developer Tools | Query/clangd、验证/诊断、图、Explain、Trace | M5 |
| 11 Watch | 源码/配置变化、事件聚合、正确重规划与重启 | M5 |
| 12 稳定性 | 故障前后 Lock 合法且旧产物保留、无部分缓存结果 | 各阶段/M6 |

## 6. 团队职责与可并行工作

人名与人力未知，使用职责槽而非假定团队规模。单人按里程碑顺序执行；多人在公共数据模型冻结后并行。

| 职责 | 主责工作包 | 对外依赖与交接件 |
|---|---|---|
| A 核心/Driver | M0、M1.2/1.4、M5.2 | SDK、模型 DTO、Action 规范、诊断类型 |
| B 平台/工具链 | M1.1/1.5、M2、M5.1/5.3 | Process Interface、Toolchain 身份、能力表、扫描 DTO |
| C 包/解析 | M3 | PackageGraph、导出 Target、Lock 候选事务 |
| D 引擎/缓存 | M1.3、M4 | Executor 请求/结果、规范 Key、CAS/result 协议 |
| E 开发体验/质量 | M1.6、M5.4–5.6、M6 | Query Schema、fixture runner、兼容矩阵、基准报告 |

关键交接顺序：

1. A/B 先确定 Host Driver 与 Action/Toolchain 值类型，D 才实现稳定调度和键编码。
2. A/C 确定声明句柄与包导出绑定，C 才冻结求解/Lock，不让 Registry 协议反过来决定 Core Target 模型。
3. B/D 共同签字确认输入闭包和扫描失效，之后开放跨项目/远程缓存。
4. A/E 确认模型快照与版本策略后冻结 Query，E 不读取 Runtime 内部数据库。
5. 平台验收贯穿 M1 后所有阶段，M6 负责收敛而不是第一次运行 Windows。

## 7. 验证设计与质量门槛

### 7.1 测试层次

- 单元/性质测试：配置展开、版本约束、图拓扑、UsageRequirements、规范编码、路径校验、优先级老化、Lock 状态机。
- 模块合约测试：Compiler/Package/Cache Adapter 均运行同一请求/结果约束；不只依赖 mock。
- 集成测试：真实编译器和临时 HOME/项目/Registry/缓存服务；检查产物行为、命令计数、Lock 内容与网络请求。
- 故障注入：在下载、解包、Lock 刷新、输出发布、CAS 发布、ActionResult 提交前后逐点中断。
- 差分测试：相同输入的增量结果与干净构建运行行为、符号/依赖结构对比；不用字节完全一致作为 V1 前提。
- 兼容测试：旧 Edition 脚本、Query 客户端和 Lock fixture；未支持版本显式失败。

### 7.2 CI 分层

| 层 | 每次改动 | 扩展验证 |
|---|---|---|
| 基础 | Core 编译、单元测试、Linux 纵向 smoke；M1 后三平台 smoke | 编译器最低/当前版本候选矩阵 |
| 受影响模块 | 对应测试族、Schema fixture 与故障回归 | 网络、跨项目缓存、Watch 压力 |
| 发行 | 全 MUST 场景、签名/校验/安装测试 | 长时间稳定性、资源/规模基准、升级迁移 |

基础工具自身可以使用成熟构建工具建立 CI；测试 CXX 用户项目时必须证明实际经过 CXX DAG/Scheduler。Bridge 内部工具调用单独记录，避免与默认执行后端混淆。

### 7.3 性能基准

固定项目源、工具链摘要、SDK、机器信息与运行条件；对每个场景记录中位数、离散程度、进程数、CPU 时间、峰值内存、I/O、动作/命中数量与传输量。至少重复多次并区分冷/热状态，具体次数由噪声实验确定。

场景包括小/中/大规模项目的全量、空构建、单源码、公共头、Modules、PCH、Unity、多 Profile、本地缓存和远程缓存；冷状态要明确是仅输出清空，还是同时清空 Action Cache/CAS/OS 缓存。

CMake+Ninja 对照使用同一源码、等价编译/链接选项、同工具链与相同产物范围，配置期与执行期分别计时。M0 建基线，后续每次性能相关修改更新数据；统计上显著退化需解释。性能预算待实测冻结，不预设“快多少”的发布指标。

## 8. 风险登记与发布阻塞

| 风险 | 失败后果 | 检测证据 | 应对/负责人 |
|---|---|---|---|
| Driver ABI 不一致 | 崩溃、异常损坏或错误释放 | P01 + 三平台 ABI fixture | 同工具链源码 Runtime；A/B |
| 输入闭包不完整 | 错误缓存命中且构建看似成功 | P03/T19/T28 差分反例 | 禁共享缓存、补扫描；B/D |
| 包解析循环 | 初次构建无法配置、重复副作用 | P02 单次声明证明 | 延迟句柄、元数据先行；A/C |
| ABI 包误复用 | 链接或运行错误 | T12 参数化 ABI 矩阵 | 默认精确匹配、源码回退；C |
| 工具链发行物缺失 | 三平台按需安装无法交付 | T06 干净用户目录安装 | 提前落实发行来源和许可；B/发行 |
| 多输出发布或并发 Lock 损坏 | 丢失已有产物/解析 | P08/T32 逐点中断 | 暂存、完成清单、事务互斥；B/D |
| 任意脚本读取环境 | 模型陈旧、错误图 | T29 非追踪环境变化 | 默认重执行声明；A/E |
| Modules 版本差异 | 组合失败、BMI 误复用 | P04/T20 真实工具探针 | 能力驱动 Adapter、明确限制；B |
| 桥接外部状态未纳入 Lock | 机器相关解析和缓存污染 | P06/T13 洁净环境回放 | 独立根与真实元数据导入；C |
| 内存估算偏差 | 进程被系统终止 | T17 超预算实验 | 预算语义明确、观测修正与取消；D |
| V1 范围过宽 | 部分功能被暗中省略 | R01–R33 与 12 场景清点 | 按里程碑交付，范围变更走产品决策；负责人 |

## 9. 下一步可直接开始的工作

1. 评审 D02/D03/D04/D06/D10/D13，确认六组用户可见语义：脚本依赖范围、变量展开、Host 选择、包信任、内存预算、清理边界。
2. 建立 M0 仓库骨架与测试 runner；优先 P01 Driver ABI、P02 声明句柄、P03 头文件失效。
3. 交付 Linux `new → build → run`，保留每个原型的可执行回归。
4. 按 M1 提前建立三平台 CI，再开始冻结持久化 Schema 与外部协议。

**V1 完成定义**：需求矩阵中的 MUST 都有通过证据；OPEN 已确认或有产品认可的范围说明；稳定性和错误缓存问题清零；发布文档如实列出实测工具链与功能组合。文档评审通过仅表示可以开始实现，不等于这些能力已经交付。
