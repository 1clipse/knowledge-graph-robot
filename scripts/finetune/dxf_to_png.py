"""DXF → PNG 渲染器，用于生成 VL 模型测试用图"""
from __future__ import annotations
import sys
from pathlib import Path
import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.config import Configuration
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import matplotlib.pyplot as plt


def dxf_to_png(dxf_path: str, png_path: str | None = None):
    dxf_path = Path(dxf_path)
    if png_path is None:
        png_path = dxf_path.with_suffix(".png")
    else:
        png_path = Path(png_path)

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    # 检查是否有内容
    all_entities = list(msp)
    print(f"{dxf_path.name}: {len(all_entities)} entities, {len(doc.layers)} layers")

    fig, ax = plt.subplots(figsize=(16, 10), dpi=150)
    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    config = Configuration(
        custom_bg_color="#FFFFFF",
        custom_fg_color="#000000",
        lineweight_scaling=0.5,
    )
    Frontend(ctx, backend, config=config).draw_layout(msp)

    ax.set_aspect("equal")
    ax.autoscale()
    ax.axis("off")
    fig.tight_layout(pad=0)
    fig.savefig(str(png_path), dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  → saved: {png_path}")
    return str(png_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python dxf_to_png.py <input.dxf> [output.png]")
        print("       python dxf_to_png.py <input_dir>   (批量转换目录下所有DXF)")
        sys.exit(1)

    target = Path(sys.argv[1])
    if target.is_dir():
        for f in sorted(target.glob("*.dxf")):
            dxf_to_png(str(f))
    else:
        dxf_to_png(str(target), sys.argv[2] if len(sys.argv) > 2 else None)
