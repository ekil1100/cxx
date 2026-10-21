# M0 实现进展与验证

## 结论

已实现 M0 的核心构建闭环和本地自举，并在 macOS arm64 / Apple Clang 21 上通过 C++ 单元测试、1 项自举测试、18 项集成测试、23 项定向回归和 9 项工具脚本测试。CXX Core/Runtime 已采用 C++23；公共 SDK、用户脚本和项目继续支持 C++17 起。当前可以创建项目、构建可执行程序和静态库、运行程序，并按内容进行本地增量构建。

**M0 尚未正式退出**：Linux CI 的自举空构建修复仍待远端复验。P02 最小声明/Lock 原型已通过本机验证，P04 已完成本机扫描与显式构建链路；两项原型的跨平台证据仍待补齐。M1 已启动 SharedLibrary/ObjectLibrary 最小切片，进展见 [M1 状态](M1_STATUS.md)；M2–M6 保持计划状态。本文不把设计中的 V1 能力计为已经实现。

## 工作包进展

| 工作包 | 状态 | 证据或剩余事项 |
|---|---|---|
| M0.1 种子构建、测试、依赖 | 核心完成 | `scripts/build.py`、`build.cpp`、自举及单元/集成测试、依赖校验清单；macOS LLVM 20 CI 已通过；Linux LLVM 20 编译通过，自举空构建检查失败 |
| M0.2 Bootstrap/Driver | 核心完成 | toml++ 严格最小 Schema；同工具链 C++23 Runtime 静态库；声明标准的单入口 Driver；结构化环境 |
| M0.3 目标和 Action DAG | 完成当前子集 | Executable/StaticLibrary，源码/include/define/link，可见性、目标图与显式 Compile/Archive/Link Action 依赖 |
| M0.4 进程与 CLI | macOS 已验证 | `new/build/run`、顺序执行、错误诊断、参数/退出码；POSIX 实现的 Linux 验证待 CI |
| M0.5 基本增量 | 完成当前子集 | 每次预处理发现依赖，原始输入内容哈希，编译原始源码，编译后再次校验，输出摘要校验 |
| M0.6 前置原型与基线 | 部分完成 | P01/P03 已有本机证据；P02/P04 最小原型本机验证通过；基础性能样本已记录；原型跨平台证据待补 |

### 与建议计划的实现差异

- Python 直接调用 Clang 只生成种子启动器；正式 CXX、support 库及单元测试由根目录 `build.cpp` 声明，经 CXX 自有图与执行器构建。Python 继续负责依赖校验、测试编排及输出快捷链接。
- Runtime 先按项目缓存，尚未实现全局跨项目 Runtime 复用。
- M0 仅使用系统已安装的 Clang/archiver；完整 preset、工具链下载、版本锁定与环境展开由 M2 实现。
- 当前 Action 图按拓扑顺序执行；M1 再接并行与资源预算。
- 存储为本地输出与指纹记录；尚未实现共享 CAS 和完整 Toolchain/SDK 内容闭包。

## 代码入口

| 路径 | 职责 |
|---|---|
| `build.cpp`、`cxxrc.toml` | CXX 自身的目标声明与 C++23 配置 |
| `scripts/build.py` | 校验依赖、生成种子、调用 CXX 构建正式目标并编排测试 |
| `tests/self_host.py` | 新启动器完整重建自身、空构建及可观察源码修改的验证 |
| `src/main.cpp` | CLI、根目录发现、TOML、工具路径、Runtime/Driver Bootstrap |
| `include/cxx/cxx.hpp` | 实验 Edition 0 的 Build/Target Interface |
| `src/runtime.hpp`、`src/runtime.cpp` | 声明执行、图验证、使用要求传播、Action 规划与执行、程序启动 |
| `src/support.hpp`、`src/support.cpp` | 进程、项目互斥、SHA-256、依赖扫描、增量、输出发布 |
| `tests/support_test.cpp` | 指纹/进程/发布/所有权的单元检查 |
| `tests/integration.py` | 真实编译器端到端回归 |
| `tests/regressions.py` | 路径、产物冲突、PTY 与信号的定向回归 |
| `tests/tooling_test.py` | 编译器选择、测试传递与性能报告一致性 |
| `scripts/benchmark.py` | 全量、空构建、源码与头文件增量样本 |
| `.github/workflows/ci.yml` | Linux/macOS 持续验证配置 |

## 已执行验证

执行命令：

```sh
python3 scripts/build.py --test
python3 scripts/benchmark.py --cxx build/cxx --output build/benchmark.json
```

本机结果：

