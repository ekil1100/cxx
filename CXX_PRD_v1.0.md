# CXX — Product Requirements Document (PRD)

**文档版本**：1.0（架构设计输入稿）  
**状态**：需求确认完成；技术方案待设计  
**产品名称 / CLI**：CXX / `cxx`  
**核心定位**：使用 C++ 描述构建，并统一 C++ 工具链、构建、依赖与缓存体验；目标是原生替代 CMake 的核心构建职责。  
**目标读者**：架构设计 Agent、实现规划 Agent、核心开发者。

> 本文档是需求约束，不是已经验证的技术实现。代码及命令示例表示**期望的用户体验**，并非冻结的公共 API。架构设计必须区分“强制行为”和“待设计接口”。

## 1. 产品目标

### 1.1 愿景

**One tool for C++. Build C++ with C++.**

CXX 必须提供原生 C++ Build API，以 `build.cpp` 声明 Target、依赖及构建逻辑。CXX 自行维护构建图、调度和执行过程，不以 CMake、Ninja 或 Make 作为默认执行引擎。

CXX 必须统一常用 C++ 开发操作，包括配置、构建、运行、测试、Toolchain 版本选择、Package 解析、本地与远程缓存。它应管理并调用 Clang、GCC、MSVC、Linker、Formatter 等成熟工具，不重新实现编译器或链接器。

### 1.2 核心目标

- **原生描述**：`build.cpp` 是标准 C++ 代码；不创建新的 Build DSL。
- **可复现的解析**：项目声明 Toolchain 和 Package 要求；`cxx.lock` 固定解析结果。
- **统一执行**：CXX 内部以 DAG 表示 Target、Action 和依赖；支持增量、并行与缓存。
- **跨平台**：V1 验收范围包括 Linux、macOS 和 Windows。
- **现代 C++**：支持 C++17 至编译器所支持的更新标准；支持 C++20 Modules、PCH、Unity Build。
- **可集成**：提供版本化 Project Query Protocol、机器可读结果和 `compile_commands.json`。
- **保持解耦**：不把 CMake、Conan、vcpkg、Bazel 或某个具体工具作为 CXX Core 的架构依赖。

### 1.3 非目标与范围约束

V1 **不**实现以下能力：

- Remote Execution、Remote Worker、远程任务调度服务及远程执行专用认证机制。
- 通用 Task Runner 和生命周期 Hooks；允许使用外部 Taskfile。
- 自研编译器、自研链接器、自研增量链接算法。
- 完整的自动 SDK 安装/管理平台。
- CMake 专用自动迁移器；可以由 AI 辅助迁移，并通过 CXX 进行验证。
- `cxx doctor --fix` 自动修复机制。
- 严格字节级 Reproducible Build 模式和独立二次构建验证。
- 完整 VS Code、JetBrains/CLion 插件的强制交付；插件功能和排期另行设计。
- 通用自定义 Rule DSL、复杂 preset 继承体系、任意模板表达式语言。

## 2. 需求级别与实施原则

- **MUST**：V1 验收必须满足。
- **SHOULD**：强烈建议，若未交付必须给出原因及后续路径。
- **FUTURE**：只预留合理边界，不视为 V1 交付。
- **OPEN**：已识别但未获产品决策，架构 Agent 应提出备选方案，不得自行标成已确认需求。

V0 是验证性原型，不需要拥有 V1 全部功能。V1 范围虽然广，但实施计划应拆分里程碑，保证各阶段可运行、可测试。

## 3. 项目结构与配置职责

典型项目：

```text
project/
├── cxxrc.toml       # Bootstrap Toolchain and CXX edition
├── build.cpp        # Entry point for project build logic
├── cxx.lock         # Resolved toolchains and packages
├── src/
├── include/
├── tests/
└── .cxx/            # Generated project-local state and outputs
```

职责必须明确：

