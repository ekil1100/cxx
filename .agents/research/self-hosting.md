# 自举与种子 SDK 边界

> 调研基线：`a8aea70`，位于本地自举实现之前。下文保留当时的源码行号、建议与验证范围；后续实现状态见 [M0 进展](../../docs/M0_STATUS.md)。

## 结论（当前源码核对）

新 Build API 不必然要求升级启动器二进制。现有启动器可通过 `CXX_SDK_ROOT` 选择新 SDK，并用其中的头文件和 Runtime 源码重新构建 Driver；前提是新 SDK 继续满足启动器的配置、调用协议、文件布局及编译规则。依赖启动器新配置、协议或布局的变更，需要配套升级启动器。

## 当前实现：已确认事实

- **SDK 选择**：进程环境 `CXX_SDK_ROOT` 优先于编译时嵌入值；相对路径以调用目录解析，并检查固定的六个 SDK 文件。当前没有按版本下载、解析或校验种子包的流程。见 `src/main.cpp:187–197`；嵌入路径见 `scripts/build.py:34–40`。
- **工具选择**：开发环境覆盖优先于项目配置，再回退到 PATH 中的 `clang++`、`llvm-ar`（后者可回退 `ar`）。见 `src/main.cpp:172–184,209–212`。
- **Runtime 编译**：外部编译器以 C++23 编译所选 SDK 的 `src/runtime.cpp` 和 `src/support.cpp`，再由归档器生成 `.cxx/driver/runtime.a`。见 `src/main.cpp:217–236`。
- **Driver 编译**：启动器生成 `script.cpp`，包含 SDK 的 `runtime.hpp` 和项目 `build.cpp`，传入 `&build` 及四字段 `Environment`；按项目 C++ 标准编译，链接 Runtime，最后转发命令及参数。见 `src/main.cpp:237–253`、`src/runtime.hpp:5–9`、`include/cxx/cxx.hpp:11–16`。Runtime 调用构建声明并执行目标构建，见 `src/runtime.cpp:432–440`。
- **更新传播**：每次扫描源文件及头文件依赖并计算内容摘要，编译缓存命中时复用产物；归档和链接按输入摘要判定。见 `src/support.cpp:500–574`。启动器版本摘要未进入这些缓存键，因此 API/Runtime 源码变化可以独立触发重建。
- **兼容边界**：配置字段白名单与 Edition 0 限制由启动器处理（`src/main.cpp:135–166`）；固定源文件、编译参数、生成的 `driver_main` 调用和命令转发同样由它控制。仅扩展 Build API 且保持这些边界时，旧启动器有能力使用新 SDK；任意未来 SDK 的兼容性仍需逐个验证。启动器自身编入的 `support.cpp` 也不会因切换 SDK 而更新（`scripts/build.py:38–40`）。

## 外部先例：父会话已确认事实

以下沿用父会话已读取的一手资料，本次未重新联网核验，也未扩展其细节。

- **Rust**：下载 stage0 编译器和库，再构建 stage1/2；`src/stage0` 固定种子信息及校验。来源：[官方自举说明](https://rustc-dev-guide.rust-lang.org/building/bootstrapping/what-bootstrapping-does.html)、[种子信息](https://github.com/rust-lang/rust/blob/main/src/stage0)。
- **Ninja**：`configure.py` 的 `Bootstrap` 代理 `ninja_syntax.Writer` 并直接执行同一组规则；`--bootstrap` 先产出 Ninja，再调用自身重建，以减少两套规则的重复维护。来源：[官方 configure.py](https://github.com/ninja-build/ninja/blob/master/configure.py)。
- **CMake**：提供 Unix `./bootstrap; make` 源码引导路径，也支持使用已有 CMake 构建。来源：[官方 README](https://github.com/Kitware/CMake/blob/master/README.rst)。

## CXX 建议（proposed，尚非当前实现）

1. 默认分发**预编译、版本化种子包**，包含启动器及匹配 SDK/Runtime，固定版本和校验；按现有机制，包中应保留编译 Runtime 所需的源码、头文件及依赖。
2. 保留**源码引导备用路径**；当前 `scripts/build.py` 可作为起点。
3. 为构建 CXX 本身维护**稳定的最小构建描述**，尽量让种子引导与自构建共享规则，明确最小 SDK 边界。
4. **CI 验证新产物再构建**：种子构建新产物，测试新产物，再用它重建并运行测试。具体检查标准应单独定义。

CXX 是构建工具，实际 C++ 编译仍由外部 Clang 执行。自构建复用构建描述和执行流程即可，无需照搬 Rust 编译器的全部阶段。

## 验证范围

仅静态阅读上述源码并核对引用位置；未运行构建或兼容性实验。仅新增本文，未修改产品代码，未提交。
