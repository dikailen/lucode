#!/usr/bin/env python3
"""
目录结构分析脚本
用于深入分析项目的目录结构和代码组织
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

def analyze_directory_structure(project_root: str, max_depth: int = 3) -> Dict[str, Any]:
    """分析项目目录结构"""
    structure = {
        "root": project_root,
        "tree": {},
        "key_files": [],
        "code_directories": [],
        "config_directories": [],
        "test_directories": [],
        "documentation_directories": [],
        "analysis_summary": {}
    }
    
    root_path = Path(project_root)
    
    if not root_path.exists():
        raise FileNotFoundError(f"项目路径不存在: {project_root}")
    
    # 生成目录树
    structure["tree"] = _generate_directory_tree(root_path, max_depth)
    
    # 分析关键文件
    structure["key_files"] = _find_key_files(root_path)
    
    # 分析目录类型
    structure = _categorize_directories(root_path, structure)
    
    # 生成分析摘要
    structure["analysis_summary"] = _generate_analysis_summary(structure)
    
    return structure

def _generate_directory_tree(path: Path, max_depth: int, current_depth: int = 0) -> Dict[str, Any]:
    """生成目录树结构"""
    if current_depth >= max_depth:
        return {}
    
    tree = {}
    
    try:
        for item in path.iterdir():
            if item.name.startswith('.'):
                continue
                
            if item.is_dir():
                tree[item.name] = {
                    "type": "directory",
                    "path": str(item),
                    "children": _generate_directory_tree(item, max_depth, current_depth + 1)
                }
            else:
                if item.name not in tree:
                    tree[item.name] = []
                tree[item.name].append({
                    "type": "file",
                    "path": str(item),
                    "extension": item.suffix.lower()
                })
    except PermissionError:
        pass
    
    return tree

def _find_key_files(root_path: Path) -> List[Dict[str, str]]:
    """查找关键配置文件"""
    key_files = []
    key_file_patterns = [
        ('package.json', '包管理配置'),
        ('pom.xml', 'Maven项目配置'),
        ('build.gradle', 'Gradle项目配置'),
        ('Cargo.toml', 'Rust项目配置'),
        ('go.mod', 'Go模块配置'),
        ('requirements.txt', 'Python依赖配置'),
        ('tsconfig.json', 'TypeScript配置'),
        ('webpack.config.js', 'Webpack配置'),
        ('vite.config.js', 'Vite配置'),
        ('next.config.js', 'Next.js配置'),
        ('nuxt.config.js', 'Nuxt.js配置'),
        ('dockerfile', 'Docker构建文件'),
        ('docker-compose.yml', 'Docker编排配置'),
        ('.env', '环境变量配置'),
        ('config.js', '通用配置文件'),
        ('settings.py', 'Python设置文件'),
        ('appsettings.json', '.NET应用配置'),
        ('web.config', 'IIS配置'),
        ('nginx.conf', 'Nginx配置'),
        ('.gitignore', 'Git忽略文件'),
        ('readme.md', '项目说明文档'),
        ('license', '许可证文件'),
        ('contributing.md', '贡献指南')
    ]
    
    for pattern, description in key_file_patterns:
        file_path = root_path / pattern
        if file_path.exists():
            key_files.append({
                "filename": pattern,
                "path": str(file_path),
                "description": description,
                "size": file_path.stat().st_size
            })
    
    return key_files

def _categorize_directories(root_path: Path, structure: Dict[str, Any]) -> Dict[str, Any]:
    """ categorize directories by their purpose"""
    directory_types = {
        'code_directories': ['src', 'source', 'lib', 'components', 'views', 'controllers', 'models', 'services', 'api'],
        'config_directories': ['config', 'conf', 'settings', 'env'],
        'test_directories': ['tests', 'test', '__tests__', 'spec'],
        'documentation_directories': ['docs', 'documentation', 'wiki'],
        'build_directories': ['build', 'dist', 'target', 'out'],
        'asset_directories': ['assets', 'static', 'public', 'resources', 'media'],
        'data_directories': ['data', 'database', 'db', 'fixtures'],
        'deployment_directories': ['deploy', 'deployment', 'scripts', 'bin']
    }
    
    found_directories = {}
    
    for item in root_path.iterdir():
        if item.is_dir() and not item.name.startswith('.'):
            dir_name = item.name.lower()
            
            for category, keywords in directory_types.items():
                if dir_name in keywords:
                    if category not in found_directories:
                        found_directories[category] = []
                    found_directories[category].append(str(item))
                    
                    # 添加到主结构
                    if category not in structure:
                        structure[category] = []
                    structure[category].append(str(item))
                    break
    
    return structure

def _generate_analysis_summary(structure: Dict[str, Any]) -> Dict[str, str]:
    """生成分析摘要"""
    summary = {}
    
    # 项目类型判断
    if 'key_files' in structure:
        package_json = any(f['filename'] == 'package.json' for f in structure['key_files'])
        pom_xml = any(f['filename'] == 'pom.xml' for f in structure['key_files'])
        requirements_txt = any(f['filename'] == 'requirements.txt' for f in structure['key_files'])
        cargo_toml = any(f['filename'] == 'Cargo.toml' for f in structure['key_files'])
        go_mod = any(f['filename'] == 'go.mod' for f in structure['key_files'])
        
        if package_json:
            summary['project_type'] = 'JavaScript/Node.js'
        elif pom_xml:
            summary['project_type'] = 'Java Maven'
        elif requirements_txt:
            summary['project_type'] = 'Python'
        elif cargo_toml:
            summary['project_type'] = 'Rust'
        elif go_mod:
            summary['project_type'] = 'Go'
        else:
            summary['project_type'] = 'Unknown'
    
    # 代码组织评估
    if 'code_directories' in structure:
        code_dirs_count = len(structure['code_directories'])
        if code_dirs_count >= 4:
            summary['code_organization'] = 'Well-organized (multiple specialized directories)'
        elif code_dirs_count >= 2:
            summary['code_organization'] = 'Moderately organized (some separation of concerns)'
        else:
            summary['code_organization'] = 'Simple structure (minimal directory separation)'
    else:
        summary['code_organization'] = 'No clear code directories found'
    
    # 测试覆盖评估
    if 'test_directories' in structure:
        test_dirs_count = len(structure['test_directories'])
        if test_dirs_count >= 2:
            summary['test_coverage'] = 'Comprehensive (multiple test directories)'
        elif test_dirs_count == 1:
            summary['test_coverage'] = 'Present (test directory found)'
        else:
            summary['test_coverage'] = 'Limited or no test directories found'
    else:
        summary['test_coverage'] = 'No test directories found'
    
    # 文档评估
    if 'documentation_directories' in structure:
        summary['documentation'] = 'Documentation directory present'
    else:
        summary['documentation'] = 'No dedicated documentation directory'
    
    # 构建系统评估
    build_files = [f for f in structure.get('key_files', []) if f['filename'] in ['package.json', 'pom.xml', 'build.gradle', 'Cargo.toml', 'Makefile']]
    if build_files:
        summary['build_system'] = f'Build system detected: {len(build_files)} configuration files'
    else:
        summary['build_system'] = 'No build configuration files found'
    
    return summary

def print_directory_tree(structure: Dict[str, Any], indent: str = ""):
    """打印目录树"""
    if "tree" in structure:
        _print_tree_node(structure["tree"], indent)

def _print_tree_node(node: Dict[str, Any], indent: str = ""):
    """递归打印树节点"""
    for name, info in node.items():
        if isinstance(info, dict) and info.get("type") == "directory":
            print(f"{indent}📁 {name}/")
            if "children" in info:
                _print_tree_node(info["children"], indent + "  ")
        elif isinstance(info, list):
            for item in info:
                if item.get("type") == "file":
                    ext = item.get("extension", "")
                    icon = "📄" if ext not in [".js", ".py", ".java", ".go", ".rs", ".cpp", ".c"] else "💻"
                    print(f"{indent}{icon} {name}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
        max_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        result = analyze_directory_structure(project_path, max_depth)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("请提供项目路径作为参数")