- Core、Runtime 与单元测试按 C++23 编译。源码种子及正式自举目标均使用 `-O2 -Wall -Wextra -Wpedantic`，第三方头文件使用系统 include；自举测试检查实际目标编译命令。Bootstrap 生成的 Driver/Runtime 保留原有编译参数。
- support/SDK 单元测试通过。
- 1 项自举测试、18 项集成测试、23 项定向回归和 9 项工具脚本测试通过，共 51 项 Python 测试；加上 P02 的 8 项与 P04 运行器的 3 项，完整入口共执行 62 项 Python 测试。参数化语言模式与错误用例包含在对应测试中。
- C++17/20/23 脚本分别链接 C++23 Runtime 并运行成功，目标可继续指定 C++17。
- 公共 SDK 与 Driver 入口头文件独立按 C++17 编译通过；内部头文件在低于 C++23 模式下给出明确错误。
- 目标标准覆写、脚本异常传播和各语言模式下空构建通过。
- 头文件遮蔽、缺失候选变存在、同 mtime 内容变化均正确失效。
- 空构建项目 Compile/Archive/Link 为零；仍执行依赖扫描。
- 失败编译和失败元数据发布保留旧可执行文件；对象/可执行文件损坏会重新生成。
- 静态库私有传递链接和菱形顺序、三种编译可见性通过。
- 带空格、`$`、`#` 的输入路径，以及程序参数中的 shell 字符通过。

### C++23 写法与工具链检查

本机 Apple Clang 版本为 `21.0.0 (clang-2100.3.34.2)`；这是编译器版本号。实测不指定标准时 `__cplusplus` 为 `201402L`（C++14），使用 `-std=c++23` 后为 `202302L`（C++23）。本机 libc++ 的 `_LIBCPP_VERSION` 为 `220106`；编译器、语言标准与标准库版本应分别记录。

已在合适位置采用以下写法：

| 写法 | 版本 | 位置与用途 |
|---|---|---|
| `std::string::contains` | C++23 | 配置、路径和 SDK 参数的字符检查 |
| `std::ranges::contains` 与投影 | C++23 | 目标名和依赖去重、命令输出参数检查 |
| 显式对象参数递归 lambda | C++23 | 两处图遍历，替换局部递归 `std::function`，去除类型擦除 |
| ranges 算法、容器 `contains`、字符串 `starts_with` | C++20，C++23 可用 | 简化查找、排序和条件判断，保留原有语义 |

`src/support.hpp` 检查实际使用的 C++23 标准库功能宏；递归 lambda 语法由实际编译验证。公共 SDK 与 Driver 入口头文件保留 C++17 写法。修改已通过单元与集成测试，包括字符串 NUL 校验、缺失输出参数拒绝、图循环与菱形依赖、C++17/20/23 脚本兼容性。当前完整测试数量见上节。

### 定向检查发现与修复

1. **预处理快照编译改变源位置列号。** 改为编译原始源码，并将 depfile 中的原始文件内容纳入指纹；通过与直接 Clang 构建的 `std::source_location` 输出对照验证。
2. **汇编 `.incbin` 文件未出现在 C++ depfile。** 除简单符号别名外，内联汇编动作每次执行；更新嵌入文件后程序读取新值。此策略牺牲部分增量效率，避免静默错误命中。
3. **先替换产物再发布元数据会丢失旧产物。** 改为先发布含产物摘要的元数据，再替换产物；两步之间的中断形成可识别的缓存未命中。通过将 `.key` 路径替换为目录的故障注入验证。

后续检视另修复了六项问题：符号链接与 `..` 的实际路径语义、大小写目标名导致产物覆盖、交互程序的终端前台交接、中文/引号项目路径的 Driver include、指定编译器向测试传递，以及性能报告记录实际编译器。新增定向测试覆盖对应触发条件；大小写冲突在所有平台统一拒绝，避免平台相关产物覆盖。

第二轮复检进一步修复三个边界：Python 脚本绝对化编译器路径时保留 `symlink/..`；stdin 重定向后仍通过独立控制终端完成前台交接；直接哈希主源码，并对 Clang depfile 无法可靠表示的 POSIX 反斜杠路径禁用编译结果复用。回归涵盖真实源文件与同名诱饵、include 目录、环境搜索路径及无诱饵构建。归档和链接仍按实际对象内容安全复用。

第三轮增加对实际预处理行标记的检查，覆盖直接、宏和传递 include 中的反斜杠路径，并验证普通、引号和中文路径保留增量复用。进程启动改为父进程单侧建立进程组，所有子进程经同步管道等待初始化完成，避免本机已复现的双侧 `setpgid` 偶发 `EPERM`。单元测试增加 1,000 次并发启动；另有 20,000 次定向启动及 40 次父进程失败注入通过。此前一次缺少阶段日志的 Clang 126 事件是否同因，仍无法判断。

