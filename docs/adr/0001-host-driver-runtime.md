---
status: proposed
---

# ADR-0001：Host Driver 直接链接同 ABI Runtime

PRD 要求 Build Driver 与 Runtime 直接链接，同时允许用户以不同工具链和更新 C++ 标准编译脚本。建议将 Driver 构建为 Host 可执行文件，SDK 分发 Runtime 源码并以与脚本一致的编译器、标准库、CRT 和 ABI 选项构建；建图、解析、调度都在 Driver 进程内进行，CLI 不通过 IPC 传图。

## 比较的方案

- 通用预编译 C++ 动态插件：启动快，但异常、标准库类型、内存分配与 Windows CRT 的 ABI 组合难以通用验证。
- 同工具链源码 Runtime：首次构建成本较高，能够将脚本与 Runtime 限定在同一可验证 ABI 域，建议采用。
- 预编译静态 Runtime：仅作为经过验证的 ABI 配置加速方式，不作为任意工具链默认回退。

## 影响与待确认

Core 使用 C++17，脚本采用声明标准；公共类型跨语言模式的布局仍须实测。Driver/Runtime 缓存包括 SDK 内容及 Host ABI，项目 Action 不常规绑定 CLI 版本。交叉工具链与 Host bootstrap 分开，Host 生成工具也与 Target 产物分开。

建议 V1 脚本以单入口 TU 和普通头文件拆分，暂不支持脚本自身自定义 Modules 或解析后 Package 参与 Driver 编译；项目 Target Modules 必须支持。该用户可见范围仍待确认。

验收：实现计划 P01/P07。此记录依据 [PRD §4、§13.2](../../CXX_PRD_v1.0.md) 与[架构 §3](../../CXX_ARCHITECTURE_v1.0.md)，尚无编译实测结论。
