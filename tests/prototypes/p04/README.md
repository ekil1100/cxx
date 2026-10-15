# P04：初始 Modules 原型

## 用途与结论

本目录是独立、可重复的编译器能力原型。Homebrew LLVM **20.1.8** 和
**21.1.8** 均已在本机完成真实 C++20 命名模块链路：接口分区 → 主接口 →
导入者 → 可执行程序。分区修改及生成头修改均改变两个 BMI 的内容摘要，并产生
可观察的输出变化；删除产物后的干净重建与修改后的输出一致。

验证通过的是 Python 编排的**显式 Clang 命令**。生产 CXX 的 Build API、
Compiler Adapter、DAG、Scheduler 和缓存均未参与。当前 M0 的 Modules 状态仍为待实现。
本次覆盖 P04 的初始探针，M5 的完整 Modules/PCH/Unity 验收仍需后续工作。

依据：

- [PRD §4.1、§6.3、§10 验收项 7](../../../CXX_PRD_v1.0.md)：项目模块、依赖扫描、BMI、PCH/Unity 共存。
- [架构 §7.1–7.2](../../../CXX_ARCHITECTURE_v1.0.md)：生成前驱先于扫描、显式 BMI、完整输入身份。
- [实现计划 §3 P04、M5、§7.1](../../../CXX_IMPLEMENTATION_PLAN_v1.0.md)：可重复证据及修改后与干净构建对照。
- [开发约定](../../../CONTRIBUTING.md)：Python 标准库、真实编译器、参数数组、隔离目录、英文程序诊断。

## 环境准备

使用已有工具，无新增包和工具安装步骤。要求：

- Python 3.14 或更新版本。
- Linux/macOS 上可执行的 Clang、配套 `clang-scan-deps`、系统链接器及 C++ 标准库头文件。
- 编译器支持 `-std=c++20`、命名模块、接口分区、`--precompile`、显式 `-fmodule-file` 映射。
- 扫描器支持编译数据库及 `-format=p1689`，输出版本为 1。
- 原型要求编译器与扫描器的发行版本号完全相同；这是本探针的配对约束，版本相同本身仍需真实构建验证。

默认选择顺序为 `CXX_BOOTSTRAP_COMPILER`、已知 LLVM 安装路径
（Homebrew LLVM 20、无版本 LLVM、LLVM 21、Linux `/usr/lib/llvm-20`）、最后 PATH 中的
`clang++`。`--compiler` 优先。默认扫描器仅从选中编译器的同目录获取；可用
`--scanner` 显式指定。保留 `clang++` 名称，避免解析符号链接后改变驱动模式。
选中工具失败后立即返回错误，避免换用另一套工具造成误判。

扫描器缺失、版本不匹配、命令失败、扫描结构不符、BMI 未随修改变化或程序输出错误，
均返回退出码 **1**，且不会输出通过结论。命令超时为 120 秒。命令行参数错误沿用
`argparse` 的退出码 2。

## 配置与运行

从仓库根目录运行：

```sh
python3 tests/prototypes/p04/run.py --compiler /opt/homebrew/opt/llvm@20/bin/clang++ --keep
python3 tests/prototypes/p04/test_runner.py
```

省略 `--keep` 时，成功和失败运行的工作目录都会清理。保留时，全部输入、编译数据库、
各阶段扫描 JSON、depfile、BMI、目标文件、程序及 `report.json` 位于本目录忽略的
`.runs/p04 <随机后缀>/`。每次创建独立目录，支持重复或并发运行；目录中的空格同时覆盖
参数数组传递。源 fixture 始终保持原样，修改发生在工作副本内。

`report.json` 包含平台、Python、完整工具版本、每条命令的 argv/cwd/退出码/stdout/stderr、
各阶段输出与 BMI SHA-256。原型继承调用环境，没有建立封闭工具链或可共享缓存身份。

### 实验顺序

| 阶段 | 操作 | 必须观察到的结果 |
|---|---|---|
| 缺少生成输入 | 首次扫描时故意缺少 `generated.hpp` | 扫描返回非零，诊断提到该文件 |
| 初始构建 | Python 生成 `P04_BASE=40`，扫描后编译 | 程序输出 `42` |
| 分区修改 | `value.cppm` 中 `+ 2` 改为 `+ 3` | 输出 `43`；分区和主接口 BMI 摘要均变化 |
| 生成头修改 | 重新生成 `P04_BASE=50`，重新扫描 | 输出 `53`；两个 BMI 摘要再次变化 |
| 干净重建 | 删除两个 BMI、三个对象及程序，保留修改后输入 | 从零生成产物，输出仍为 `53` |

