# Python 开发规范

## 适用场景

本规范适用于 Python 项目，不限特定框架（FastAPI、Django、Flask 等均适用）。当检测到 `.py` 文件或用户显式要求 Python 开发时，按本规范的强制性规则执行。

## 强制性规则

### 代码风格（PEP 8）

- 使用 **4 个空格** 缩进，禁止使用 tab。
- 单行长度控制在 **100 字符以内**，最大不超过 120。
- 导入按以下顺序分组，组间空一行：
  1. 标准库导入（`import os`, `from pathlib import Path`）
  2. 第三方库导入（`import requests`, `from pydantic import BaseModel`）
  3. 本地模块导入（`from .models import User`, `from .utils import helper`）
- 每组内按字母序排列，禁止 `from module import *` 通配符导入。
- 使用 `ruff` 或 `black` + `isort` 自动格式化，减少人工干预。

### 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 变量 / 函数 / 方法 | `snake_case` | `user_email`, `get_user_by_id()` |
| 类 | `PascalCase` | `UserProfile`, `OrderService` |
| 常量 | `UPPER_CASE` | `MAX_RETRIES`, `DEFAULT_TIMEOUT` |
| 模块文件 | `snake_case` 简短名 | `model_utils.py` |
| 受保护方法 | 单下划线前缀 `_` | `_internal_helper()` |
| 私有方法 | 双下划线前缀 `__` | `__private_method()` |

### 类型标注（PEP 484 / Python 3.10+）

- **所有公共函数和方法必须添加类型标注**，包括参数和返回值。
- 优先使用 Python 3.10+ 简化语法：`str | None` 代替 `Optional[str]`，`str | int` 代替 `Union[str, int]`。
- 使用 `mypy` 或 Pyright 进行静态类型检查，提交前确保无类型错误。

```python
# 推荐写法（3.10+）
def get_user(user_id: int) -> dict[str, str] | None:
    ...

# 类属性标注
class UserService:
    db: Database
    cache: Cache | None = None

    def __init__(self, db: Database) -> None:
        self.db = db
```

### 数据结构

- **优先使用 dataclass**（Python 3.7+）定义数据模型，替代裸 dict 和非类型化 namedtuple。
- 需要数据校验、序列化/反序列化时，使用 **Pydantic BaseModel**。
- 避免用 dict 在函数之间传递结构不透明的数据。

```python
from dataclasses import dataclass

@dataclass
class User:
    id: int
    name: str
    email: str
    active: bool = True
```

### 文件操作
- 使用 `pathlib.Path` 处理所有文件路径，禁止使用 `os.path` 和字符串拼接路径。
- 使用 `with` 上下文管理器打开文件和所有需要释放的资源。

```python
from pathlib import Path

config_path = Path("config/app.yml")
content = config_path.read_text(encoding="utf-8")
```

### 字符串格式化

- 使用 **f-string** 进行字符串格式化，禁止使用 `%` 格式化或 `.format()`。

### 日志

- 使用 `logging` 模块输出日志，**禁止在业务代码中使用 `print()`** 进行调试或输出。
- 按模块级别获取 logger：`logger = logging.getLogger(__name__)`。

### 空值判断

- 使用 `is None` 和 `is not None` 判断空值，禁止使用 `== None`。
- 利用空序列的 falsy 特性优雅判断：`if not items:` 而非 `if len(items) == 0:`。

### 迭代与推导

- 简单转换场景优先使用列表推导/生成器表达式，避免手动写 for 循环追加元素。
- 复杂逻辑（嵌套条件、多步骤转换）仍用传统 for 循环以保持可读性，不要在推导式中堆砌复杂表达式。
- 大数据量场景使用生成器表达式 `(x for x in items if ...)` 节省内存。

### 可变默认参数

- 禁止在函数定义中使用可变对象作为默认参数（如 `def f(x, items=[])`）。
- 正确写法：`def f(x, items: list | None = None):` 然后在函数体内 `if items is None: items = []`。

### 异常处理

- 捕获具体异常类型，禁止使用裸 `except:` 或 `except Exception:`（除非确实需要）。
- 使用 `raise from` 链接异常时保留完整的异常链。

### 测试

- 使用 `pytest` 编写测试，利用 fixture 和 parametrize 减少重复代码。
- 测试文件命名：`test_<模块名>.py`，测试函数命名：`test_<功能描述>`。
