"""Render the existing blue T brand as a multi-resolution Windows icon."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parent
image = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((12, 12, 1012, 1012), radius=278, fill='#2169ff')
draw.rounded_rectangle((291, 252, 733, 365), radius=14, fill='white')
draw.rounded_rectangle((453, 332, 571, 777), radius=14, fill='white')
image.save(root / 'app.ico', format='ICO', sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)])
