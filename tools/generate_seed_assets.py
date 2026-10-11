"""Generates 16 rich, scenic, painterly travel photos and 3 coastline backdrops
using layered gradients, atmospheric perspective, sunset lighting, horizons, and silhouettes.
No flat color boxes with circles.
"""
import math
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

BASE_DIR = Path(__file__).resolve().parent.parent
PHOTOS_DIR = BASE_DIR / "frontend" / "public" / "seed" / "photos"
BACKDROPS_DIR = BASE_DIR / "frontend" / "public" / "seed" / "backdrops"

PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
BACKDROPS_DIR.mkdir(parents=True, exist_ok=True)

def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

def draw_gradient_vertical(draw, width, height, stops):
    for y in range(height):
        t = y / max(1, height - 1)
        for i in range(len(stops) - 1):
            y0, c0 = stops[i]
            y1, c1 = stops[i + 1]
            if y0 <= t <= y1:
                factor = (t - y0) / max(0.0001, y1 - y0)
                color = lerp_color(c0, c1, factor)
                draw.line([(0, y), (width, y)], fill=color)
                break

def generate_coastal_scenery(width, height, theme="golden_hour", title="Coastline"):
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    if theme == "golden_hour":
        sky_stops = [
            (0.0, (28, 75, 143)),    # Sea blue
            (0.35, (70, 130, 160)),  # Sky
            (0.65, (230, 140, 70)),  # Warm amber
            (0.8, (245, 190, 110)),  # Golden sun glow
            (1.0, (235, 120, 60)),   # Sunset horizon
        ]
        sea_stops = [
            (0.0, (220, 130, 70)),   # Sun reflection
            (0.3, (30, 127, 133)),   # Lagoon teal
            (0.7, (25, 90, 110)),    # Deep teal
            (1.0, (18, 48, 60)),     # Dark sea
        ]
        sun_color = (255, 245, 200)
        has_sun = True
    elif theme == "azure_cove":
        sky_stops = [
            (0.0, (30, 127, 133)),
            (0.5, (141, 201, 223)),
            (1.0, (220, 240, 245)),
        ]
        sea_stops = [
            (0.0, (141, 201, 223)),
            (0.4, (30, 127, 133)),
            (1.0, (25, 75, 140)),
        ]
        sun_color = (255, 255, 230)
        has_sun = False
    elif theme == "twilight_harbor":
        sky_stops = [
            (0.0, (15, 25, 45)),
            (0.4, (50, 45, 85)),
            (0.7, (180, 85, 100)),
            (1.0, (240, 140, 90)),
        ]
        sea_stops = [
            (0.0, (210, 110, 90)),
            (0.4, (35, 45, 75)),
            (1.0, (12, 20, 35)),
        ]
        sun_color = (255, 220, 160)
        has_sun = True
    else: # lush_backwater
        sky_stops = [
            (0.0, (75, 135, 165)),
            (0.6, (160, 210, 220)),
            (1.0, (230, 240, 225)),
        ]
        sea_stops = [
            (0.0, (120, 180, 150)),
            (0.5, (45, 110, 90)),
            (1.0, (25, 65, 55)),
        ]
        sun_color = (255, 245, 210)
        has_sun = False

    horizon_y = int(height * 0.58)

    # 1. Sky
    sky_img = Image.new("RGB", (width, horizon_y))
    sky_draw = ImageDraw.Draw(sky_img)
    draw_gradient_vertical(sky_draw, width, horizon_y, sky_stops)
    img.paste(sky_img, (0, 0))

    # Sun
    if has_sun:
        sun_x = int(width * 0.62)
        sun_y = int(horizon_y * 0.78)
        sun_r = int(min(width, height) * 0.08)
        # Glow
        for gr in range(sun_r * 3, sun_r, -4):
            alpha = int(40 * (1 - (gr - sun_r) / (sun_r * 2)))
            draw.ellipse([sun_x - gr, sun_y - gr, sun_x + gr, sun_y + gr], fill=(255, 220, 150))
        draw.ellipse([sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r], fill=sun_color)

    # 2. Distant Hills / Headland
    hill_color = (40, 75, 95) if theme != "twilight_harbor" else (30, 25, 50)
    hill_pts = [(0, horizon_y)]
    for x in range(0, width + 40, 40):
        hill_h = math.sin(x * 0.005) * 35 + math.sin(x * 0.015) * 15 + 20
        hill_pts.append((x, int(horizon_y - hill_h)))
    hill_pts.extend([(width, horizon_y), (0, horizon_y)])
    draw.polygon(hill_pts, fill=hill_color)

    # 3. Sea / Water
    sea_h = height - horizon_y
    sea_img = Image.new("RGB", (width, sea_h))
    sea_draw = ImageDraw.Draw(sea_img)
    draw_gradient_vertical(sea_draw, width, sea_h, sea_stops)
    img.paste(sea_img, (0, horizon_y))

    # Water shimmer / sun reflections
    if has_sun:
        for ry in range(horizon_y, height, 4):
            spread = int((ry - horizon_y) * 0.45) + 12
            rx0 = max(0, sun_x - spread + random.randint(-10, 10))
            rx1 = min(width, sun_x + spread + random.randint(-10, 10))
            t_fade = 1.0 - (ry - horizon_y) / sea_h
            if random.random() < 0.65:
                draw.line([(rx0, ry), (rx1, ry)], fill=(255, 235, 180), width=1)

    # 4. Foreground Silhouettes: Rocks & Palm Trees
    # Shore rocks on bottom left
    rock_pts = [
        (0, height),
        (0, int(height * 0.82)),
        (int(width * 0.12), int(height * 0.85)),
        (int(width * 0.22), int(height * 0.78)),
        (int(width * 0.35), int(height * 0.88)),
        (int(width * 0.42), height),
    ]
    draw.polygon(rock_pts, fill=(18, 30, 36))

    # Palm trees on left or right
    palm_x = int(width * 0.88)
    palm_base_y = height
    # Trunk curve
    trunk_pts = []
    for step in range(12):
        t = step / 11.0
        ty = int(palm_base_y - t * (height * 0.65))
        tx = int(palm_x - math.sin(t * 1.5) * 45)
        trunk_pts.append((tx, ty))

    for i in range(len(trunk_pts) - 1):
        draw.line([trunk_pts[i], trunk_pts[i+1]], fill=(15, 24, 28), width=int(10 * (1 - i/len(trunk_pts) * 0.5)))

    top_x, top_y = trunk_pts[-1]
    # Fronds
    frond_angles = [-140, -110, -70, -30, 0, 35, 75, 115]
    for angle in frond_angles:
        rad = math.radians(angle)
        frond_len = int(min(width, height) * 0.22)
        ctrl_x = top_x + int(math.cos(rad) * frond_len * 0.6)
        ctrl_y = top_y + int(math.sin(rad) * frond_len * 0.4) - 20
        end_x = top_x + int(math.cos(rad) * frond_len)
        end_y = top_y + int(math.sin(rad) * frond_len) + 30
        draw.line([(top_x, top_y), (ctrl_x, ctrl_y), (end_x, end_y)], fill=(15, 26, 30), width=4)

    # Distant boat silhouette
    boat_x = int(width * 0.45)
    boat_y = int(horizon_y + sea_h * 0.15)
    draw.polygon([(boat_x - 14, boat_y), (boat_x + 14, boat_y), (boat_x + 9, boat_y + 4), (boat_x - 11, boat_y + 4)], fill=(15, 25, 30))
    draw.line([(boat_x, boat_y), (boat_x, boat_y - 14)], fill=(15, 25, 30), width=1)
    draw.polygon([(boat_x, boat_y - 12), (boat_x + 8, boat_y - 3), (boat_x, boat_y - 3)], fill=(240, 230, 210))

    return img

