"""生成应用图标: 蓝白渐变圆角方块 + Telegram 风格纸飞机"""
import os
from PIL import Image, ImageDraw, ImageFilter

SIZE = 512


def _gradient_bg(size):
    """从上到下的蓝色渐变背景"""
    img = Image.new("RGB", (size, size))
    top = (0, 136, 204)       # #0088cc
    bottom = (34, 158, 217)   # #229ED9
    for y in range(size):
        t = y / size
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            img.putpixel((x, y), (r, g, b))
    return img


def _rounded(img, radius):
    """圆角遮罩"""
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, img.width, img.height), radius=radius, fill=255)
    out = Image.new("RGB", img.size, (0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _paper_plane(size):
    """白色纸飞机"""
    w = size
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 主体大三角: 机头朝右
    body = [
        (w * 0.52, w * 0.30),   # 上缘起点
        (w * 0.93, w * 0.50),   # 机头(右下)
        (w * 0.50, w * 0.66),   # 下缘后部
    ]
    # 尾翼: 向左翘起的小三角
    tail = [
        (w * 0.16, w * 0.26),   # 尾翼上端
        (w * 0.52, w * 0.45),   # 与主体相接点(上)
        (w * 0.52, w * 0.55),   # 与主体相接点(下)
        (w * 0.22, w * 0.80),   # 尾翼下端
    ]
    d.polygon(tail, fill=(255, 255, 255, 255))
    d.polygon(body, fill=(255, 255, 255, 255))
    return img


def main():
    # 底图 + 圆角
    base = _gradient_bg(SIZE)
    base = _rounded(base, radius=int(SIZE * 0.22))
    base = base.convert("RGBA")

    # 纸飞机
    plane = _paper_plane(SIZE)
    out = Image.alpha_composite(base, plane)

    # 轻微阴影增强立体感
    out = out.filter(ImageFilter.SMOOTH_MORE)

    # 输出 PNG 预览 + 多尺寸 ICO
    out.save("icon.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    out.save("icon.ico", sizes=sizes)
    print("icon.png / icon.ico 已生成")


if __name__ == "__main__":
    main()
