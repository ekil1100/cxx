# P02：单次声明与解析锁原型

## 用途与结论

**状态：提案验证。** 本目录验证 [P02](../../../CXX_IMPLEMENTATION_PLAN_v1.0.md#3-前置原型与风险消减) 的最小闭环。8 项定向测试通过：一次 C++ 声明返回稳定依赖句柄；声明结束后按静态元数据展开传递依赖；各配置域保留旧锁定选择；竞争写入和发布前后进程中断均保留完整解析记录。

原型独立于正式命令和 SDK。所有新增源码位于本目录；运行产物位于自动清理的临时目录。接口、锁格式和配置域键均保持提案状态。此证据覆盖本机 P02 最小切片，完整 M3 验收与跨平台验收仍待实施。

依据：

- [PRD §5.1–5.3](../../../CXX_PRD_v1.0.md)：统一依赖声明、传递依赖、单一解析锁、旧项稳定、严格锁定模式只读。
- [架构 §3.1、§5、§6.1、§6.4](../../../CXX_ARCHITECTURE_v1.0.md)：延迟绑定、值对象模型、多解析域和完整候选提交。
- [ADR-0002](../../../docs/adr/0002-declaration-and-model.md) 与 [ADR-0004](../../../docs/adr/0004-lock-and-protocol-identities.md)：两项决策目前均为提案。

## 安装与运行

前置条件：Python 3.14+、Linux/macOS、支持仓库所用 C++23 功能的 Clang 和标准库。使用仓库已有 `toml++`、`PicoSHA2`；运行器校验现有第三方清单；Python 测试依赖通过仓库的 `requirements-test.txt` 安装。

在仓库根目录执行：

```sh
python3 -m pytest tests/prototypes/p02/run.py --compiler=/path/to/clang++
```

编译器选择顺序为：显式 `--compiler`、环境变量 `CXX_BOOTSTRAP_COMPILER`、`clang++`。运行器沿用现有测试的编译器定位约定，使用 `shutil.which()` 和 `Path.absolute()`，保留符号链接与 `..` 的路径语义。同一编译器用于声明、内部实现、现有支持代码和最终链接。

运行器打印平台、Python、编译器版本、完整编译和测试命令。每次运行创建独立临时目录，包含带空格的路径；二进制、锁、候选文件及守卫文件放在 pytest 管理的临时目录中，保留策略由 pytest 控制。测试有超时，检查点通过管道握手，进程清理覆盖失败分支。测试无需预先构建 `build/cxx`，也无需修改正式构建图。

### 配置

- `registry.toml`：本地、可信的合成包元数据，包含依赖与导出名。
- `declaration.cpp`：按 C++17 编译的声明。`base`、`added` 等变体仅用于测试输入。
- 平台、配置和角色是解析域字段。测试使用 `fixture-a/fixture-b`、`debug/release`、`host/target`。
- `build/locked` 为原型模式参数。它们尚未接入正式 `cxx` 命令。

## 实验设计

### 声明与解析

1. 调用一次 `p02::build(Build&)`。独立计数器遇到再次调用即失败。
2. 首次 `dependency()` 返回所有者与索引组成的值句柄。随后追加 256 次声明以触发存储扩容，再用原句柄及其副本声明两个链接。
3. 关闭声明，将要求和链接复制成值对象。追加依赖被拒绝，跨构建器句柄被拒绝。构建器先销毁，再读取包元数据。
4. 按包主版本要求选择确定版本；已锁定项优先。只展开元数据依赖，解析期间保持声明调用计数为 1。
5. 校验导出名并形成只读解析值。声明阶段仅能表达逻辑链接，接口没有已解析文件或版本查询。

元数据中的 `widget → core` 验证传递依赖；`extra → core` 验证新增根复用旧传递项。三个条件依赖分别测试配置、角色和平台。新解析域可选择 `1.1.0`，已有域中的 `1.0.0` 保持完整身份不变。

### 解析锁与事务

复用 `src/support.cpp` 的 `ProjectLock`、`fingerprint`、`write_atomic`。为复用现有解析库，**实验锁采用 TOML**，与 ADR-0004 建议的 JSON 存在显式格式偏差。解析集合按语义域摘要索引；表和链接排序确定，独立空目录生成的锁字节一致。检查工具通过现有 TOML 库导出 JSON 供 Python 断言。

锁记录包含：独立 `lock_format`、域、规范声明摘要、工具链占位身份、包精确版本、合成来源、元数据摘要、依赖边和导出名。运行期指针与索引、安装绝对路径均留在进程内。

普通模式的顺序：

1. 声明完成，取得元数据。
2. 取得项目守卫锁，随后读取最新 `cxx.lock`。
3. 校验原记录，保留其他域，将当前域解析成完整候选。声明变化需要修改旧包身份时提示显式更新。
4. 校验全部候选集合，使用现有原子写入器写同目录候选文件，再次解析校验候选。
5. 在 `before-publish` 检查点后，用同目录 `rename` 替换 `cxx.lock`；随后到达 `after-publish` 检查点。

现有 `ProjectLock` 使用非阻塞 `flock`。竞争者收到明确错误，稍后作为新命令重试；重试在持锁后读取最新快照，保留另一写者新增的解析域。每次命令执行一次声明，解析过程保持单次声明。

严格锁定模式读取一个完整快照，并校验声明及配置域。该路径跳过守卫文件和候选文件的创建，所有测试同时比较文件内容、文件集合与修改时间。

### 中断矩阵

测试在真实子进程到达检查点后发送 `SIGKILL`，覆盖四种组合：

| 原记录 | 发布前中断 | 发布后中断 |
|---|---|---|
| 首次生成 | `cxx.lock` 保持缺失，候选完整且可解析 | 完整新记录可读取，并通过严格锁定模式 |
| 已有记录 | 原记录字节保持一致且可解析 | 完整新记录可读取，并通过严格锁定模式 |

重启命令能够重新取得守卫锁。遗留候选被忽略；临时目录最后统一清理。首次发布前的“缺失”是有效事务状态，此时尚无已提交解析记录。

## 验证证据

实测平台为 macOS 27.0.1（构建号 `26A434`）、arm64；Python 3.9.6。两套编译器均完成 C++17 声明对象与 C++23 实现的真实编译、链接和测试：

| 编译器路径 | 实际版本 | 结果 |
|---|---|---|
| `/usr/bin/clang++` | Apple Clang `21.0.0 (clang-2100.3.34.2)`，目标 `arm64-apple-darwin27.0.0` | 8 项测试通过 |
| `/opt/homebrew/opt/llvm@20/bin/clang++` | Homebrew Clang `20.1.8`，目标 `arm64-apple-darwin27.0.0` | 8 项测试通过 |

以下为原型初次验证时的历史命令；独立脚本测试入口现已移除，当前复现请使用上节 pytest 命令：

```sh
CXX_BOOTSTRAP_COMPILER=/usr/bin/clang++ python3 tests/prototypes/p02/run.py
python3 tests/prototypes/p02/run.py --compiler /opt/homebrew/opt/llvm@20/bin/clang++
git diff --check -- tests/prototypes/p02
```

运行器实际编译参数如下。`ROOT` 表示仓库绝对路径，`TMP` 表示该次运行的临时目录，`COMPILER` 表示所选编译器；完整展开命令会逐条输出：

```sh
"$COMPILER" -O2 -Wall -Wextra -Wpedantic -std=c++17 -pedantic-errors \
  -c "$ROOT/tests/prototypes/p02/declaration.cpp" -o "$TMP/declaration.o"
"$COMPILER" -O2 -Wall -Wextra -Wpedantic -std=c++23 -I"$ROOT/src" \
  -isystem "$ROOT/third_party/picosha2" -isystem "$ROOT/third_party/tomlplusplus" \
  "$ROOT/tests/prototypes/p02/prototype.cpp" "$ROOT/src/support.cpp" \
  "$TMP/declaration.o" -o "$TMP/p02"
```

声明文件通过 `static_assert(__cplusplus == 201703L)` 验证真实语言模式。内部复用 `support.hpp` 的 C++23 标准库能力检查，并编译显式对象参数递归 lambda。两套工具链均无编译警告。

测试证据与断言：

| 测试 | 主要证据 |
|---|---|
| `test_deferred_resolution_and_old_choices` | 声明计数 1、读取元数据前构建器已销毁、257 个声明后的句柄绑定、独立生成字节一致、新版本可用时旧项稳定、新根复用传递项 |
| `test_configuration_domains` | 同文件四个解析域，平台／配置／角色条件分别生效，旧域逐值不变 |
| `test_locked_is_read_only` | 成功、缺失锁、声明变化、缺失域四条路径的文件内容、集合和修改时间保持一致 |
| `test_failures_preserve_lock` | 不兼容要求、缺失导出、声明异常均保留原锁 |
| `test_metadata_identity_and_invalid_lock` | 同版本元数据变化、未知锁主版本、截断语法和错误包身份均被拒绝 |
| `test_unresolved_metadata_preserves_lock` | 新增依赖缺失或成环时保留原锁 |
| `test_competing_writers_retry_fresh_snapshot` | 首次和已有锁两种场景：竞争者被拒绝，重试后两个写者的域均存在 |
| `test_interruption_before_and_after_publication` | 四种检查点中断组合、实际 `SIGKILL`、可解析候选和已提交记录、重启恢复 |

实际输出节选：

```text
declaration calls=1 handles=stable metadata_reads=0 builder=destroyed
resolved calls=1 sets=1 packages=3 mode=build
checkpoint before-publish
checkpoint after-publish
P02 PASS: 8 tests, including competing writers and 4 publication interruption cases
```

## 限制与待验证项

- **持久化范围**：`write_atomic` 关闭文件后执行重命名，尚无文件及父目录的 `fsync`。本实验验证进程终止时的旧／新完整性。断电、内核崩溃、存储故障、网络文件系统和 Windows 发布语义仍待验证；当前证据无法判断其持久化保证。
- **包解析范围**：可信本地元数据、整数主版本约束、数字三段版本的确定性选择。约束冲突或循环直接失败；回溯求解、完整冲突路径、显式更新和迁移尚未实现。注册表完整模式校验与不可信输入安全验证留给正式实现。
- **身份范围**：工具链为合成占位值；包摘要覆盖规范元数据。实际编译器的工具闭包、下载内容、源码与配方摘要、二进制 ABI、包安装和配方一致性尚未接入。
- **域与模型范围**：平台值用于条件选择，实验未执行交叉编译。每个命令处理一个域；域字段尚未纳入特性、SDK、工具链或全部配置选项。值对象仅覆盖包与逻辑导出绑定，完整目标模型、动作规划和构建尚未接入。
- **句柄范围**：句柄仅在所属构建器的声明期使用；所有者检查和索引验证覆盖当前场景。跨生命周期保留句柄与地址复用需要正式代数设计。
- **事务范围**：竞争策略为明确失败后新命令重试；等待队列与取消尚未实现。实验仅在发布边界中断，原子写入内部中断、并发压力及长期恢复测试留给后续阶段。
- **验证范围**：本机 macOS 两套 Clang 已实测。Linux 与 Windows 尚无本原型证据。本次执行定向测试；全仓自举、完整回归及性能基准留给父级集成，避免本任务写入共享构建目录。

## 文件与父级集成建议

| 文件 | 职责 |
|---|---|
| `api.hpp` | C++17 实验边界、稳定依赖句柄 |
| `declaration.cpp` | 单次声明及存储增长场景 |
| `registry.toml` | 传递依赖、条件依赖与导出元数据 |
| `prototype.cpp` | C++23 收集、解析、校验、事务和检查点 |
| `run.py` | 所选编译器编译、定向断言和子进程故障注入 |
| `README.md` | 提案范围、命令、证据和限制 |

父级集成建议：

1. 先将 `run.py --compiler "$CXX_COMPILER"` 作为独立 P02 检查加入 Linux/macOS CI，继续保留原型隔离。主测试入口可在后续任务显式调用本运行器；本任务保留所有共享文件原样。
2. 在 M0 进展中记录“本机 P02 最小验证通过”，保留 Linux 和完整 M3 门槛的待验证状态。原型结果作为 ADR-0002、ADR-0004 的证据附件，决策状态由评审确认。
3. 正式集成时，在现有 `driver_main` 的声明调用与图验证之间引入收集／解析／冻结接缝，复用已验证的阶段约束。正式包 API 与锁模式另行评审。
4. 在 M2/M3 冻结 JSON 格式、完整域身份、工具链与包整体候选、更新闭包及持久化契约。优先补齐平台事务层的文件刷新和目录持久化，再扩展求解与包构建。

当前仓库未新增生产 API，未修改共享构建入口，未创建提交。