def main():
    print("Generating high-resolution atmospheric travel photos and backdrops...")

    # 1. Backdrops (1920x1080)
    bd_golden = generate_coastal_scenery(1920, 1080, "golden_hour", "Kozhikode Coast Golden Hour")
    bd_golden.save(BACKDROPS_DIR / "coastline_golden.jpg", quality=92)

    bd_azure = generate_coastal_scenery(1920, 1080, "azure_cove", "Arabian Sea Azure Cove")
    bd_azure.save(BACKDROPS_DIR / "coastline_azure.jpg", quality=92)

    bd_twilight = generate_coastal_scenery(1920, 1080, "twilight_harbor", "Beypore Twilight Harbor")
    bd_twilight.save(BACKDROPS_DIR / "coastline_twilight.jpg", quality=92)

    # 2. Photos (1200x800) for trips
    photo_themes = [
        ("kappad_beach_sunset", "golden_hour"),
        ("beypore_port_dhows", "twilight_harbor"),
        ("mananchira_heritage", "azure_cove"),
        ("kadalundi_bird_sanctuary", "lush_backwater"),
        ("kozhikode_south_beach", "golden_hour"),
        ("kozhikode_lighthouse", "twilight_harbor"),
        ("valayanad_temple_grove", "lush_backwater"),
        ("puthiyappa_harbor", "golden_hour"),
        ("sweet_meat_street", "golden_hour"),
        ("chaliyam_beach_rocks", "azure_cove"),
        ("feroke_tile_kilns", "twilight_harbor"),
        ("sulaimani_chai_stall", "golden_hour"),
        ("malabar_sunset_cliffs", "golden_hour"),
        ("coconut_lagoon_creek", "lush_backwater"),
        ("coastal_fishing_nets", "twilight_harbor"),
        ("kallayi_timber_river", "azure_cove"),
    ]

    for name, theme in photo_themes:
        img = generate_coastal_scenery(1200, 800, theme, name.replace("_", " ").title())
        img.save(PHOTOS_DIR / f"{name}.jpg", quality=88)

    print(f"Generated {len(photo_themes)} photos and 3 backdrops successfully.")

if __name__ == "__main__":
    main()
