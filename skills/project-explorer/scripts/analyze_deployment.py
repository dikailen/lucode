#!/usr/bin/env python3
"""
部署和运行环境分析脚本
用于分析项目的部署配置和运行要求
"""

import os
import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional

def analyze_deployment(project_root: str) -> Dict[str, Any]:
    """分析项目部署相关信息"""
    deployment_info = {
        "deployment_type": "",
        "build_commands": [],
        "start_commands": [],
        "environment_variables": [],
        "ports": [],
        "services": [],
        "docker_config": {},
        "server_requirements": {},
        "access_urls": [],
        "deployment_scripts": []
    }
    
    root_path = Path(project_root)
    
    # 分析构建配置
    deployment_info = _analyze_build_config(root_path, deployment_info)
    
    # 分析启动配置
    deployment_info = _analyze_start_config(root_path, deployment_info)
    
    # 分析Docker配置
    deployment_info = _analyze_docker_config(root_path, deployment_info)
    
    # 分析环境变量
    deployment_info = _analyze_environment_variables(root_path, deployment_info)
    
    # 分析端口配置
    deployment_info = _analyze_port_config(root_path, deployment_info)
    
    # 分析部署脚本
    deployment_info = _analyze_deployment_scripts(root_path, deployment_info)
    
    # 确定部署类型
    deployment_info = _determine_deployment_type(deployment_info)
    
    return deployment_info