| 文件 | 职责 | 不承担的职责 |
|---|---|---|
| `cxxrc.toml` | 让 `build.cpp` 可以编译并运行：C++ 标准、preset/Toolchain、Edition | 不声明 Target、项目依赖、构建 DAG、Profile |
| `build.cpp` | 项目配置、Target、源码、依赖版本声明、SDK 使用、Profile、构建语义 | 不自行解析 bootstrap TOML |
| `cxx.lock` | 锁定工具链和 Package 的实际解析结果与内容身份 | 不承载用户构建规则 |
| `.cxx/` | Build Driver、构建状态、项目输出 | 不保存全局共享 CAS 的唯一副本 |
| `~/.cxx/` | 全局工具链版本管理、Preset、共享 CAS、用户级基础设施配置 | 不改变项目声明的确定性 |

### 3.1 最小配置

```toml
[cxx]
edition = "0"

[cpp]
version = "26"

[preset]
name = "llvm"
version = "22"
```

`edition` 从 `0` 开始。Edition 0 是早期语义，允许较快迭代；首个稳定 Edition 为 1。Edition 不等于 CXX 可执行程序版本，也不等于 C++ 标准版本。初期可以缺省为 0；稳定版项目应显式声明 Edition。

`cpp.version` 默认作用于 `build.cpp` 和项目 Target；单个 Target 可以覆写。CXX Core 自身以 C++17 为最低实现基线，不能因此限制用户的 Build Script 必须使用 C++17。对于 C++26 等更新版本，必须验证所选编译器实际支持相应语言模式；不应声称所有编译器版本都完整支持某标准。

### 3.2 Toolchain 与 CXX 版本解耦

CXX 自身版本**不应成为项目构建身份的常规约束**。项目重点固定 compiler、linker、工具、C++ 标准、目标平台和依赖。升级 CXX 不应无故改变 Build API 的旧 Edition 行为、Target 命令或产物。若内部行为确实变化，缓存必须正确失效。

### 3.3 Toolchain 选择、Preset 与 Override

用户可以选内置或自定义 preset：

```toml
[preset]
name = "llvm"
version = "22"

[linker]
name = "mold"
version = "2.40"
```

- `[preset].name` 必填；`version` 可选。`version` 表示 preset 所代表的**工具链发行版本**，不是 preset 文件版本。
- 工具组件字段可分别使用 `name`、`version`、`path`；存在组件段时覆盖 preset 对应组件。
- 自定义 preset 可以位于项目 `.cxx/presets/` 或用户 `~/.cxx/presets/`；同名优先级：项目 > 用户 > 内置。
- preset **不支持继承**，也不支持多继承；不创建复杂的 DSL。
- preset 定义允许 `path` 作为工具链根目录；组件相对路径相对于该根目录解析。
- 自定义 preset 中使用的组件路径**必须明确声明**，不得依赖隐式扫描或目录猜测。内置 preset 可以封装已知的官方布局。
- 项目可以覆盖组件，不需要 `[toolchain.override]` 层。

自定义 preset 定义示例：

```toml
name = "company-llvm"
path = "/opt/company/llvm"

[compiler]
name = "clang"
path = "bin/clang++"

[linker]
name = "lld"
path = "bin/ld.lld"

[archiver]
name = "llvm-ar"
path = "bin/llvm-ar"
```

用户选择：

```toml
[preset]
name = "company-llvm"
```

### 3.4 字符串环境变量

所有字符串配置字段允许 `$VAR` 和 `${VAR}` 环境变量展开。变量缺失时必须明确报错；不支持 shell 命令替换、默认值、条件语法、字符串函数。

CLI 使用 `--set KEY=VALUE` 为当前命令设置展开变量，且不修改配置。已确认的优先级意图为：CLI `--set` > 项目配置明确值 > 用户级配置明确值 > 进程环境。**OPEN**：对于 TOML 中“字面字段覆盖”和“同名变量覆盖”的精确定义需要写成统一算法，避免歧义。

最终展开的值必须参与 Toolchain 解析、Lock 验证和必要的缓存身份计算。不得把机密环境值原样写入公开日志或缓存元数据。

### 3.5 Toolchain 版本管理

