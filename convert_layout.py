#!/usr/bin/env python3
"""将左右结构图片转换为上下结构，保持逻辑关系，高端大气风格"""

from PIL import Image, ImageDraw, ImageFont

output_path = "/mnt/e/修改后.png"

# 创建新图片 - 上下结构
img = Image.new('RGB', (800, 1100), color='#0a1628')
draw = ImageDraw.Draw(img)

# 定义颜色
primary_color = '#3b82f6'
secondary_color = '#10b981'
accent_color = '#f59e0b'
text_color = '#ffffff'
muted_color = '#94a3b8'
card_bg = '#1e3a5f'

# 尝试加载字体
try:
    font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
except:
    font_large = ImageFont.load_default()
    font_medium = ImageFont.load_default()
    font_small = ImageFont.load_default()
    font_bold = ImageFont.load_default()

def draw_icon_circle(draw, x, y, size, color, symbol):
    """绘制圆形图标"""
    draw.ellipse([x, y, x+size, y+size], fill=color)
    draw.text((x+size//2, y+size//2), symbol, font=font_bold, fill=text_color, anchor="mm")

# ========== 顶部区域：用户意图识别 ==========
top_y = 50

# 阴影层
draw.rounded_rectangle([55+6, top_y+6, 745+6, top_y+110+6], radius=15, fill=(15, 30, 60))
# 主标题卡片
draw.rounded_rectangle([50, top_y, 745, top_y+110], radius=15, fill=card_bg, outline=primary_color, width=2)

# 标题
draw.text((400, top_y + 30), "用户意图识别 GenAI", font=font_large, fill=text_color, anchor="mm")
draw.text((400, top_y + 70), "USER INTENT RECOGNITION", font=font_small, fill=muted_color, anchor="mm")

# 连接线向下
draw.line([(400, top_y+110), (400, top_y+170)], fill=primary_color, width=3)
draw.polygon([(400, top_y+170), (390, top_y+155), (410, top_y+155)], fill=primary_color)

# ========== 第二层：任务目标和对话风格 ==========
level2_y = top_y + 190

# 左侧阴影
draw.rounded_rectangle([65+5, level2_y+5, 385+5, level2_y+150+5], radius=12, fill=(15, 30, 60))
# 左侧：任务目标
draw.rounded_rectangle([60, level2_y, 380, level2_y+150], radius=12, fill='#0f2942', outline='#3b82f6', width=1)

# 图标
draw_icon_circle(draw, 85, level2_y+25, 50, '#1e40af', "T")
draw.text((235, level2_y+35), "任务目标", font=font_bold, fill=text_color, anchor="mm")
draw.text((235, level2_y+60), "Task Goal", font=font_small, fill=muted_color, anchor="mm")
draw.text((235, level2_y+90), "对话主题", font=font_small, fill='#60a5fa', anchor="mm")
draw.text((235, level2_y+115), "Conversation Topic", font=font_small, fill=muted_color, anchor="mm")

# 中间连接线
draw.line([(380, level2_y+75), (420, level2_y+75)], fill='#4b5563', width=2)

# 右侧阴影
draw.rounded_rectangle([425+5, level2_y+5, 745+5, level2_y+150+5], radius=12, fill=(15, 30, 60))
# 右侧：对话风格
draw.rounded_rectangle([420, level2_y, 740, level2_y+150], radius=12, fill='#0f2942', outline='#10b981', width=1)

# 图标
draw_icon_circle(draw, 445, level2_y+25, 50, '#065f46', "S")
draw.text((595, level2_y+35), "对话风格", font=font_bold, fill=text_color, anchor="mm")
draw.text((595, level2_y+60), "Dialogue Style", font=font_small, fill=muted_color, anchor="mm")
draw.text((595, level2_y+90), "5种类别", font=font_small, fill='#34d399', anchor="mm")
draw.text((595, level2_y+115), "5 Categories", font=font_small, fill=muted_color, anchor="mm")

# ========== 中间连接区域 ==========
connector_y = level2_y + 170

draw.line([(400, level2_y+150), (400, connector_y+20)], fill='#4b5563', width=2)
draw.polygon([(400, connector_y+20), (390, connector_y+5), (410, connector_y+5)], fill='#4b5563')

# ========== 底部区域：模型能力 ==========
bottom_y = connector_y + 40

# 阴影
draw.rounded_rectangle([55+6, bottom_y+6, 745+6, bottom_y+220+6], radius=15, fill=(15, 30, 60))
# 主卡片
draw.rounded_rectangle([50, bottom_y, 740, bottom_y+220], radius=15, fill=card_bg, outline=accent_color, width=2)

# 标题
draw.text((400, bottom_y + 35), "模型能力", font=font_large, fill=text_color, anchor="mm")
draw.text((400, bottom_y + 70), "MODEL CAPABILITIES", font=font_small, fill=muted_color, anchor="mm")

# 工具箱标签
draw.rounded_rectangle([320, bottom_y + 90, 480, bottom_y + 120], radius=8, fill='#92400e')
draw.text((400, bottom_y + 105), "TOOLBOX", font=font_bold, fill='#fbbf24', anchor="mm")

# 两个能力卡片
capability_y = bottom_y + 140

# 左阴影
draw.rounded_rectangle([85+4, capability_y+4, 385+4, capability_y+110+4], radius=10, fill=(15, 30, 60))
# 左侧
draw.rounded_rectangle([80, capability_y, 380, capability_y+110], radius=10, fill='#0f2942', outline='#3b82f6', width=1)

draw_icon_circle(draw, 105, capability_y+20, 50, '#1e40af', "F")
draw.text((265, capability_y+35), "功能工具调用", font=font_bold, fill=text_color, anchor="mm")
draw.text((265, capability_y+60), "Function / Tool Calling", font=font_small, fill=muted_color, anchor="mm")

# 右阴影
draw.rounded_rectangle([420+4, capability_y+4, 715+4, capability_y+110+4], radius=10, fill=(15, 30, 60))
# 右侧
draw.rounded_rectangle([415, capability_y, 710, capability_y+110], radius=10, fill='#0f2942', outline='#10b981', width=1)

draw_icon_circle(draw, 440, capability_y+20, 50, '#065f46', "P")
draw.text((580, capability_y+35), "Prompt质量", font=font_bold, fill=text_color, anchor="mm")
draw.text((580, capability_y+60), "Prompt Quality", font=font_small, fill=muted_color, anchor="mm")

# 中间连接
draw.line([(380, capability_y+55), (415, capability_y+55)], fill='#4b5563', width=2)

# ========== 装饰元素 ==========
# 顶部装饰线
draw.line([(50, 25), (180, 25)], fill=primary_color, width=3)
draw.line([(620, 25), (745, 25)], fill=secondary_color, width=3)

# 底部装饰
draw.line([(50, 1075), (180, 1075)], fill=secondary_color, width=3)
draw.line([(620, 1075), (745, 1075)], fill=accent_color, width=3)

# 角落装饰
draw.rounded_rectangle([30, 15, 48, 33], radius=3, fill=primary_color)
draw.rounded_rectangle([752, 15, 770, 33], radius=3, fill=primary_color)
draw.rounded_rectangle([30, 1067, 48, 1085], radius=3, fill=secondary_color)
draw.rounded_rectangle([752, 1067, 770, 1085], radius=3, fill=accent_color)

# 保存
img.save(output_path, 'PNG', quality=95)
print(f"图片已保存到: {output_path}")