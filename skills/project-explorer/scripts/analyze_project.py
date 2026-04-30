#!/usr/bin/env python3
"""
项目基本信息分析脚本
用于扫描项目根目录，收集基本信息
"""

import os
import json
import re
from pathlib import Path
from typing import Dict, List, Any

def analyze_project_info(project_root: str) -> Dict[str, Any]:
    """分析项目基本信息"""
    project_info = {
        "project_name": "",
        "project_type": "",
        "main_languages": [],
        "technologies": [],
        "config_files": [],
        "build_files": [],
        "test_directories": [],
        "documentation_files": [],
        "project_root": project_root
    }
    
    root_path = Path(project_root)
    
    # 检测项目名称
    project_info["project_name"] = root_path.name
    
    # 扫描根目录文件
    for file in root_path.iterdir():
        if file.is_file():
            file_name = file.name.lower()
            
            # 识别配置文件
            if file_name in ['package.json', 'pom.xml', 'build.gradle', 'cargo.toml', 'go.mod', 'requirements.txt', 'pyproject.toml']:
                project_info["config_files"].append(file.name)
                
                # 根据配置文件判断项目类型和技术栈
                if file_name == 'package.json':
                    project_info["project_type"] = "JavaScript/Node.js"
                    project_info = _analyze_package_json(file, project_info)
                elif file_name == 'pom.xml':
                    project_info["project_type"] = "Java Maven"
                    project_info = _analyze_pom_xml(file, project_info)
                elif file_name == 'cargo.toml':
                    project_info["project_type"] = "Rust"
                    project_info = _analyze_cargo_toml(file, project_info)
                elif file_name == 'go.mod':
                    project_info["project_type"] = "Go"
                    project_info = _analyze_go_mod(file, project_info)
                elif file_name == 'requirements.txt':
                    project_info["project_type"] = "Python"
                    project_info = _analyze_requirements_txt(file, project_info)
            
            # 识别构建文件
            elif file_name in ['Makefile', 'build.gradle', 'webpack.config.js', 'vite.config.js', 'next.config.js']:
                project_info["build_files"].append(file.name)
            
            # 识别文档文件
            elif file_name in ['readme.md', 'readme.rst', 'readme.txt', 'contributing.md', 'license.md', 'changelog.md']:
                project_info["documentation_files"].append(file.name)
    
    # 扫描目录结构
    project_info = _scan_directory_structure(root_path, project_info)
    
    return project_info

def _analyze_package_json(file_path: Path, project_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析package.json文件"""
    try:
        import json as json_lib
        with open(file_path, 'r', encoding='utf-8') as f:
            package_data = json_lib.load(f)
        
        # 获取主要语言
        if 'dependencies' in package_data:
            for dep, version in package_data['dependencies'].items():
                if any(lib in dep.lower() for lib in ['react', 'vue', 'angular', 'svelte']):
                    project_info["technologies"].append(dep)
        
        # 获取脚本信息
        if 'scripts' in package_data:
            scripts = package_data['scripts']
            if 'start' in scripts:
                project_info["start_command"] = f"npm run {scripts['start']}"
            if 'build' in scripts:
                project_info["build_command"] = f"npm run {scripts['build']}"
            if 'test' in scripts:
                project_info["test_command"] = f"npm run {scripts['test']}"
        
        # 获取项目描述
        if 'description' in package_data:
            project_info["description"] = package_data['description']
            
    except Exception as e:
        print(f"分析package.json时出错: {e}")
    
    return project_info

def _analyze_pom_xml(file_path: Path, project_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析pom.xml文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 提取groupId和artifactId
        groupId_match = re.search(r'<groupId>(.*?)</groupId>', content)
        artifactId_match = re.search(r'<artifactId>(.*?)</artifactId>', content)
        
        if groupId_match and artifactId_match:
            project_info["maven_coords"] = f"{groupId_match.group(1)}:{artifactId_match.group(1)}"
        
        # 查找Spring Boot等框架
        if 'spring-boot' in content.lower():
            project_info["technologies"].append("Spring Boot")
            
    except Exception as e:
        print(f"分析pom.xml时出错: {e}")
    
    return project_info

def _analyze_cargo_toml(file_path: Path, project_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析Cargo.toml文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 查找主要依赖
        dependencies = re.findall(r'([^=\s]+)\s*=\s*["\'][^"\']*["\']', content)
        for dep in dependencies:
            if dep not in project_info["technologies"]:
                project_info["technologies"].append(dep)
                
    except Exception as e:
        print(f"分析Cargo.toml时出错: {e}")
    
    return project_info

def _analyze_go_mod(file_path: Path, project_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析go.mod文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 查找模块名
        module_match = re.search(r'module\s+([^\s]+)', content)
        if module_match:
            project_info["module_name"] = module_match.group(1)
        
        # 查找主要依赖
        deps = re.findall(r'([^\s]+)\s+[^\n]*', content)
        for dep in deps:
            if dep not in project_info["technologies"]:
                project_info["technologies"].append(dep)
                
    except Exception as e:
        print(f"分析go.mod时出错: {e}")
    
    return project_info

def _analyze_requirements_txt(file_path: Path, project_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析requirements.txt文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 提取依赖
        lines = content.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                # 提取包名（去除版本信息）
                package = line.split('==')[0].split('>=')[0].split('<=')[0]
                if package not in project_info["technologies"]:
                    project_info["technologies"].append(package)
                
    except Exception as e:
        print(f"分析requirements.txt时出错: {e}")
    
    return project_info

def _scan_directory_structure(root_path: Path, project_info: Dict[str, Any]) -> Dict[str, Any]:
    """扫描目录结构"""
    common_dirs = ['src', 'lib', 'components', 'views', 'controllers', 'models', 'utils', 'services', 'api', 'config', 'tests', 'test', 'docs', 'bin', 'dist', 'build', 'public', 'static', 'assets']
    
    for dir_name in common_dirs:
        dir_path = root_path / dir_name
        if dir_path.exists() and dir_path.is_dir():
            project_info["directories"] = project_info.get("directories", [])
            project_info["directories"].append(dir_name)
            
            # 检查测试目录
            if dir_name in ['tests', 'test']:
                project_info["test_directories"].append(str(dir_path))
    
    return project_info

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
        result = analyze_project_info(project_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("请提供项目路径作为参数")