- 默认先复用符合 Lock 的已安装、已验证工具链；缺失时自动下载并安装到 `~/.cxx/`。
- 不修改系统 PATH 或系统目录，不要求 root 权限。
- 支持全局默认工具链，项目 `cxxrc.toml` 优先于全局默认。
- 支持 `cxx toolchain list/install/remove/use/current/update` 等命令；具体 CLI 可在技术设计中统一。
- 官方 LLVM preset 默认提供完整 LLVM 组件集合，并允许单独覆盖 compiler、linker、formatter 等。
- 标准库默认依据编译器及平台选择，并可显式覆写；不得默默选用 ABI 不兼容的标准库。
- 显式路径指向 fork 编译器时，必须校验其真实身份和二进制内容，不能只用 `--version` 字符串或路径作为跨机器缓存身份。

版本约束初期支持：`22`（匹配 22.x）、`22.0`（匹配 22.0.x）、精确版本、`latest` 和 `system`（若该工具类型和管理策略允许）。Lock 中必须保存精确的实际版本/身份；不得在同名系统库中进行隐式回退。

## 4. Build Script 与 Build API

### 4.1 固定入口

```cpp
#include <cxx/cxx.hpp>

void build(cxx::Build& b)
{
    auto& app = b.executable("hello");
    app.source("src/main.cpp");
}
```

`build.cpp` 是唯一入口 Translation Unit。CXX 生成并缓存 Build Driver，通过 runtime 直接链接执行；V1 不使用 IPC/daemon 传输 Build Graph。构建脚本可通过普通 C++ Header (`.h`/`.hpp`) 拆分。V1 不做 `build/**/*.cpp` 的自动扫描或隐式链接。Build Driver 的多 Translation Unit 支持不是 V1 的必要条件。

C++20+ 用户项目的 Module 必须作为 Target 源文件受到支持。**OPEN**：`build.cpp` 自身导入自定义 C++ Modules 的 bootstrap 流程尚未冻结；技术设计需要避免将其误写成“V1 已承诺自动多 TU Build Driver”。

### 4.2 Bootstrap 流程

```text
cxx build
  -> locate cxxrc.toml / project root
  -> parse bootstrap config and edition
  -> resolve and verify toolchain; install if missing
  -> compile or reuse cached build driver
  -> expose structured resolved environment to Build API
  -> execute build() to create Project Model and DAG
  -> validate, plan, cache lookup and schedule Actions
  -> materialize outputs and report results
```

`build.cpp` 应通过 `b.environment()` 等结构化 API 读取已解析的 Toolchain、C++ 标准、版本和**真实工具路径**；它不需要自己读取 TOML。Build Driver 变更时必须基于 `build.cpp`、被 include 的文件、CXX Build API Edition、工具链及相关编译输入正确失效缓存。

### 4.3 Target 与构建语义

MUST 支持 Executable、StaticLibrary、SharedLibrary、ObjectLibrary，以及可缓存 Custom Action。提供 Source、Include、Define、Compiler/Linker Option、依赖传播与可见性（private/public/interface）、链接、安装、测试、运行、Generated Files 和 Target 条件逻辑。

示意：

```cpp
void build(cxx::Build& b)
{
    auto fmt = b.dependency("fmt", "11");
    auto target = b.standard_target();

    auto& app = b.executable("app");
    app.target(target)
       .source("src/main.cpp")
       .link(fmt);
}
```

Build API 使用 C++17 可实现的公共类型；允许 C++17 以上语言模式调用。公共 API 默认采用结构化异常错误传播，统一错误诊断；CXX 内部可以使用显式 Error/Result 类型。需要保持 Build Driver 失败时的状态一致性。

### 4.4 Edition 与兼容性

CXX 可执行程序版本、Build API/行为 Edition、Query Protocol Schema 和 Lock Format 必须分离。相同稳定 Edition 下尽量维持 Build Script Source Compatibility。重大行为变化进入新 Edition；旧 Edition 不应因为 CXX 程序升级而立即失效。Query 和 Lock 各自采用版本化 Schema。V0 的 Edition 0 为实验阶段，不承诺与稳定 Edition 相同的长期兼容水平。