def _analyze_build_config(root_path: Path, deployment_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析构建配置"""
    build_configs = {
        'package.json': _extract_npm_build_scripts,
        'pom.xml': _extract_maven_build_commands,
        'build.gradle': _extract_gradle_build_commands,
        'Cargo.toml': _extract_cargo_build_commands,
        'Makefile': _extract_makefile_commands,
        'requirements.txt': _extract_python_build_commands
    }
    
    for config_file, extractor in build_configs.items():
        file_path = root_path / config_file
        if file_path.exists():
            try:
                commands = extractor(file_path)
                deployment_info["build_commands"].extend(commands)
                deployment_info["build_config_file"] = config_file
            except Exception as e:
                print(f"分析构建配置 {config_file} 时出错: {e}")
    
    return deployment_info

def _extract_npm_build_scripts(file_path: Path) -> List[str]:
    """提取npm构建脚本"""
    commands = []
    try:
        import json
        with open(file_path, 'r', encoding='utf-8') as f:
            package_json = json.load(f)
        
        if 'scripts' in package_json:
            for script_name, command in package_json['scripts'].items():
                if script_name in ['build', 'dist', 'prepare']:
                    commands.append(f"npm run {script_name}")
    except:
        pass
    return commands

def _extract_maven_build_commands(file_path: Path) -> List[str]:
    """提取Maven构建命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if 'mvn' in content:
            commands.extend([
                "mvn clean install",
                "mvn package",
                "mvn deploy"
            ])
    except:
        pass
    return commands

def _extract_gradle_build_commands(file_path: Path) -> List[str]:
    """提取Gradle构建命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if 'gradle' in content:
            commands.extend([
                "gradle build",
                "gradle bootJar",
                "gradle assemble"
            ])
    except:
        pass
    return commands

def _extract_cargo_build_commands(file_path: Path) -> List[str]:
    """提取Cargo构建命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        commands.extend([
            "cargo build",
            "cargo build --release",
            "cargo install"
        ])
    except:
        pass
    return commands

def _extract_makefile_commands(file_path: Path) -> List[str]:
    """提取Makefile命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 查找所有target
        targets = re.findall(r'^(\w+):', content, re.MULTILINE)
        for target in targets:
            commands.append(f"make {target}")
    except:
        pass
    return commands

def _extract_python_build_commands(file_path: Path) -> List[str]:
    """提取Python构建命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if 'pip' in content:
            commands.extend([
                "pip install -r requirements.txt",
                "pip install -e ."
            ])
        
        if 'setup.py' in content:
            commands.extend([
                "python setup.py build",
                "python setup.py install"
            ])
    except:
        pass
    return commands

def _analyze_start_config(root_path: Path, deployment_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析启动配置"""
    start_configs = {
        'package.json': _extract_npm_start_scripts,
        'pom.xml': _extract_maven_start_commands,
        'app.js': _extract_node_start_commands,
        'main.py': _extract_python_start_commands,
        'Dockerfile': _extract_docker_start_commands
    }
    
    for config_file, extractor in start_configs.items():
        file_path = root_path / config_file
        if file_path.exists():
            try:
                commands = extractor(file_path)
                deployment_info["start_commands"].extend(commands)
            except Exception as e:
                print(f"分析启动配置 {config_file} 时出错: {e}")
    
    return deployment_info

def _extract_npm_start_scripts(file_path: Path) -> List[str]:
    """提取npm启动脚本"""
    commands = []
    try:
        import json
        with open(file_path, 'r', encoding='utf-8') as f:
            package_json = json.load(f)
        
        if 'scripts' in package_json:
            if 'start' in package_json['scripts']:
                commands.append(f"npm start")
            if 'dev' in package_json['scripts']:
                commands.append(f"npm run dev")
            if 'run' in package_json['scripts']:
                commands.append(f"npm run")
    except:
        pass
    return commands

def _extract_maven_start_commands(file_path: Path) -> List[str]:
    """提取Maven启动命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if 'spring-boot' in content.lower():
            commands.extend([
                "mvn spring-boot:run",
                "java -jar target/app.jar"
            ])
    except:
        pass
    return commands

def _extract_node_start_commands(file_path: Path) -> List[str]:
    """提取Node.js启动命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if 'node' in content.lower():
            commands.append("node app.js")
            commands.append("node server.js")
    except:
        pass
    return commands

def _extract_python_start_commands(file_path: Path) -> List[str]:
    """提取Python启动命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if 'flask' in content.lower():
            commands.append("python app.py")
        elif 'django' in content.lower():
            commands.append("python manage.py runserver")
        else:
            commands.append("python main.py")
    except:
        pass
    return commands

def _extract_docker_start_commands(file_path: Path) -> List[str]:
    """提取Docker启动命令"""
    commands = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 查找EXPOSE指令
        ports = re.findall(r'EXPOSE\s+(\d+)', content, re.IGNORECASE)
        for port in ports:
            commands.append(f"docker run -p {port}:{port} your-image-name")
    except:
        pass
    return commands

def _analyze_docker_config(root_path: Path, deployment_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析Docker配置"""
    dockerfile_path = root_path / "Dockerfile"
    docker_compose_path = root_path / "docker-compose.yml"
    docker_compose_yaml_path = root_path / "docker-compose.yaml"
    
    if dockerfile_path.exists():
        try:
            with open(dockerfile_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            deployment_info["docker_config"]["dockerfile_present"] = True
            deployment_info["docker_config"]["base_image"] = _extract_docker_base_image(content)
            deployment_info["docker_config"]["exposed_ports"] = _extract_docker_ports(content)
            deployment_info["docker_config"]["environment_vars"] = _extract_docker_env_vars(content)
            
        except Exception as e:
            print(f"分析Dockerfile时出错: {e}")
    
    if docker_compose_path.exists():
        deployment_info["docker_config"]["docker_compose_present"] = True
        deployment_info["docker_config"]["compose_file"] = str(docker_compose_path)
    
    if docker_compose_yaml_path.exists():
        deployment_info["docker_config"]["docker_compose_present"] = True
        deployment_info["docker_config"]["compose_file"] = str(docker_compose_yaml_path)
    
    return deployment_info

def _extract_docker_base_image(content: str) -> str:
    """提取Docker基础镜像"""
    match = re.search(r'FROM\s+([^\s]+)', content, re.IGNORECASE)
    return match.group(1) if match else "unknown"

def _extract_docker_ports(content: str) -> List[str]:
    """提取Docker暴露的端口"""
    ports = re.findall(r'EXPOSE\s+(\d+)', content, re.IGNORECASE)
    return ports

def _extract_docker_env_vars(content: str) -> List[str]:
    """提取Docker环境变量"""
    env_vars = re.findall(r'ENV\s+(\w+)\s+([^\s]+)', content, re.IGNORECASE)
    return [f"{var}={value}" for var, value in env_vars]

def _analyze_environment_variables(root_path: Path, deployment_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析环境变量配置"""
    env_files = ['.env', 'environment', '.env.example', 'config.env']
    
    for env_file in env_files:
        file_path = root_path / env_file
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 提取环境变量
                env_vars = re.findall(r'(\w+)\s*=\s*([^\n]+)', content)
                deployment_info["environment_variables"].extend([f"{var}={value}" for var, value in env_vars])
                
            except Exception as e:
                print(f"分析环境变量文件 {env_file} 时出错: {e}")
    
    return deployment_info

def _analyze_port_config(root_path: Path, deployment_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析端口配置"""
    port_configs = {
        'package.json': _extract_node_ports,
        'app.js': _extract_node_ports,
        'server.js': _extract_node_ports,
        'main.py': _extract_python_ports,
        'app.py': _extract_python_ports,
        'Dockerfile': _extract_docker_ports
    }
    
    for config_file, extractor in port_configs.items():
        file_path = root_path / config_file
        if file_path.exists():
            try:
                ports = extractor(file_path)
                deployment_info["ports"].extend(ports)
            except Exception as e:
                print(f"分析端口配置 {config_file} 时出错: {e}")
    
    # 默认端口
    if not deployment_info["ports"]:
        deployment_info["ports"].extend(["3000", "8080", "5000", "8000"])
    
    return deployment_info

def _extract_node_ports(file_path: Path) -> List[str]:
    """提取Node.js端口配置"""
    ports = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        port_matches = re.findall(r'port\s*=\s*(\d+)|PORT\s*=\s*(\d+)|: (\d+)', content, re.IGNORECASE)
        for match in port_matches:
            for port in match:
                if port.isdigit():
                    ports.append(port)
                    break
    except:
        pass
    return ports

def _extract_python_ports(file_path: Path) -> List[str]:
    """提取Python端口配置"""
    ports = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        port_matches = re.findall(r'port\s*=\s*(\d+)|PORT\s*=\s*(\d+)|: (\d+)', content, re.IGNORECASE)
        for match in port_matches:
            for port in match:
                if port.isdigit():
                    ports.append(port)
                    break
    except:
        pass
    return ports

def _analyze_deployment_scripts(root_path: Path, deployment_info: Dict[str, Any]) -> Dict[str, Any]:
    """分析部署脚本"""
    script_patterns = ['deploy.sh', 'deploy.py', 'deploy.rb', 'deployment.js', 'setup.sh', 'install.sh']
    
    for pattern in script_patterns:
        file_path = root_path / pattern
        if file_path.exists():
            deployment_info["deployment_scripts"].append(str(file_path))
    
    return deployment_info

def _determine_deployment_type(deployment_info: Dict[str, Any]) -> Dict[str, Any]:
    """确定部署类型"""
    has_docker = deployment_info["docker_config"].get("dockerfile_present", False)
    has_package_json = any("package.json" in cmd for cmd in deployment_info["build_commands"])
    has_pom_xml = any("mvn" in cmd for cmd in deployment_info["build_commands"])
    
    if has_docker:
        deployment_info["deployment_type"] = "Docker Container"
        deployment_info["access_urls"].append("http://localhost:8080")
    elif has_package_json:
        deployment_info["deployment_type"] = "Node.js Application"
        deployment_info["access_urls"].append("http://localhost:3000")
    elif has_pom_xml:
        deployment_info["deployment_type"] = "Java Application"
        deployment_info["access_urls"].append("http://localhost:8080")
    else:
        deployment_info["deployment_type"] = "Standalone Application"
        deployment_info["access_urls"].append("http://localhost:5000")
    
    return deployment_info

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
        result = analyze_deployment(project_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("请提供项目路径作为参数")