生成头位于分区的全局模块片段，通过文本包含提供宏；它是普通头文件，未测试 Header Unit。
正常阶段始终遵循“生成输入 → 扫描 → BMI → 对象 → 链接 → 运行”。缺少输入的首次扫描
是专用负向对照。

扫描检查覆盖：恰好三个 TU、唯一且正确的 `primary-output`、提供者名称及源路径、
`is-interface=true`、精确的导入集合：

```text
generated.hpp → p04:value (value.cppm) → p04 (api.cppm) → main.cpp → app
```

P1689 在此提供模块依赖关系。普通头依赖由分区预编译的 `-MD -MF value.d` 补充，
运行器检查其中包含 `generated.hpp`。扫描结果经过验证，但执行顺序仍按这个固定 fixture
显式编排；本目录没有通用拓扑排序器。

### 实际命令形态

以下参数与实测一致；`W` 表示报告中的绝对工作目录，命令在 `W` 内执行。
两个证据 JSON 保留了每次调用的完整绝对路径及原始输出。

```sh
C=/opt/homebrew/opt/llvm@20/bin/clang++
S=/opt/homebrew/opt/llvm@20/bin/clang-scan-deps
# W is the absolute work directory printed by run.py.
cd "$W"
"$C" --version
"$S" --version
sw_vers
xcrun --show-sdk-path
"$S" -format=p1689 "-compilation-database=$W/compile_commands.json"
"$C" -std=c++20 -O0 -Wall -Wextra -Wpedantic --precompile value.cppm -MD -MF value.d -o value.pcm
"$C" -std=c++20 -O0 -Wall -Wextra -Wpedantic --precompile api.cppm "-fmodule-file=p04:value=$W/value.pcm" -o api.pcm
"$C" -std=c++20 -O0 -Wall -Wextra -Wpedantic -c value.pcm -o value.o
"$C" -std=c++20 -O0 -Wall -Wextra -Wpedantic -c api.pcm "-fmodule-file=p04:value=$W/value.pcm" -o api.o
"$C" -std=c++20 -O0 -Wall -Wextra -Wpedantic -c main.cpp "-fmodule-file=p04:value=$W/value.pcm" "-fmodule-file=p04=$W/api.pcm" -o main.o
"$C" value.o api.o main.o -o app
"$W/app"
```

编译数据库有三个条目，均使用上述语言/警告选项及绝对路径 `-c <source> -o <object>`，
并显式设置 `directory`、`file`、`arguments`、`output`。扫描发生在 BMI 产生之前，
因此数据库不含指向尚未生成 BMI 的映射。后续编译使用扫描已确认关系的显式 BMI 路径。

接入时发现并修正的实测差异：

1. LLVM 20.1.8 的本次 P1689 调用拒绝 `-j=1`，诊断为 `unknown argument '-j'`；最终命令省略它。
2. 仅有 argv 中的 `-o` 时，此扫描器输出空 `primary-output`；增加数据库的 `output` 后获得准确路径。
3. 主接口 BMI 与分区 BMI 是两个显式产物；对象编译和导入者使用完整的命名映射，避免依赖隐式搜索。

## 实测记录

平台为 `macOS 27.0.1`，构建号 `26A434`，`arm64`，Darwin `27.0.0`；
Python `3.9.6`。编译器目标均为 `arm64-apple-darwin27.0.0`。
`xcrun --show-sdk-path` 返回 `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk`。
Homebrew 编译器报告配置文件 `/opt/homebrew/etc/clang/arm64-apple-darwin.cfg`；
运行沿用该配置及主机默认 SDK 查找，没有显式覆盖 sysroot。