### 4.5 跨平台与交叉编译

V1 运行平台为 Linux、macOS、Windows。Host 自动探测；Target、SDK 的使用关系、Sysroot 和交叉编译配置由 `build.cpp` 描述，并允许 CLI Target 覆盖。CXX 管理通用 Toolchain，但**不管理全世界的 SDK 安装**。SDK 可以使用显式路径及第三方扩展封装。

## 5. Package Manager（V1 核心）

### 5.1 统一 Dependency API

所有项目 Package 通过统一的 `b.dependency(...)` 接口描述，来源只是属性。Registry、Git、URL、Local Path、Conan/vcpkg Bridge 均转换为 CXX 内部统一的 Package/Target/Artifact 模型。

- 默认未指定 Source 时使用受配置控制的 Registry，不隐式回退到系统现有库。
- **禁止 system package source**。不同机器不得因系统已安装 OpenSSL、zlib 等版本不同而悄然切换 Package。
- OS 和 SDK 原生能力（例如平台 Framework、线程支持）属于 Platform/Toolchain 能力，不等价于可锁定的第三方 Package。其链接表达在技术设计中统一。
- CXX Registry 是原生模型；Conan/vcpkg 是 Adapter，不让外部模型决定 CXX Core 接口。
- 包版本与来源声明在 `build.cpp`，解析结果存入同一个 `cxx.lock`。

### 5.2 Package 解析、构建与缓存

V1 必须支持：版本解析、传递依赖、冲突诊断、源码/二进制包、校验、Local Package Store、包构建、锁定与可重用缓存。优先使用**已验证且与 ABI、Toolchain、平台、标准库和配置兼容**的预编译包；不兼容则源码构建。支持用户强制源码构建。

Conan/vcpkg Bridge 必须把真实解析结果重新纳入 `cxx.lock`；不得仅依赖机器现有的未锁定状态。官方 Registry 服务上线时间与具体 Registry 协议仍需架构设计给出 V1 可落地方案；不能因为“有 Bridge”而放弃 CXX 原生 Package Model。

### 5.3 `cxx.lock` 与更新规则

- 同一份 Lock 保存 Toolchain 和 Package 的精确解析身份（名称、版本、来源、内容摘要、Git Commit、传递依赖等）。
- 普通 `cxx build` 若 Lock 存在且有效，**不得悄悄升级**已锁定解析。
- 首次普通构建在 Lock 缺失时可解析并生成 Lock。
- `cxx build --locked` 要求 Lock 存在且与声明一致；不得写入 Lock。
- CI 建议默认采用严格锁定策略；自动 CI 识别的具体规则属于实现细节，应可显式控制。
- `cxx update`、`cxx update <package>`、`cxx update toolchain` 才能主动升级相应版本。
- `cxx install` 可以解析缺失条目，但不得无关升级已锁定条目。
- `--offline` 禁止网络请求；正常模式网络失败可以复用符合 Lock 的已验证本地对象。
- Git 分支/标签必须锁成具体 Commit，远程内容需校验摘要。
- 本地路径依赖属于工作区输入，可以不固定内容 Hash，但必须追踪实际文件变更。

## 6. 构建引擎

### 6.1 Build Graph / Action Model

V1 内部必须使用明确的 DAG 和统一 Action 模型；至少支持编译、链接、归档、代码生成、复制、测试、安装的相应步骤。每个 Action 声明命令、环境、输入、输出和依赖。禁止多个 Action 无规则地写同一输出。检测循环依赖、缺失输入、目标冲突和失败传播。

CXX 自己实现 Scheduler 和 Executor；Ninja 可作为后续导出格式，但不是默认 Build Backend。V1 Build Driver 与 Runtime 直接链接，不引入独立 IPC 守护进程。

### 6.2 并行与失败策略

