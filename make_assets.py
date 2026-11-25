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

# 2) 插画：整窗铺满（镜像让人物落在左侧投放区）
art = Image.open(os.path.join(ART, 'hd_water.png')).convert('RGB')
art = art.transpose(Image.FLIP_LEFT_RIGHT)
base = cover(art, W, H, anchor_x=0.5, anchor_y=0.45).convert('RGBA')

# 3) 毛玻璃面板
blur = base.filter(ImageFilter.GaussianBlur(24))
white = Image.new('RGBA', (W, H), (255, 255, 255, 255))
for box, alpha in ((LEFT_BOX, 0.26), (RIGHT_BOX, 0.42)):
    mask = Image.new('L', (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=20, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(14))
    frosted = Image.blend(blur, white, alpha)
    base = Image.composite(frosted, base, mask)

edge = Image.new('RGBA', (W, H), (0, 0, 0, 0))
ed = ImageDraw.Draw(edge)
for box in (LEFT_BOX, RIGHT_BOX):
    ed.rounded_rectangle(box, radius=20, outline=(255, 255, 255, 140), width=2)
base = Image.alpha_composite(base, edge.filter(ImageFilter.GaussianBlur(1.2)))

# 4) 投放区文字底衬（让"拖到这里"在人物上也能看清）
plate = Image.new('RGBA', (W, H), (0, 0, 0, 0))
pl = ImageDraw.Draw(plate)
pl.rounded_rectangle([150, 198, 364, 288], radius=18, fill=(255, 255, 255, 172))
plate = plate.filter(ImageFilter.GaussianBlur(7))
base = Image.alpha_composite(base, plate)

base.convert('RGB').save(os.path.join(OUT, 'wallpaper.png'), optimize=True)
print('wallpaper.png', base.size)

# ---------------------------------------------------------------- 图标（取正面立绘头部）
sheet = Image.open(os.path.join(ART, 'hd_sheet.png')).convert('RGB')
head = sheet.crop((250, 20, 640, 410)).resize((512, 512), Image.LANCZOS).convert('RGBA')

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
