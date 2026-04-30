#!/usr/bin/env python3
"""
项目结构可视化图表生成脚本
用于生成各种类型的图表来可视化项目结构
"""

import os
import json
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import ConnectionPatch
import networkx as nx
import numpy as np
from pathlib import Path
from typing import Dict, List, Any, Optional
import base64
from io import BytesIO

class ProjectVisualizer:
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.project_info = None
        
    def generate_all_diagrams(self, output_dir: str = "diagrams") -> Dict[str, str]:
        """生成所有类型的图表"""
        diagrams = {}
        
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        
        # 生成目录树图
        tree_diagram = self.generate_directory_tree_diagram(output_dir)
        if tree_diagram:
            diagrams["directory_tree"] = tree_diagram
            
        # 生成技术栈饼图
        tech_stack_diagram = self.generate_tech_stack_pie_chart(output_dir)
        if tech_stack_diagram:
            diagrams["tech_stack"] = tech_stack_diagram
            
        # 生成架构图
        architecture_diagram = self.generate_architecture_diagram(output_dir)
        if architecture_diagram:
            diagrams["architecture"] = architecture_diagram
            
        # 生成文件类型分布图
        file_type_diagram = self.generate_file_type_distribution(output_dir)
        if file_type_diagram:
            diagrams["file_types"] = file_type_diagram
            
        return diagrams
    
    def generate_directory_tree_diagram(self, output_dir: str) -> Optional[str]:
        """生成目录树可视化图"""
        try:
            # 创建图形
            fig, ax = plt.subplots(1, 1, figsize=(16, 12))
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 10)
            ax.axis('off')
            
            # 绘制根目录
            self._draw_node(ax, 5, 9, "项目根目录", 'project', color='#FF6B6B')
            
            # 扫描一级目录
            directories = []
            for item in self.project_root.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    directories.append(item.name)
            
            # 绘制一级目录节点
            x_positions = np.linspace(1, 9, len(directories))
            for i, dir_name in enumerate(directories):
                self._draw_node(ax, x_positions[i], 7, dir_name, 'directory', color='#4ECDC4')
                # 连接线
                self._draw_connection(ax, 5, 8.5, x_positions[i], 7.5)
                
                # 绘制二级目录示例
                if i < 3:  # 只显示前三个目录的子目录作为示例
                    subdirs = self._get_subdirectories(self.project_root / dir_name)
                    if subdirs:
                        subdir_x = x_positions[i]
                        for j, subdir in enumerate(subdirs[:2]):  # 每个目录最多显示2个子目录
                            subdir_y = 5 - j * 0.8
                            self._draw_node(ax, subdir_x, subdir_y, subdir, 'subdirectory', color='#45B7D1')
                            self._draw_connection(ax, subdir_x, 6.5, subdir_x, subdir_y + 0.3)
            
            plt.title('项目目录结构图', fontsize=16, fontweight='bold', pad=20)
            
            # 保存图片
            output_path = os.path.join(output_dir, "directory_tree.png")
            plt.tight_layout()
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close()
            
            return output_path
            
        except Exception as e:
            print(f"生成目录树图时出错: {e}")
            return None
    
    def generate_tech_stack_pie_chart(self, output_dir: str) -> Optional[str]:
        """生成技术栈饼图"""
        try:
            # 分析项目技术栈
            tech_stack = self._analyze_tech_stack()
            
            if not tech_stack:
                return None
            
            # 创建图形
            fig, ax = plt.subplots(1, 1, figsize=(12, 8))
            
            # 准备数据
            labels = list(tech_stack.keys())
            sizes = list(tech_stack.values())
            colors = plt.cm.Set3(np.linspace(0, 1, len(labels)))
            
            # 绘制饼图
            wedges, texts, autotexts = ax.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', 
                                              startangle=90, textprops={'fontsize': 10})
            
            # 设置标题
            ax.set_title('技术栈分布', fontsize=16, fontweight='bold')
            
            # 保存图片
            output_path = os.path.join(output_dir, "tech_stack_pie.png")
            plt.tight_layout()
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close()
            
            return output_path
            
        except Exception as e:
            print(f"生成技术栈饼图时出错: {e}")
            return None
    
    def generate_architecture_diagram(self, output_dir: str) -> Optional[str]:
        """生成架构图"""
        try:
            # 创建图形
            fig, ax = plt.subplots(1, 1, figsize=(14, 10))
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 10)
            ax.axis('off')
            
            # 绘制前端层
            frontend_box = patches.Rectangle((1, 7), 3, 1.5, linewidth=2, 
                                           edgecolor='#FF6B6B', facecolor='#FFE5E5', label='前端层')
            ax.add_patch(frontend_box)
            ax.text(2.5, 7.75, '前端层 (Frontend)', ha='center', va='center', fontweight='bold')
            
            # 绘制API层
            api_box = patches.Rectangle((1, 4.5), 3, 1.5, linewidth=2, 
                                     edgecolor='#4ECDC4', facecolor='#E5F9F6', label='API层')
            ax.add_patch(api_box)
            ax.text(2.5, 5.25, 'API层 (REST API)', ha='center', va='center', fontweight='bold')
            
            # 绘制业务逻辑层
            business_box = patches.Rectangle((6, 7), 3, 1.5, linewidth=2, 
                                           edgecolor='#45B7D1', facecolor='#E5F4F8', label='业务逻辑层')
            ax.add_patch(business_box)
            ax.text(7.5, 7.75, '业务逻辑层 (Business Logic)', ha='center', va='center', fontweight='bold')
            
            # 绘制数据访问层
            data_box = patches.Rectangle((6, 4.5), 3, 1.5, linewidth=2, 
                                       edgecolor='#96CEB4', facecolor='#E8F8F5', label='数据访问层')
            ax.add_patch(data_box)
            ax.text(7.5, 5.25, '数据访问层 (Data Access)', ha='center', va='center', fontweight='bold')
            
            # 绘制数据库层
            db_box = patches.Rectangle((6, 2), 3, 1.5, linewidth=2, 
                                     edgecolor='#FFEAA7', facecolor='#FFF9E6', label='数据库层')
            ax.add_patch(db_box)
            ax.text(7.5, 2.75, '数据库层 (Database)', ha='center', va='center', fontweight='bold')
            
            # 绘制连接线
            self._draw_connection(ax, 4, 7.75, 6, 7.75)  # 前端到业务逻辑
            self._draw_connection(ax, 2.5, 7, 2.5, 6)  # 前端到API
            self._draw_connection(ax, 2.5, 4.5, 6, 5.25)  # API到数据访问
            self._draw_connection(ax, 7.5, 4.5, 7.5, 3.5)  # 数据访问到数据库
            
            # 添加示例组件
            self._add_component_examples(ax)
            
            plt.title('项目架构图', fontsize=16, fontweight='bold', pad=20)
            
            # 保存图片
            output_path = os.path.join(output_dir, "architecture_diagram.png")
            plt.tight_layout()
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close()
            
            return output_path
            
        except Exception as e:
            print(f"生成架构图时出错: {e}")
            return None
    
    def generate_file_type_distribution(self, output_dir: str) -> Optional[str]:
        """生成文件类型分布图"""
        try:
            # 统计文件类型
            file_types = self._count_file_types()
            
            if not file_types:
                return None
            
            # 创建图形
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
            
            # 柱状图
            files = list(file_types.keys())
            counts = list(file_types.values())
            colors = plt.cm.viridis(np.linspace(0, 1, len(files)))
            
            bars = ax1.bar(files, counts, color=colors)
            ax1.set_xlabel('文件类型')
            ax1.set_ylabel('数量')
            ax1.set_title('文件类型分布')
            
            # 在柱状图上显示数值
            for bar, count in zip(bars, counts):
                height = bar.get_height()
                ax1.text(bar.get_x() + bar.get_width()/2., height,
                       f'{count}', ha='center', va='bottom')
            
            # 饼图
            ax2.pie(counts, labels=files, autopct='%1.1f%%', startangle=90)
            ax2.set_title('文件类型占比')
            
            # 保存图片
            output_path = os.path.join(output_dir, "file_type_distribution.png")
            plt.tight_layout()
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close()
            
            return output_path
            
        except Exception as e:
            print(f"生成文件类型分布图时出错: {e}")
            return None
    
    def _draw_node(self, ax, x, y, text, node_type, color='#CCCCCC'):
        """绘制节点"""
        if node_type == 'project':
            circle = plt.Circle((x, y), 0.3, color=color, ec='black', linewidth=2)
            ax.add_patch(circle)
        else:
            rect = patches.Rectangle((x-0.4, y-0.2), 0.8, 0.4, linewidth=1, 
                                  edgecolor='black', facecolor=color)
            ax.add_patch(rect)
        
        ax.text(x, y, text, ha='center', va='center', fontsize=8, fontweight='bold')
    
    def _draw_connection(self, ax, x1, y1, x2, y2):
        """绘制连接线"""
        connection = ConnectionPatch((x1, y1), (x2, y2), "data", "data",
                                  arrowstyle="-|>", shrinkA=5, shrinkB=5,
                                  mutation_scale=20, fc="black", ec="black")
        ax.add_patch(connection)
    
    def _get_subdirectories(self, directory: Path) -> List[str]:
        """获取子目录列表"""
        subdirs = []
        try:
            for item in directory.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    subdirs.append(item.name)
        except PermissionError:
            pass
        return subdirs[:3]  # 最多返回3个子目录
    
    def _analyze_tech_stack(self) -> Dict[str, int]:
        """分析技术栈"""
        tech_stack = {}
        
        # 检查package.json
        package_json = self.project_root / "package.json"
        if package_json.exists():
            tech_stack["JavaScript/Node.js"] = 30
        
        # 检查Python相关文件
        if (self.project_root / "requirements.txt").exists():
            tech_stack["Python"] = 25
        elif (self.project_root / "pyproject.toml").exists():
            tech_stack["Python"] = 25
        
        # 检查Java相关文件
        if (self.project_root / "pom.xml").exists():
            tech_stack["Java"] = 20
        elif (self.project_root / "build.gradle").exists():
            tech_stack["Java"] = 20
        
        # 检查React相关
        if (self.project_root / "src").exists():
            src_dir = self.project_root / "src"
            for item in src_dir.rglob("*.jsx"):
                tech_stack["React"] = 15
                break
        
        # 检查Docker
        if (self.project_root / "Dockerfile").exists():
            tech_stack["Docker"] = 10
        
        return tech_stack
    
    def _count_file_types(self) -> Dict[str, int]:
        """统计文件类型"""
        file_types = {}
        
        for item in self.project_root.rglob("*"):
            if item.is_file() and not item.name.startswith('.'):
                ext = item.suffix.lower()
                file_type = ext[1:] if ext else "无扩展名"
                file_types[file_type] = file_types.get(file_type, 0) + 1
        
        return dict(sorted(file_types.items(), key=lambda x: x[1], reverse=True))
    
    def _add_component_examples(self, ax):
        """添加组件示例"""
        examples = {
            '前端层': ['React组件', 'Vue组件', '页面模板'],
            'API层': ['REST接口', 'GraphQL', 'WebSocket'],
            '业务逻辑': ['用户服务', '订单处理', '支付逻辑'],
            '数据访问': ['ORM', 'SQL查询', '缓存'],
            '数据库': ['MySQL', 'PostgreSQL', 'MongoDB']
        }
        
        y_pos = 1.5
        for layer, components in examples.items():
            text = f"{layer}: {', '.join(components)}"
            ax.text(0.5, y_pos, text, fontsize=9, ha='left', va='center')
            y_pos -= 0.3
    
    def generate_base64_image(self, image_path: str) -> Optional[str]:
        """将图片转换为base64编码"""
        try:
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode()
        except Exception as e:
            print(f"转换图片为base64时出错: {e}")
            return None

def main():
    import sys
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
        output_dir = sys.argv[2] if len(sys.argv) > 2 else "diagrams"
        
        visualizer = ProjectVisualizer(project_path)
        diagrams = visualizer.generate_all_diagrams(output_dir)
        
        print("生成的图表:")
        for name, path in diagrams.items():
            print(f"  {name}: {path}")
            
            # 输出base64编码
            base64_image = visualizer.generate_base64_image(path)
            if base64_image:
                print(f"  Base64: {base64_image[:100]}...")
    else:
        print("请提供项目路径作为参数")

if __name__ == "__main__":
    main()