CI 最初显式选择 Ubuntu 24.04 上的 Clang 18，以及 macOS 14 上的 Homebrew LLVM 20；构建、测试和基准使用同一编译器路径。远端运行已核对：macOS 通过，Linux 暴露编译器崩溃。当前工作流将 Linux 改为 LLVM 官方 APT 仓库的 Clang/LLVM 20；修复后的 Linux runner 已完成编译，自举空构建检查仍有失败，详见下节。

第四轮将同一保守策略覆盖到行标记中的 ASCII 控制字符及其转义表示。LF、CR、VT、FF、TAB 五类 include 目录路径均有错误命中反例和修复后回归；有诱饵时正确更新产物，无诱饵时正常构建，对象内容相同时链接仍可复用。最终统一验证为 C++ 单元测试及 44 项 Python 测试全部通过。

上述修复不等同于完整崩溃持久化或沙箱保证。M1/M4 仍需补齐进程树生命周期、文件系统攻击边界、多输出事务及断电恢复。

## 远端 CI 证据

已核对提交 `2223b0c` 的 [M0 checks 运行 38373985372](https://github.com/ekil1100/cxx/actions/runs/38373985372)：

| 平台 | 工具链 | 结果 |
|---|---|---|
| macOS 14 arm64 | Homebrew Clang 20.1.8 | 自举、C++ 单元测试及 49 项 Python 测试通过；基准执行和报告上传通过 |
| Ubuntu 24.04 x86_64 | Ubuntu Clang 18.1.3 | 编译 `src/runtime.cpp` 时前端以 139 退出；测试和基准尚未开始 |

Linux 日志将崩溃定位到 `validate_graph` 的显式对象参数递归 lambda 的 LLVM IR 生成阶段。接受 C++23 语法并不足以证明代码生成可用。本次保持 C++23 实现，改为从 LLVM 官方 APT 仓库安装 Clang/LLVM 20，并让 PATH 中的归档工具来自同一主版本。软件源使用限定于该源的签名密钥。

提交 `0cdee24` 的 [运行 38392085938](https://github.com/ekil1100/cxx/actions/runs/38392085938) 已结束。Linux LLVM 20 越过原有编译器崩溃，但 `test_self_build_rebuild_and_incremental` 预期空构建 `(0, 0, 0)`，实际得到 `(4, 0, 0)`。本轮后续测试及基准未完成；编译通过不等同于 Linux 验收通过。后续本地诊断与修复证据如下，远端完整复验仍待执行。

### Linux 空构建重复编译：定位与修复

使用 Ubuntu 官方软件包解包的临时 sysroot（libstdc++ 13.3.0/14.2.0、glibc 2.39），以 Homebrew Clang 20.1.8 交叉预处理四个自举翻译单元。八份预处理输出均包含 `<iostream>:82` 的 `__asm(".globl _ZSt21ios_base_library_initv")`。原汇编输入检查只接受简单符号别名，因此这条全局符号声明触发保守重编译；从文本副本中仅移除该声明，所有检查均恢复通过。行标记和依赖路径检查未发现损失性路径。

原版 `compile_cpp` 对四个 Linux 目标连续编译两次，均得到 `first_compiled=1 second_compiled=1 same_object=1 same_key=1`。修复仅允许普通字符串拼接形成的 `.globl ` 加单个非空符号，仍拒绝转义、分隔符、附加指令、操作数和其他汇编；缓存版本更新为 `v6`。修复后，八份扫描均可复用，四个目标的第二次编译均跳过，对象与键保持相同。

新增定向回归先失败后通过，覆盖三种字符串拼接形式；`.globl` 前缀后带 `.include` 或 `.incbin` 的用例继续禁用缓存，并验证外部文件修改会改变程序结果。自举测试现在在计数断言失败时输出详细构建日志。复现入口：

```sh
python3 -m pytest tests/regressions.py --cxx=build/cxx -k global_symbol
python3 scripts/build.py --test
```

该实验证明 Linux 头文件输入足以产生并消除已观察到的重复编译。交叉实验未运行 Linux 可执行文件；原 CI 未保存失败空构建的详细日志，其确切头文件补丁版本也未记录，因此最终验收仍以 Linux runner 完整复验为准。

## 本地自举

首次构建执行 `python3 scripts/build.py`：校验第三方依赖，生成 `build/cxx-seed`，由种子执行根目录 `build.cpp`，构建 `cxx_support`、`cxx` 和 `support_test`。正式产物位于 `.cxx/build/`；脚本为启动器和单元测试程序创建 `build/cxx`、`build/support_test` 相对符号链接。默认只构建；添加 `--test` 才执行完整测试。已有副本会被链接替换，后续原生产物更新时短入口自动指向新版本。

日常统一执行 `./build/cxx build`，单元测试使用 `./build/support_test`。直接命令使用所选工具链；指定非默认编译器时设置 `CXX_BOOTSTRAP_COMPILER`。依赖清单校验目前由 Python 入口负责。

自举测试复制源码到含中文和引号的临时路径，验证种子构建、新启动器在移除 SDK 环境覆盖后完整重建自身、空构建零动作，以及修改 `main.cpp` 后一次编译和链接产生新的可观察命令行为。测试通过短符号链接入口执行，并在修改前后均检查空构建；运行期间替换启动器产物、链接自动跟随新产物也已实测通过。

Build API 新增目标局部的 `optimization(Optimization)` 与 `warnings(bool)`，以及按可见性传播的 `system_include(path, visibility)`。当前 Clang 后端将 `speed` 映射为 `-O2`、`none` 映射为 `-O0`、`default_` 保留编译器默认；启用警告加入 `-Wall -Wextra -Wpedantic`。系统 include 使用 `-isystem`，头文件仍参与原始内容哈希。回归验证配置失效、三种可见性、传递依赖、系统头诊断处理，以及优化和警告设置保持目标局部。公共头文件继续兼容 C++17。

自举目标已配置上述选项，与种子的优化、警告及第三方系统 include 设置对齐；toml++ 的既有头文件警告得到系统头处理。完整 Profile、任意编译/链接选项、可搬移工具包、固定种子下载与发布流程仍待实现。

## M0 前置原型

原型与生产 API 隔离，接口和格式均保持 proposed。

- **[P02 声明与 Lock](../tests/prototypes/p02/README.md)**：C++17 声明对象链接 C++23 实现；验证单次声明、扩容后的稳定句柄、声明结束后读取元数据、传递依赖与配置分域、旧锁定选择稳定、严格锁定只读、两个写者竞争，以及发布前后四种真实 SIGKILL 场景。Apple Clang 21 与 Homebrew LLVM 20 各通过 8 项检查。使用合成工具链身份、简单版本约束及 TOML 原型格式；生产 JSON Lock、完整求解和断电持久化留待后续。
- **[P04 Modules](../tests/prototypes/p04/README.md)**：Homebrew LLVM 20.1.8/21.1.8 的配套 `clang-scan-deps` 验证 P1689 Interface/Partition/Import 关系；生成头缺失时扫描失败，生成后正常扫描。显式命令完成 BMI/对象/链接链路；分区与生成头修改后输出从 `42 → 43 → 53`，干净重建仍为 `53`。这是命令链路实验，生产调度、自动增量、PCH/Unity 共存仍待实现。

原型首次接入时，`python3 scripts/build.py --test` 通过 C++ 单元测试与 60 项 Python 测试；随后增加两项 Linux 汇编缓存回归，当前完整入口的 62 项 Python 测试再次全部通过。另以 LLVM 20 单独执行 P04 实际工具链检查通过。完整 Modules 检查需要配套扫描器，保持独立命令，并已加入 CI 配置；该配置尚未取得远端执行证据。

## 首次性能样本

以下是切换 C++23 基线之前、Core/Runtime 使用 C++17 时记录的首次样本，保留作历史参考，不作为当前 C++23 版本的性能实测。环境为 macOS arm64、Apple Clang 21；13 个项目翻译单元，另含 Driver/Runtime 准备工作。未清空操作系统页缓存，未统计 CPU、峰值内存或 I/O。

| 场景 | 墙钟时间样本 | 项目编译数 | 链接数 |
|---|---:|---:|---:|
| 首次 Driver 与全量构建 | 2.856 秒 | 13 | 1 |
| 三次空构建 | 0.642–0.644 秒 | 0 | 0 |
| 单源码修改 | 0.694 秒 | 1 | 1 |
| 公共头修改 | 0.996 秒 | 12 | 1 |

数据用于建立初始参考，不代表稳定性能承诺。`build/benchmark.json` 是本地生成报告，已通过 `.gitignore` 排除；CI 将上传各平台报告。完整 PRD 基准与 CMake/Ninja 等价对照属于后续工作。

## 下一步

1. 推送 Linux LLVM 20 的自举空构建修复后，在远端复验完整测试和基准。
2. 补齐 P02/P04 原型的 Linux CI 证据；依据原型限制评审阶段 Interface，正式包和 Lock 实现留待 M3。
3. 将 P04 的生成前驱、模块扫描图和 BMI 身份结论用于后续 Adapter 设计；生产 Modules 能力留待 M5。
4. 按用户要求并行推进 M1：Shared/ObjectLibrary 最小切片已交付本机验证，继续生成文件、测试/安装、平台进程与资源感知调度；M0 远端验收单独跟踪。

所有产品层 OPEN 仍以[架构决策登记](../CXX_ARCHITECTURE_v1.0.md)为准；实验实现不自动将提案转为稳定承诺。
