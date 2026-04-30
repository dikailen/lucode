# C++ 开发规范

## 适用场景

本规范适用于现代 C++（C++17/20）项目。当检测到 `.cpp`、`.h`、`.hpp`、`.cc`、`.cxx`、`.hxx` 文件或用户显式要求 C++ 开发时，按本规范的强制性规则执行。

## 强制性规则

### 资源管理 — RAII

- **所有资源必须由 RAII 管理**，将资源生命周期绑定到对象生命周期。包括但不限于：内存、文件句柄、锁、套接字、数据库连接。
- **禁止使用裸 `new` 和 `delete`**。使用 `std::make_unique<T>()` 和 `std::make_shared<T>()` 创建对象。
- **禁止在 C++ 中使用 `malloc()`/`free()`**。
- 避免在单个表达式中进行多次资源分配，防止异常安全风险。
- 若必须与非 RAII 的 C API 交互，将裸资源立即封装到 RAII 包装器中。

### 智能指针

- **优先使用 `std::unique_ptr`**，只有在所有权确实需要共享时才使用 `std::shared_ptr`。
- 裸指针 `T*` 只能是 **非拥有观察者**（observer），不代表所有权。看到裸指针应理解为"我不用负责释放"。
- 使用 `std::weak_ptr` 打破 `shared_ptr` 循环引用。

```cpp
// 推荐：unique_ptr 表示独占所有权
auto widget = std::make_unique<Widget>("config");

// 推荐：裸指针作为观察者
void render(const Widget* w) {
    if (w) w->draw();
}
render(widget.get());

// 仅在真正需要共享时用 shared_ptr
auto cache = std::make_shared<Cache>(1024);
```

### const 正确性

- **默认将对象声明为 `const`**，只有确实需要修改时才不加 const。
- **成员函数默认标记为 `const`**，只有确实会修改成员变量的函数才不加。
- 传参优先使用 `const T&`（只读引用），避免不必要的拷贝。
- 使用 `constexpr` 标记所有编译期可计算的值和函数，让计算在编译期完成。

```cpp
// 默认 const
const int max_retries = 5;
constexpr double pi = 3.1415926535;

// const& 传参
void process(const Order& order) {
    // 只读访问 order
}

// const 成员函数
class User {
public:
    std::string name() const { return name_; }  // 不修改成员
    void set_name(std::string name) { name_ = std::move(name); }  // 会修改成员
private:
    std::string name_;
};
```

### 现代 C++ 特性（C++17/20）

优先使用以下 C++17/20 特性来编写更安全、更清晰的代码：

| 特性 | 用途 | 何时使用 |
|------|------|---------|
| `std::optional<T>` | 可能不存在的值 | 代替返回哨兵值（-1、nullptr）或输出参数 |
| `std::variant<Ts...>` | 类型安全的联合体 | 代替裸 union 或多态滥用 |
| `std::string_view` | 非拥有字符串引用 | 函数参数接受只读字符串时 |
| `std::span<T>` (C++20) | 非拥有数组视图 | 替代 `T*` + `size_t` 参数对 |
| `if constexpr` | 编译期条件分支 | 模板代码中按类型选择逻辑 |
| `enum class` | 有作用域枚举 | 始终使用，禁止裸 enum |
| `[[nodiscard]]` | 返回值不可丢弃 | 错误码、资源、重要计算结果 |
| Structured bindings | 解构返回多值 | `auto [name, age] = get_user();` |
| Concepts (C++20) | 模板参数约束 | 替代 SFINAE，提供清晰的模板报错 |

### 其他规则

#### 空指针

- 使用 `nullptr`，禁止使用 `NULL` 或 `0` 表示空指针。

#### 栈分配优先

- 优先在栈上创建对象，避免不必要的堆分配。
- 只有当对象生命周期需要超出当前作用域、对象很大、或者需要多态时，才在堆上分配。

#### 类型安全

- 始终使用 `enum class`，禁止裸 `enum`。
- 尽量使用 `auto` 简化类型声明，特别是在迭代器和复杂模板类型的场景。
- 避免 C 风格类型转换，使用 `static_cast`、`dynamic_cast`、`const_cast`、`reinterpret_cast`（仅在必要时使用 reinterpret_cast）。

#### 移动语义

- 对于可能发生大量拷贝的返回，优先利用返回值优化（RVO/NRVO），不要用 `std::move` 干扰编译器。
- 只在确实需要移交所有权时使用 `std::move`。
- 将析构函数设为 `= default` 放在 `.cpp` 实现文件中，特别是包含 `unique_ptr` 成员的类。

#### 函数设计

- 函数保持简短，单一职责。
- 参数超过 3 个时考虑封装为 struct。
- 优先返回 `std::optional` 而非通过引用参数输出结果。