- `-j N` 控制并行任务数，`--memory-limit=16G` 控制总内存预算；调度器支持 CPU/内存资源感知。
- 优先调度关键路径、用户指定的 Target 和 IDE 交互任务；优先级不能改变构建结果，也不能饿死其他任务。
- 默认首次失败后停止启动新任务，但允许已运行任务完成。
- `-k` / `--keep-going` 继续运行独立任务，最后汇总失败；依赖失败的后继任务不得执行。
- Ctrl-C 应停止新任务、尽量终止子进程并保持缓存及构建状态一致性。

### 6.3 增量构建和语言特性

- 修改单个 `.cpp` 只编译必要单元，再执行受影响的链接动作。
- 头文件依赖自动追踪；GCC/Clang 可利用 Depfile，Module 使用编译器依赖扫描。
- C++20 Modules 自动识别 Module Interface、Partition 和 Import 依赖，维护 BMI 生命周期及缓存；允许与 PCH 共存（受编译器约束）。
- V1 支持 PCH。
- V1 支持 Unity Build（默认关闭），支持固定 Batch Size、排除不兼容文件及 CLI `--unity`。
- 可以使用原生 Linker 的增量链接能力；CXX 不自行实现链接器内部增量算法。
- 支持一个命令同时构建多个 Profile，避免重复执行相同 Action；Profile 默认由 `build.cpp` 定义，也支持命令行组合如 Sanitizer/LTO/PGO，配置冲突必须报错。
- 支持 Watch Mode：`build/test/run --watch`，含防抖、增量重新规划和程序重启机制。
- 编译器插件在 V1 使用通用编译参数；不提供专用插件 API。

### 6.4 自定义 Action 与任务边界

Custom Action 必须是 DAG 的一部分，并显式声明输入、输出及受追踪工具依赖。通用脚本部署、发布、文档制作、独立任务 Hook 不属于 V1；用户可通过 Taskfile 等外部工具完成。

### 6.5 输出目录、清理与日志

- 默认输出 `.cxx/build/`；使用 `--out` / `-o` 临时指定输出位置，项目构建状态与全局 CAS 分离。
- `cxx clean` 清理当前项目构建产物；`cxx clean --all` 清理项目状态；`cxx cache gc` 回收共享缓存；`cxx cache clear` 删除全局缓存前必须确认。不得误删托管 Toolchain 或其他项目依赖。
- 交互终端显示动态构建进度；CI/管道按行输出。
- 默认简洁日志，错误时展开诊断；`-v` 输出完整命令；`--log-format=json` 输出机器可读结果。
- `cxx build --explain` 报告 Action 执行、跳过、缓存未命中的原因。
- `cxx build --profile-build` 生成耗时、关键路径、CPU/内存、缓存及远程传输报告，并提供 Chrome Trace/Perfetto 可消费的数据。
- `cxx graph --level=target|action` 导出 DOT 和 JSON 两级构建图。

## 7. 缓存与远程缓存（V1 核心）

### 7.1 统一 CAS 与独立语义层

全局缓存存储于 `~/.cxx/cache/`，跨项目共享内容寻址对象。内部应区分：

- `CAS`：内容对象与完整性校验。
- `ActionCache`：Action Key 到 Action Result 的映射。
- `PackageStore`：Package 身份、来源与内容。
- `ToolchainStore`：Toolchain 身份、来源与内容。

以上可共享底层内容存储，但不能混成相同的逻辑对象。

### 7.2 Remote Cache

V1 必须支持本地 CAS + HTTP(S) Native Remote Cache。支持远程读写、只读模式、TLS、认证、摘要校验、并发上传下载、压缩、离线回退和隔离 Namespace。不得默认信任来源不明的二进制缓存内容。

V1 Native Protocol 仅要求 HTTP(S)。CXX Core 定义自己的 Cache Interface，外部协议通过 Adapter 集成；Bazel HTTP Cache / REAPI Adapter 应纳入兼容方向，**不作为内部强制依赖**。REAPI 完整兼容深度应由技术设计提出验证计划。

Remote Cache 的连接地址、凭据和运营配置属于 `~/.cxx/config.toml` 等用户/组织级基础设施配置，不属于 `cxxrc.toml` 的 Build Driver Bootstrap 需求。访问凭据不得写入项目 Lock。

