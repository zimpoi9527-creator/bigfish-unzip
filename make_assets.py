# 生成主题素材：整窗壁纸（插画 + 毛玻璃面板）+ 程序图标
# 插画素材来源：ZipZipPipe（用户提供），版权归原作者所有
from PIL import Image, ImageDraw, ImageFilter, ImageChops
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, 'art')
OUT = os.path.join(HERE, 'assets')
os.makedirs(OUT, exist_ok=True)

W, H = 1040, 620
DROP_BOX = (12, 12, 366, 292)
LEFT_BOX = (12, 300, 366, 604)
RIGHT_BOX = (376, 12, 1028, 608)


def cover(img, w, h, anchor_x=0.5, anchor_y=0.5):
    """等比缩放并裁剪，填满目标尺寸。"""
    scale = max(w / img.width, h / img.height)
    nw, nh = int(img.width * scale + 0.5), int(img.height * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = int((nw - w) * anchor_x)
    top = int((nh - h) * anchor_y)
    return img.crop((left, top, left + w, top + h))


# ---------------------------------------------------------------- 壁纸
# 1) 天空渐变底
base = Image.new('RGB', (W, H), (255, 255, 255))
d = ImageDraw.Draw(base)
for y in range(H):
    t = y / (H - 1)
    top, bot = (110, 182, 238), (226, 245, 255)
    d.line([(0, y), (W, y)],
           fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))

