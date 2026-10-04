"""Contact sheets from rive-seq captures: transparent frames composited on white."""
import os, re, glob, sys
from PIL import Image, ImageDraw
d = sys.argv[1]; prefix = sys.argv[2]; S = 110; COLS = 12
names = sorted({re.match(r"(.+?)-\d{3}-\d+\.(png|jpg)", f).group(1) for f in os.listdir(d)})
for n in names:
    fs = sorted(sorted(glob.glob(f"{d}/{n}-*.png") + glob.glob(f"{d}/{n}-*.jpg"))); rows = (len(fs) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * S, rows * (S + 12)), "white"); dr = ImageDraw.Draw(sheet)
    for i, f in enumerate(fs):
        t = re.search(r"-(\d+)\.(png|jpg)$", f).group(1); im = Image.open(f).convert("RGBA").resize((S, S)); bg = Image.new("RGBA", im.size, "white"); bg.alpha_composite(im)
        x = (i % COLS) * S; y = (i // COLS) * (S + 12); sheet.paste(bg.convert("RGB"), (x, y + 12)); dr.text((x + 2, y), f"{int(t) / 1000:.2f}", fill="black")
    sheet.save(f"{prefix}-{n}.png")
print("sheets:", names)