### 7.3 Cache Key 与正确性

Action Fingerprint 必须包含所有会影响输出的已知输入：编译器/链接器内容身份、标志、Target、Sysroot/SDK、输入内容及传递依赖、显式环境输入、生成工具和构建参数。不能只依赖 `mtime` 或版本字符串。

可缓存 Action 必须具备充分的输入输出声明。对于无法证明封闭输入集合的命令，必须采取保守策略（禁止共享缓存或标记为不稳定），避免产生错误 Cache Hit。Remote Cache 命中结果必须校验内容摘要及适用的元数据。

### 7.4 Remote Execution 的边界

V1 **不**实现 Remote Worker、远程调度、容器/沙箱执行或 RPC 协议。Executor 抽象要允许未来扩展 `RemoteExecutor`，但不要求冻结远程执行完整协议。后续 Client/Server 可以独立设计与部署。

## 8. IDE、查询与验证

### 8.1 Project Introspection Protocol

V1 必须提供稳定、版本化、机器可读的 Project Query Protocol。可查询 Project、Target、源码、编译参数、编译器及真实路径、Toolchain、Profile、依赖、生成文件、测试和输出 Artifacts。

示例：

```bash
cxx query project --format=json
cxx query targets --format=json
cxx query toolchain --format=json
```

Project Model 应可缓存，且可以由 CXX 在需要时刷新。第三方工具不得依赖解析终端文本或自行理解 `build.cpp`。Query Schema 与 Build API Edition 单独版本化。V1 必须输出 `compile_commands.json`，供 clangd/clang-tidy/IDE 使用。

**IDE 插件具体功能尚未确认。** V1 保证 IDE 基础接口，VS Code/JetBrains 插件的功能清单及发布日期另行决定。

### 8.2 验证与诊断

- `cxx validate`：解析配置、编译/执行 Build Script、创建模型并验证基本约束，不实际执行项目编译 Actions。应报告由于动态构建/生成依赖导致的验证盲区。
- `cxx build --dry-run`：预览预计 Action、命令和执行原因，不执行 Action；对尚未生成的输入需标明计划可能不完整。
- `cxx doctor`：诊断工具链、SDK 可用性、依赖、缓存与环境配置；只诊断，不自动修复。
- 上述结果应支持 JSON 格式，便于 AI/IDE 处理。
- 不提供专用的 CMake-to-CXX 迁移器；AI 可辅助迁移，实际构建和测试作为最终验证。

## 9. CLI 总览（预期能力，具体帮助文本待冻结）

```bash
cxx new app
cxx configure
cxx build [target...] [-j N] [-o DIR]
cxx build --profile debug,release
cxx build --profile debug --sanitizer address
cxx build --profile release --lto thin
cxx build --locked
cxx build --offline
cxx build --dry-run --explain
cxx build --watch
cxx build --profile-build
cxx run [target] -- [args...]
cxx test [filter]
cxx test --watch
cxx validate
cxx doctor
cxx query project --format=json
cxx graph --level=action --format=dot
cxx clean [--all]
cxx cache gc
cxx cache clear
cxx toolchain list
cxx toolchain install clang@22
cxx toolchain use clang@22
cxx update [package]
cxx update toolchain
```

`cxx new` 只生成最小 `cxxrc.toml`、`build.cpp` 与示例源码，不设计复杂模板市场。CLI `--set KEY=VALUE` 只影响本次命令的配置变量展开，不写回配置文件。

## 10. V1 最低验收情景

