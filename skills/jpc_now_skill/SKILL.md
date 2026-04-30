---
name: jpc_now_skill
description: 多语言开发规范技能，覆盖 Java/Python/C++ 三种语言的编码最佳实践，结合 Karpathy 行为准则。当用户编写、修改、审查 Java/Python/C++ 代码时使用此技能，确保代码风格一致、遵循业界规范、避免常见 LLM 编码错误。触发场景包括：代码开发、代码评审、接口设计、重构、新增功能、修复 bug。即使用户未明确提及"规范"或"最佳实践"，只要涉及上述语言的编码任务，都应使用此技能。
---

# JPC Now Skill — 多语言开发规范

## 概述

本技能整合 Java、Python、C++ 三种语言的开发规范和 Karpathy 行为准则，为不同语言项目提供统一的编码指导框架。

**核心原则：** 根据当前代码文件的后缀名自动识别语言，应用对应语言的开发规范；同时在所有语言中统一应用 Karpathy 行为准则（简单性、外科手术式修改、目标驱动执行）。

## 语言检测

在处理代码之前，按以下规则自动识别语言：

| 文件后缀 | 语言 | 读取的规范文件 |
|----------|------|---------------|
| `.java` | Java | [references/java.md](references/java.md) |
| `.py` | Python | [references/python.md](references/python.md) |
| `.cpp`, `.h`, `.hpp`, `.cc`, `.cxx`, `.hxx` | C++ | [references/cpp.md](references/cpp.md) |

- 若用户显式提及语言名称（"用 Python 写一个..."、"这段 Java 代码..."），以用户声明的语言为准。
- 若同一任务涉及多种语言的文件，为每种文件分别应用对应规范。
- 语言规范参考文件按需读取，避免将所有规范同时加载到上下文。

## 共享行为准则

Karpathy 行为准则对所有语言通用，是编码过程中的底层行为约束。详细内容见 [references/karpathy-guidelines.md](references/karpathy-guidelines.md)。

四条核心准则：
1. **编码前思考** — 明确假设，不隐藏困惑，说明权衡
2. **简单性优先** — 用最少代码解决问题，不添加推测性内容
3. **外科手术式修改** — 只改必须改的部分，匹配现有风格
4. **目标驱动执行** — 定义可验证的成功标准，循环直到通过

## 工作流程

### 第一步：识别语言与读取规范

1. 查看当前工作目录中涉及的文件后缀，按语言检测规则确定语言。
2. 读取对应的 `references/` 下的语言规范文件。
3. 始终遵循 [references/karpathy-guidelines.md](references/karpathy-guidelines.md) 中的行为准则。

### 第二步：分层设计（适用所有语言）

- 把参数校验、业务逻辑、数据访问、接口定义分别放到合适层级，不混写。
- 先理解现有代码的架构模式和既有写法，优先沿用而非另起一套。
- 如果现有写法与规范冲突，优先保持仓库一致性。

### 第三步：编码实现

按照对应语言规范文件中的强制性规则进行编码。各语言的核心规范要点概述如下：

#### Java 规范核心
- Knife4j/OpenAPI 接口文档注解
- Hutool BeanUtil + Builder 模式对象转换
- MyBatis-Plus lambda 风格查询/更新
- DTO 层 javax.validation 参数校验
- 禁止 N+1 查询，批量收集 ID 后一次查询
- 优先 application.yml 配置，避免新增 config 类
- 中文注释，解释"为什么"

#### Python 规范核心
- PEP 8 代码风格（4空格缩进、导入排序、命名规范）
- 类型标注（Python 3.10+ 语法），mypy 静态检查
- dataclass / Pydantic 替代裸 dict
- pathlib 替代 os.path，with 上下文管理
- f-string 替代 .format()
- logging 替代 print()
- 列表推导/生成器表达式替代简单 for 循环

#### C++ 规范核心
- RAII 管理所有资源，禁止裸 new/delete
- unique_ptr 优先，shared_ptr 仅共享时用
- 裸指针仅作非拥有观察者
- const 默认，const& 传参，const 成员函数
- C++17/20 特性（optional、variant、string_view、span）
- enum class 替代裸 enum
- nullptr 替代 NULL/0
- 优先栈分配，constexpr 编译期计算

### 第四步：自查与验证

实现完成后，按本技能末尾对应语言的检查清单逐项自查，再提交结果。

## 输出期望

- **编写 Java 代码时：** 产出符合 Spring Boot/MyBatis-Plus 分层规范的代码，补齐 Knife4j 文档、DTO 校验、lambda 查询。
- **编写 Python 代码时：** 产出符合 PEP 8 的代码，类型标注完整，使用 dataclass/Pydantic，pathlib 和 f-string。
- **编写 C++ 代码时：** 产出符合 RAII 的现代 C++ 代码，使用智能指针管理资源，const 正确，利用 C++17/20 特性。
- **代码评审时：** 优先指出违反语言规范和 Karpathy 行为准则的地方，给出符合规范的替代实现。
- **所有场景：** 始终应用简单性优先、外科手术式修改、目标驱动执行的行为准则。

## 检查清单

### Java
- [ ] 是否补齐了 Knife4j/OpenAPI 文档信息
- [ ] 是否避免了大段 setXxx()，优先使用 Builder 或 Hutool 转换
- [ ] 是否使用了 lambda 风格查询/更新 API
- [ ] 是否把基础参数校验放在 DTO 层
- [ ] 是否避免了循环内反复查库（N+1）
- [ ] 是否没有手动维护自动时间字段
- [ ] 是否优先使用 application.yml 配置，不新增 config 类
- [ ] 是否补充了中文注释，注释重点清楚

### Python
- [ ] 是否遵循 PEP 8（4空格缩进、导入分组排序、snake_case 命名）
- [ ] 是否所有函数都添加了类型标注
- [ ] 是否优先使用 dataclass/Pydantic，避免裸 dict 传递数据
- [ ] 是否使用 pathlib 处理文件路径
- [ ] 是否使用 f-string 进行字符串格式化
- [ ] 是否使用 logging 模块，没有 print() 调试
- [ ] 是否使用 with 语句管理文件/网络等资源
- [ ] 是否避免了对可变默认参数的依赖

### C++
- [ ] 是否所有资源由 RAII 管理，没有裸 new/delete
- [ ] 是否优先使用 unique_ptr 而非 shared_ptr
- [ ] 是否所有不修改的成员函数标记为 const
- [ ] 是否传参优先使用 const&
- [ ] 是否使用 nullptr 而非 NULL/0
- [ ] 是否使用 enum class 而非裸 enum
- [ ] 是否优先栈分配，避免不必要的堆分配
- [ ] 是否利用了 C++17/20 特性（optional、string_view、span 等）

### Karpathy 行为准则
- [ ] 是否在编码前明确了假设和权衡
- [ ] 是否遵循了简单性优先原则，没有过度设计
- [ ] 是否进行了外科手术式修改，只更改必要部分
- [ ] 是否定义了可验证的成功标准
- [ ] 是否匹配了现有代码风格
