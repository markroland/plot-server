import os
import re
from xml.etree import ElementTree as ET

import cairosvg


THUMBNAIL_SUFFIX = '-tn@2x.png'
THUMBNAIL_LONG_EDGE_PX = 480
SVG_LENGTH_UNIT_TO_PX = {
    '': 1,
    'px': 1,
    'in': 96,
    'cm': 96 / 2.54,
    'mm': 96 / 25.4,
    'pt': 96 / 72,
    'pc': 16,
}


def normalize_relative_path(path):
    return path.replace(os.sep, '/')


def build_public_upload_url(relative_path):
    return f"/static/uploads/{normalize_relative_path(relative_path)}"


def build_thumbnail_relative_path(relative_path):
    base_path, _ = os.path.splitext(relative_path)
    return normalize_relative_path(f"{base_path}{THUMBNAIL_SUFFIX}")


def build_file_entry(relative_path):
    normalized_path = normalize_relative_path(relative_path)
    return {
        'filename': normalized_path,
        'svg_url': build_public_upload_url(normalized_path),
        'thumbnail_url': build_public_upload_url(build_thumbnail_relative_path(normalized_path)),
    }


def parse_svg_length_to_px(value):
    if not value:
        return None

    length_text = value.strip()
    if not length_text or length_text.endswith('%'):
        return None

    match = re.fullmatch(r'([0-9]*\.?[0-9]+)([a-zA-Z]*)', length_text)
    if not match:
        return None

    numeric_value = float(match.group(1))
    unit = match.group(2).lower()
    unit_scale = SVG_LENGTH_UNIT_TO_PX.get(unit)
    if unit_scale is None:
        return None

    return numeric_value * unit_scale


def get_svg_dimensions_px(svg_path):
    try:
        root = ET.parse(svg_path).getroot()
    except (ET.ParseError, OSError) as error:
        print(f"[WARN] Unable to parse SVG for thumbnail sizing: {svg_path} ({error})")
        return None

    view_box = root.attrib.get('viewBox')
    if view_box:
        values = [part for part in re.split(r'[\s,]+', view_box.strip()) if part]
        if len(values) == 4:
            try:
                width = abs(float(values[2]))
                height = abs(float(values[3]))
                if width > 0 and height > 0:
                    return width, height
            except ValueError:
                pass

    width = parse_svg_length_to_px(root.attrib.get('width'))
    height = parse_svg_length_to_px(root.attrib.get('height'))
    if width and height:
        return width, height

    return None


def generate_svg_thumbnail(svg_path, thumbnail_path):
    dimensions = get_svg_dimensions_px(svg_path)
    output_kwargs = {'output_width': THUMBNAIL_LONG_EDGE_PX}

    if dimensions:
        width, height = dimensions
        if height > width:
            output_kwargs = {'output_height': THUMBNAIL_LONG_EDGE_PX}

    os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
    cairosvg.svg2png(url=svg_path, write_to=thumbnail_path, **output_kwargs)