1. **Bootstrap**：三大系统能创建并构建 Hello World；`build.cpp` 使用声明的 C++ 标准编译，且可读取已解析 Toolchain 的结构化信息。
2. **Build Graph**：支持多个 Library/Executable、生成源码、公共与私有依赖传播、测试与安装；循环依赖与输出冲突有可操作的错误消息。
3. **Incremental**：空构建不重新运行 Compile/Link；改变一个源码或公共头只触发必要 Action；改变编译器或相关参数正确失效。
4. **Package**：`fmt` 等第三方依赖能通过 Registry/兼容 Adapter 解析、锁定、下载、构建和复用；系统已安装的同名库不得悄然替换。
5. **Lock**：首次构建可生成 `cxx.lock`；锁定模式不允许更改解析；升级必须显式；离线模式只能使用已验证本地内容。
6. **Toolchain**：支持官方 preset、自定义 preset、组件 Override、显式 fork 路径、环境变量展开、全局托管版本和按需自动安装。
7. **Modern C++**：至少验证一组 C++20 Modules + PCH + Unity Build 情景，并正确报告各编译器不支持的组合。
8. **Cache**：Local CAS 跨项目复用；Native HTTP(S) Remote Cache 命中/未命中、离线、认证、校验、读写模式有集成测试。
9. **Scheduler**：并行、失败策略、Keep-going、多 Profile、资源限制与关键路径调度都能被验证。
10. **Developer Tools**：`validate`、`dry-run`、`doctor`、`explain`、Graph、构建性能报告和 Query JSON 可用；`compile_commands.json` 与 clangd 兼容。
11. **Watch**：源码和配置变化触发正确的增量动作；频繁变更合并处理。
12. **稳定性**：错误、信号中断、缓存损坏、下载失败和部分构建失败不应破坏 Lock 或已有有效产物。

## 11. 性能评估与质量要求

V1 首要目标是**正确性、稳定性和可测量性**，不预先承诺“比 CMake + Ninja 快多少”。必须提供自动化基准，包括：全量构建、空构建、单文件增量、头文件修改、Modules、PCH、Unity、多个 Profile、本地/远程缓存、不同规模项目的 CPU/内存/I/O 统计。性能变化应可追踪并与 CMake + Ninja 进行可复现实验对比。

系统应支持详细诊断但默认简洁；无网络或认证失败时明确报告；对内容摘要失配拒绝采用缓存结果；对不同平台和自定义工具链给出清晰的能力检查。

## 12. V0 → V1 里程碑建议（非强制实现顺序）

**V0 — Architecture Spike**：Linux + Clang + C++17 Core；`build.cpp` → Driver → DAG → Compiler/Linker；Executable/StaticLibrary、头文件依赖、基本增量构建。

**M1 — Portable Core**：跨平台 Process/Filesystem、Scheduler、Target Model、Graph、Profile、诊断和基础 CLI。

**M2 — Toolchain/Bootstrap**：`cxxrc.toml`、Edition、Preset、显式路径、Toolchain Installer、`cxx.lock` 工具链部分。

**M3 — Package & Lock**：统一 Package Model、Registry/Adapters、Package Build、全局 Store、锁定与更新语义。

**M4 — Cache**：Local CAS、Action Cache、Native HTTP(S) Remote Cache、正确性测试。

**M5 — C++ Features & Developer Experience**：Modules/PCH/Unity、Watch、Dry-run/Validate/Doctor、Project Query、Compile Database、Profiling。

**M6 — Platform Verification & Release**：Linux/macOS/Windows、SDK 边界、兼容性与故障测试、基准测试、文档和发行流程。

## 13. 待架构设计解决的问题（OPEN；不得当成产品已确认）

以下问题留给 Architecture Agent 形成 ADR（Architecture Decision Record）并说明选择理由：

