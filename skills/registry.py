SKILLS = {
    "lucode_native_capability": {
        "folder": "../core_skills/lucode-native-capability",
        "description": "Lucode 原生终端代理能力契约，负责读写文件、CLI 优先、MCP 兜底、上下文复用、审批安全和 full 模式主管协作边界。",
    },
    "jpc_now_skill": {
        "folder": "jpc_now_skill",
        "description": "负责 Java/Python/C++ 代码开发、代码评审、重构、修复 bug 和代码规范建议。",
    },
    "humanizer_zh": {
        "folder": "Humanizer-zh-main",
        "description": "负责中文文本润色、去除 AI 写作痕迹、改写为更自然的人类表达。",
    },
    "project_explorer": {
        "folder": "project-explorer",
        "description": "负责分析项目结构、技术栈、目录用途、配置文件、运行方式和开发入手点。",
    },
    "skill_creator": {
        "folder": "skill-creator",
        "description": "负责创建、修改、优化和评估 skill，包括编写 SKILL.md 和改进触发描述。",
    },
    "task_router": {
        "folder": "task-router",
        "description": "负责判断用户问题应该交给哪个专家 Agent，不直接完成专家任务。",
    },
    "query_refiner": {
        "folder": "query-refiner",
        "description": "负责在主脑规划前优化用户原始问题，提取意图、约束和潜在歧义。",
    },
    "orchestrator_planner": {
        "folder": "orchestrator-planner",
        "description": "负责读取 Skill/MCP 图书馆并输出动态多智能体调度计划。",
    },
    "final_synthesizer": {
        "folder": "final-synthesizer",
        "description": "负责在多 Agent 任务完成后汇总多个结果，生成最终回答。",
    },
    "full_worker_contract": {
        "folder": "full-worker-contract",
        "description": "full 团队模式 Worker 角色契约，约束员工边界、证据纪律和 WorkerReport 输出。",
    },
    "serial_executor_contract": {
        "folder": "serial-executor-contract",
        "description": "serial 模式顺序执行 Agent 角色契约，约束串行任务边界和模式话术。",
    },
}