glow = Image.new('RGB', (W, H), (0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse([-220, -260, 520, 320], fill=(255, 255, 255))
gd.ellipse([620, -300, 1300, 260], fill=(190, 232, 255))
gd.ellipse([-300, 420, 560, 900], fill=(214, 244, 255))
glow = glow.filter(ImageFilter.GaussianBlur(90))
base = ImageChops.add(base, glow.point(lambda v: int(v * 0.18)))
base = base.convert('RGBA')

# 2) 人物：高清透明立绘，放在投放区右侧（原阿罗娜位置）
char = Image.open(os.path.join(ART, 'char_hd.png')).convert('RGBA')
char = char.crop((0, 0, char.width, int(char.height * 0.72)))    # 头到膝
target_h = 300
ratio = target_h / char.height
char = char.resize((max(1, int(char.width * ratio)), target_h), Image.LANCZOS)

fmask = Image.new('L', char.size, 255)
md = ImageDraw.Draw(fmask)
fade = 64
for i in range(fade):
    y = char.height - fade + i
    md.line([(0, y), (char.width, y)], fill=int(255 * (1 - i / fade)))
char.putalpha(ImageChops.multiply(char.getchannel('A'), fmask))

base.alpha_composite(char, (DROP_BOX[0] + 6, 2))

# 3) 毛玻璃面板
blur = base.filter(ImageFilter.GaussianBlur(24))
white = Image.new('RGBA', (W, H), (255, 255, 255, 255))
for box, alpha in ((LEFT_BOX, 0.26), (RIGHT_BOX, 0.42)):
    mask = Image.new('L', (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=20, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(14))
    base = Image.composite(Image.blend(blur, white, alpha), base, mask)

edge = Image.new('RGBA', (W, H), (0, 0, 0, 0))
ed = ImageDraw.Draw(edge)
for box in (LEFT_BOX, RIGHT_BOX):
    ed.rounded_rectangle(box, radius=20, outline=(255, 255, 255, 140), width=2)
base = Image.alpha_composite(base, edge.filter(ImageFilter.GaussianBlur(1.2)))

# 4) 投放区：虚线边框 + 底部文字底衬
def dashed_round_rect(draw, box, radius, color, width=3, dash=16, gap=11):
    import math
    x0, y0, x1, y1 = box
    path = []
    def arc(cx, cy, a0, a1, steps=14):
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            path.append((cx + math.cos(a) * radius, cy + math.sin(a) * radius))
    path.append((x0 + radius, y0))
    path.append((x1 - radius, y0)); arc(x1 - radius, y0 + radius, -90, 0)
    path.append((x1, y1 - radius)); arc(x1 - radius, y1 - radius, 0, 90)
    path.append((x0 + radius, y1)); arc(x0 + radius, y1 - radius, 90, 180)
    path.append((x0, y0 + radius)); arc(x0 + radius, y0 + radius, 180, 270)

    prev = path[0]
    on, acc = True, 0.0
    for cur in path[1:]:
        seg = math.dist(prev, cur)
        if seg <= 0:
            continue
        t = 0.0
        while t < seg:
            remain = (dash if on else gap) - acc
            step = min(remain, seg - t)
            x_prev = prev[0] + (cur[0] - prev[0]) * (t / seg)
            y_prev = prev[1] + (cur[1] - prev[1]) * (t / seg)
            x_next = prev[0] + (cur[0] - prev[0]) * ((t + step) / seg)
            y_next = prev[1] + (cur[1] - prev[1]) * ((t + step) / seg)
            if on:
                draw.line([(x_prev, y_prev), (x_next, y_next)], fill=color, width=width)
            t += step
            acc += step
            if acc >= (dash if on else gap):
                acc = 0.0
                on = not on
        prev = cur


dl = Image.new('RGBA', (W, H), (0, 0, 0, 0))
dd = ImageDraw.Draw(dl)
dd.rounded_rectangle([DROP_BOX[0] - 3, DROP_BOX[1] - 3, DROP_BOX[2] + 3, DROP_BOX[3] + 3],
                     radius=22, outline=(255, 255, 255, 90), width=8)
base = Image.alpha_composite(base, dl.filter(ImageFilter.GaussianBlur(6)))

dl2 = Image.new('RGBA', (W, H), (0, 0, 0, 0))
dashed_round_rect(ImageDraw.Draw(dl2), DROP_BOX, 20, (255, 255, 255, 240), width=3)
base = Image.alpha_composite(base, dl2)


base.convert('RGB').save(os.path.join(OUT, 'wallpaper.png'), optimize=True)
print('wallpaper.png', base.size)

# ---------------------------------------------------------------- 图标（取正面立绘头部）
sheet = Image.open(os.path.join(ART, 'char_hd.png')).convert('RGBA')
head = sheet.crop((300, 70, 790, 560)).resize((512, 512), Image.LANCZOS)
pad = Image.new('RGBA', head.size, (255, 255, 255, 255))
head = Image.alpha_composite(pad, head)

S = 512
icon = Image.new('RGBA', (S, S), (0, 0, 0, 0))
mask = Image.new('L', (S, S), 0)
ImageDraw.Draw(mask).ellipse([6, 6, S - 6, S - 6], fill=255)
icon.paste(head, (0, 0), mask)

ring = Image.new('RGBA', (S, S), (0, 0, 0, 0))
ImageDraw.Draw(ring).ellipse([6, 6, S - 6, S - 6],
                             outline=(255, 255, 255, 255), width=22)
icon = Image.alpha_composite(icon, ring)

edge = Image.new('RGBA', (S, S), (0, 0, 0, 0))
ImageDraw.Draw(edge).ellipse([6, 6, S - 6, S - 6],
                             outline=(62, 151, 221, 255), width=10)
icon = Image.alpha_composite(icon, edge)

icon.resize((256, 256), Image.LANCZOS).save(
    os.path.join(OUT, 'icon.ico'), format='ICO',
    sizes=[(s, s) for s in (256, 128, 64, 48, 32, 16)])
icon.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, 'icon.png'))
print('icon.ico / icon.png')

json.dump({'W': W, 'H': H, 'drop': DROP_BOX, 'left': LEFT_BOX, 'right': RIGHT_BOX},
          open(os.path.join(OUT, 'layout.json'), 'w'), indent=1)
print('done')