1. Project Model、Target Graph 与 Action Graph 的具体对象模型、所有权、稳定 Handle 及序列化边界。
2. Build Driver 与 Runtime 的二进制链接/插件方式、Build API SDK 分发、异常 ABI、跨平台启动过程，以及 `build.cpp` 自身使用 C++ Modules 的具体支持范围。
3. CXX Core C++17 最低标准所对应的最低 Clang/GCC/MSVC 编译器版本，以及实际支持的 C++26 语言功能检测策略。
4. `cxxrc.toml` 的严格 Schema：preset 版本选择、组件字段部分覆盖的 merge 规则、同名 preset 冲突、环境变量与 CLI `--set` 的精确解析优先级。
5. Managed Toolchain 和自定义 fork 的真实性验证方法；系统、用户和项目 Toolchain 的信任边界；平台 SDK/stdlib 身份追踪。
6. Package Registry API、包元数据结构、Conan/vcpkg Bridge 可实现边界、二进制 ABI 兼容判定及 Package 构建沙箱策略。
7. Lock File Schema、Package Lock 和 Toolchain Lock 的合并结构、可移植路径处理、Schema 迁移规则、第一次生成 Lock 的原子性。
8. Action Fingerprint、CAS 摘要算法、Header/Module 动态依赖、工具可执行文件哈希策略、非封闭自定义 Action 的保守缓存规则。
9. Native HTTP Remote Cache 具体 API、认证与完整性模型、命名空间、压缩、并发、错误处理；REAPI/Bazel HTTP Adapter 的技术范围。
10. Scheduler 资源估算和优先级策略，Watch Mode 文件事件抽象、跨平台进程管理与输出目录布局。
11. `cxx query` 协议 Schema、版本协商、缓存失效，以及 IDE 接口最小验收集。
12. 在不扩大 V1 范围的前提下，明确 MVP 分层依赖、开发人员任务拆分与可交付顺序。

## 14. 已确认的重要决策摘要

- CXX 是 CMake 的原生替代方案，不是 CMake wrapper；CXX 自行调度 DAG。
- `build.cpp` 使用 C++，固定 `void build(cxx::Build&)` 入口；构建逻辑可经 Header 拆分。
- CXX Core 最低 C++17；`build.cpp` 默认与项目语言标准一致，支持更新标准。
- `cxxrc.toml` 仅负责 Bootstrap Toolchain 和 Edition；`build.cpp` 声明 Package、Target、Profile 和 SDK 使用关系。
- Edition 从 0 开始，和 CXX 版本解耦；Query/Lock 独立版本化。
- `preset` 为可参数化 Toolchain 模板，`version` 可选；允许自定义 preset 和组件覆盖；**不支持 preset 继承**。
- 自定义 preset 路径必须显式；根路径与相对组件路径组合；所有字符串字段支持明确的环境变量展开；CLI `--set` 覆盖变量。
- 托管工具链自动安装到用户目录，可管理全局默认版本；不默默修改系统工具链。
- V1 自带统一 Package Manager；禁止 system package source；Conan/vcpkg 通过 Adapter 兼容。
- 一个 `cxx.lock` 同时锁定 Toolchain 和 Package；普通构建不偷偷升级；CI 建议锁定模式。
- V1 Local CAS + Native HTTP(S) Remote Cache；与 Bazel/REAPI 通过 Adapter 兼容，无 Bazel 核心依赖。
- **V1 不实现 Remote Execution**；Executor 接口为后续预留。
- V1 支持 Linux、macOS、Windows；SDK 安装不属于 CXX Core。
- V1 需要 PCH、C++20 Modules、Unity Build、自定义 Action、增量/并行/资源感知调度、多 Profile、Watch。
- V1 提供验证、Dry Run、Explain、Doctor、构建图和性能报告；不提供自动修复或通用 Task Runner。
- V1 必须提供稳定的 IDE Query 基础接口，但官方 VS Code/JetBrains 插件交付范围尚未冻结。
- V1 以可测量性和正确性优先，不承诺特定性能领先幅度。

---

### 交给下游 Agent 的工作指令

1. 先提取本 PRD 的 MUST、FUTURE 和 OPEN 条目，制作需求追踪矩阵。
2. 在不改变已确认产品边界的前提下，输出模块架构图、数据模型、关键流程、接口草案及 ADR。
3. 对尚未冻结的 API 示例，不得把示例签名直接视为最终接口。
4. 识别 C++17 Core、跨平台、Modules、Package、Remote Cache 之间的关键技术风险，并安排验证原型。
5. 将 V1 拆成可持续集成的里程碑，定义每个里程碑的验收测试和退出条件。
6. 不要把 Remote Execution、IDE 完整插件、Task Runner、SDK 自动安装或自动修复重新放进 V1 必交范围。
7. 如发现需求相互冲突，应显式提出 ADR/澄清点，不得自行隐藏或绕过冲突。