| 工具组合 | 版本及路径 | 结果 |
|---|---|---|
| Homebrew LLVM 20 | Clang `20.1.8`，扫描器 `20.1.8`；`/opt/homebrew/opt/llvm@20/bin/`；实际安装 `/opt/homebrew/Cellar/llvm@20/20.1.8/bin` | 完整运行通过；另一个全新目录重复运行通过 |
| Homebrew LLVM 21 | Clang `21.1.8`，扫描器 `21.1.8`；`/opt/homebrew/opt/llvm@21/bin/`；实际安装 `/opt/homebrew/Cellar/llvm@21/21.1.8/bin` | 完整运行通过 |
| Apple Clang | `/usr/bin/clang++`，`21.0.0 (clang-2100.3.34.2)`；报告安装位置 `/Library/Developer/CommandLineTools/usr/bin` | 默认同目录扫描器缺失，运行器退出 1；此组合模块能力待验证 |
| LLVM 20 编译器 + LLVM 21 扫描器 | 上述实际路径 | 版本配对检查退出 1，编译前拒绝 |
| 不存在的编译器或扫描器 | 本目录 `missing-tool` | 两条失败路径均退出 1，无通过结论 |

完整原始成功证据：[LLVM 20](evidence/llvm20.json)、[LLVM 21](evidence/llvm21.json)。
每份记录含 37 次外部调用，其中缺少生成头的扫描失败是预期结果。两套工具均观察到
`42 → 43 → 53 → 53`。摘要只在同一次运行内比较；不同目录或编译器的 BMI 字节相等性
没有作为可移植性保证。

补充验证命令：

```sh
python3 tests/prototypes/p04/run.py --compiler /opt/homebrew/opt/llvm@21/bin/clang++ --keep
python3 tests/prototypes/p04/run.py --compiler /opt/homebrew/opt/llvm@20/bin/clang++ --scanner /opt/homebrew/opt/llvm@21/bin/clang-scan-deps
python3 tests/prototypes/p04/run.py --compiler /usr/bin/clang++
python3 tests/prototypes/p04/test_runner.py
git diff --check
```

`test_runner.py` 的 3 个测试通过，包含真实扫描证据回读、8 类扫描契约破坏、2 条缺工具路径。
本次执行限定为原型相关检查；仓库全量 bootstrap/测试未运行，以保持共享构建目录及生产文件不变。

## PCH / Unity 边界与限制

| 能力 | 本次范围 | 后续边界 |
|---|---|---|
| 命名模块接口、接口分区、导入 | 两套 LLVM 实测通过 | 按工具链与参数组合建立能力矩阵 |
| PCH | 未创建或消费 PCH，共存能力待验证 | 独立 Action/Artifact；PCH 使用条件与编译参数必须匹配 |
| Unity | 未合并任何 TU | 接口及接口分区默认排除；普通源文件按固定批大小稳定生成 |
| Modules + PCH + Unity 项目共存 | 待验证 | 独立模块 TU、独立 PCH 消费者、普通 Unity 源文件共同链接，作为 M5 验收用例 |

本原型每个阶段显式重编译整个最小链路。它证明变更可传播及 BMI 可重新生成，
增量失效决策、空构建复用和最小重建范围仍待实现。也未覆盖生成头切换 import、
实现分区、重复提供者/缺失提供者/环的通用图诊断、参数失配、跨 Target/Profile、
缓存恢复、跨根路径复用、并行调度、失败产物发布、GCC/MSVC、Linux/Windows。
构建脚本自身的模块导入保持产品文档中的 OPEN 状态。

## 集成建议

1. 将工具配对和真实功能探针放入 Compiler Adapter 能力检查；扫描器缺失或组合未验证时，提前给出可操作诊断。
2. 将生成头的生产者建为扫描前驱；先完成扫描和图校验，再确定输入摘要及查询缓存。
3. 保留编译数据库 `output`，规范化模块提供者、分区和 import，同时汇入普通头依赖；接入重复/缺失提供者和环检查。
4. 把两个 BMI 分别建为 Artifact，并纳入工具链、平台、SDK、标准、宏、选项及传递 BMI 身份；随后验证 no-op 和最小失效，避免每次全编译被统计为增量能力。
5. 用同样的输出断言新增真实 `cxx build/run` 回归，证明经过 CXX DAG/Scheduler；本目录保留为底层工具能力对照。
6. M5 补充独立 PCH 消费者与普通 Unity 批次的项目共存场景，并测试模块源文件排除。公开能力矩阵只列实测组合。
